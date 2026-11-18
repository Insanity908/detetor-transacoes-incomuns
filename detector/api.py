"""API REST v1: receber transações de outros sistemas (por exemplo, o simulador), avaliá-las e pôr os alertas na fila.

Autenticação por chave (cabeçalho `Authorization: Bearer dtf_...` ou `X-Chave-API`), nunca por sessão:
por isso a API não usa cookies nem precisa de token CSRF. Cada chave tem um limite de pedidos por minuto.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta

import pandas as pd
from flask import Blueprint, Flask, current_app, g, jsonify, request
from sqlalchemy.orm import sessionmaker

from . import chaves_api, repositorio
from .simulacao import TIPOS_SIMULACAO
from .servico import avaliar_transacoes

VERSAO_API = "1.0.0"
MAX_LOTE = 500  # o mesmo valor está em openapi.py
DIAS_HISTORICO = 30  # transações recebidas antes pela API que contam como passado do cliente
FUTURO_MAXIMO = timedelta(days=1)
DATA_MINIMA = datetime(2000, 1, 1)
CAMPOS = ("id_transacao", "cliente", "data_hora", "valor", "cidade", "pais", "categoria")
OPCIONAIS = ("simulacao",)
PADRAO_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,100}$")
PADRAO_PAIS = re.compile(r"^[A-Za-z]{2,3}$")
CONTROLO = re.compile(r"[\x00-\x1f\x7f]")
registo = logging.getLogger("detetor")
bp = Blueprint("api", __name__, url_prefix="/api/v1")


class ErroValidacao(ValueError):
    def __init__(self, erros: list[dict]):
        super().__init__("; ".join(f"{e.get('indice', '')}{e['campo']}: {e['erro']}" for e in erros))
        self.erros = erros


def erro(estado: int, codigo: str, mensagem: str, **extra):
    resposta = jsonify({"erro": codigo, "mensagem": mensagem, **extra})
    resposta.status_code = estado
    return resposta


# ------------------------------------------------------------ validação
def _texto(valor, campo: str, maximo: int, erros: list, indice: int) -> str | None:
    if not isinstance(valor, str) or not valor.strip():
        erros.append({"indice": indice, "campo": campo, "erro": "tem de ser um texto não vazio"})
        return None
    valor = valor.strip()
    if len(valor) > maximo or CONTROLO.search(valor):
        erros.append({"indice": indice, "campo": campo,
                      "erro": f"até {maximo} caracteres, sem caracteres de controlo"})
        return None
    return valor


def _data_hora(valor, erros: list, indice: int, agora: datetime) -> datetime | None:
    try:
        quando = datetime.fromisoformat(valor) if isinstance(valor, str) else None
    except ValueError:
        quando = None
    if quando is None:
        erros.append({"indice": indice, "campo": "data_hora",
                      "erro": "use ISO 8601, por exemplo 2026-05-01T14:30:00 ou 2026-05-01T14:30:00+02:00"})
        return None
    if quando.tzinfo is not None:  # o histórico usa a hora local, sem fuso
        quando = quando.astimezone().replace(tzinfo=None)
    if not DATA_MINIMA <= quando <= agora + FUTURO_MAXIMO:
        erros.append({"indice": indice, "campo": "data_hora",
                      "erro": "fora do intervalo aceite (desde 2000 e no máximo um dia no futuro)"})
        return None
    return quando.replace(microsecond=0)


def validar_pedido(corpo, agora: datetime | None = None) -> pd.DataFrame:
    """Converte o JSON num DataFrame pronto a avaliar, ou levanta ErroValidacao com todos os problemas."""
    agora = agora or datetime.now()
    if not isinstance(corpo, dict):
        raise ErroValidacao([{"campo": "corpo", "erro": "tem de ser um objeto JSON"}])
    if "transacoes" in corpo:
        extra = set(corpo) - {"transacoes"}
        if extra:
            raise ErroValidacao([{"campo": c, "erro": "campo desconhecido"} for c in sorted(extra)])
        lista = corpo["transacoes"]
        if not isinstance(lista, list) or not 1 <= len(lista) <= MAX_LOTE:
            raise ErroValidacao([{"campo": "transacoes", "erro": f"tem de ser uma lista com 1 a {MAX_LOTE} transações"}])
    else:
        lista = [corpo]
    erros, linhas, vistos = [], [], set()
    for i, item in enumerate(lista):
        if not isinstance(item, dict):
            erros.append({"indice": i, "campo": "transacao", "erro": "tem de ser um objeto"})
            continue
        for campo in sorted(set(item) - set(CAMPOS) - set(OPCIONAIS)):
            erros.append({"indice": i, "campo": campo, "erro": "campo desconhecido"})
        for campo in CAMPOS:
            if campo not in item:
                erros.append({"indice": i, "campo": campo, "erro": "obrigatório"})
        if any(c not in item for c in CAMPOS):
            continue
        n_erros = len(erros)
        id_transacao = item["id_transacao"]
        if not isinstance(id_transacao, str) or not PADRAO_ID.match(id_transacao):
            erros.append({"indice": i, "campo": "id_transacao",
                          "erro": "1 a 100 caracteres: letras, números, '.', '_', ':' ou '-'"})
        elif id_transacao in vistos:
            erros.append({"indice": i, "campo": "id_transacao", "erro": "repetido neste pedido"})
        vistos.add(id_transacao if isinstance(id_transacao, str) else None)
        cliente = _texto(item["cliente"], "cliente", 100, erros, i)
        cidade = _texto(item["cidade"], "cidade", 100, erros, i)
        categoria = _texto(item["categoria"], "categoria", 50, erros, i)
        pais = item["pais"]
        if not isinstance(pais, str) or not PADRAO_PAIS.match(pais.strip()):
            erros.append({"indice": i, "campo": "pais", "erro": "código de 2 ou 3 letras, como MZ ou ZAF"})
        valor = item["valor"]
        if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor) \
                or not 0 < valor <= 1e9:
            erros.append({"indice": i, "campo": "valor", "erro": "número maior que zero e até 1 000 000 000"})
        quando = _data_hora(item["data_hora"], erros, i, agora)
        simulacao = item.get("simulacao")
        fraude_simulada = tipo_simulado = None
        if simulacao is not None:
            if (not isinstance(simulacao, dict) or set(simulacao) - {"fraude", "tipo"}
                    or not isinstance(simulacao.get("fraude"), bool)
                    or simulacao.get("tipo", "") not in ("", *TIPOS_SIMULACAO)):
                erros.append({"indice": i, "campo": "simulacao",
                              "erro": f"objeto {{fraude: true|false, tipo: um de {', '.join(TIPOS_SIMULACAO)}}}"})
            else:
                fraude_simulada, tipo_simulado = simulacao["fraude"], simulacao.get("tipo") or None
        if len(erros) == n_erros:
            linhas.append({"id_transacao": id_transacao, "cliente": cliente, "data_hora": quando,
                           "valor": float(valor), "cidade": cidade, "pais": pais.strip().upper(),
                           "categoria": categoria, "fraude_simulada": fraude_simulada,
                           "tipo_fraude_simulada": tipo_simulado})
    if erros:
        raise ErroValidacao(erros)
    df = pd.DataFrame(linhas)
    df["data_hora"] = pd.to_datetime(df["data_hora"])
    return df


# ------------------------------------------------------------ resultado
def _resultado(r: dict, duplicada: bool) -> dict:
    return {
        "id_transacao": r["id_transacao"],
        "risco": round(float(r["risco"]), 1),
        "anomalia": round(float(r["anomalia"]), 1),
        "nivel": r["nivel"],
        "alerta": r["nivel"] != "Normal",
        "motivos": list(r["motivos"]),
        "alerta_id": r.get("alerta_id"),
        "estado_alerta": r.get("estado_alerta"),
        "duplicada": duplicada,
    }


# ------------------------------------------------------------ rotas
@bp.before_request
def _autenticar():
    if request.endpoint == "api.especificacao":
        return None
    sessoes = current_app.config["SESSOES"]
    cabecalho = request.headers.get("Authorization", "")
    texto = cabecalho[7:].strip() if cabecalho.lower().startswith("bearer ") else request.headers.get("X-Chave-API")
    chave = chaves_api.autenticar(sessoes, texto)
    if chave is None:
        registo.warning("API: pedido %s %s sem chave válida (IP %s)", request.method, request.path, request.remote_addr)
        resposta = erro(401, "nao_autenticado", "Envie uma chave válida em 'Authorization: Bearer <chave>'.")
        resposta.headers["WWW-Authenticate"] = 'Bearer realm="detetor"'
        return resposta
    permitido, esperar = current_app.config["LIMITADOR"].permitir(chave.id, chave.limite_por_minuto)
    if not permitido:
        resposta = erro(429, "limite_excedido",
                        f"Limite de {chave.limite_por_minuto} pedidos por minuto atingido para esta chave.",
                        tentar_de_novo_em=esperar)
        resposta.headers["Retry-After"] = str(esperar)
        return resposta
    g.chave_api = chave
    if current_app.config["ARTEFATOS"] is None:
        return erro(503, "sem_modelos", "Os modelos ainda não foram treinados neste servidor.")
    return None


@bp.post("/transacoes")
def receber_transacoes():
    sessoes: sessionmaker = current_app.config["SESSOES"]
    artefatos = current_app.config["ARTEFATOS"]
    if not request.is_json:
        return erro(415, "tipo_invalido", "Envie o corpo em JSON (Content-Type: application/json).")
    corpo = request.get_json(silent=True)
    if corpo is None:
        return erro(400, "json_invalido", "O corpo não é JSON válido.")
    try:
        transacoes = validar_pedido(corpo)
    except ErroValidacao as exc:
        return erro(422, "dados_invalidos", "Há transações com dados inválidos; nada foi gravado.", erros=exc.erros)

    anteriores = repositorio.resultados_api(sessoes, list(transacoes["id_transacao"]))
    novas = transacoes[~transacoes["id_transacao"].isin(anteriores)].reset_index(drop=True)
    if len(novas):
        clientes = set(novas["cliente"].str.strip().str.upper())
        desde = novas["data_hora"].min().to_pydatetime() - timedelta(days=DIAS_HISTORICO)
        historico = repositorio.historico_api(sessoes, clientes, desde)
        resultado = avaliar_transacoes(artefatos, novas, historico_extra=historico,
                                       cortes=current_app.config.get("CORTES"))
        repositorio.guardar_analise(sessoes, resultado, "api", artefatos.detector.escolhido,
                                    gerar_alertas=True, chave_api_id=g.chave_api.id, versao_modelo=artefatos.versao)
    gravados = repositorio.resultados_api(sessoes, list(novas["id_transacao"]))
    resultados = [_resultado(anteriores[i], True) if i in anteriores else _resultado(gravados[i], False)
                  for i in transacoes["id_transacao"]]
    chaves_api.registar_uso(sessoes, g.chave_api.id, len(novas))
    n_alertas = sum(r["alerta"] and not r["duplicada"] for r in resultados)
    if len(novas):
        registo.info("API (%s): %d transação(ões) avaliada(s), %d com alerta, %d repetida(s)",
                     g.chave_api.nome, len(novas), n_alertas, len(anteriores))
    return jsonify({"recebidas": len(transacoes), "avaliadas": len(novas), "duplicadas": len(anteriores),
                    "alertas": n_alertas, "resultados": resultados})


@bp.get("/transacoes/<id_transacao>")
def consultar_transacao(id_transacao):
    if not PADRAO_ID.match(id_transacao):
        return erro(404, "nao_encontrada", "Transação não encontrada.")
    encontrado = repositorio.resultados_api(current_app.config["SESSOES"], [id_transacao])
    if id_transacao not in encontrado:
        return erro(404, "nao_encontrada", "Transação não encontrada.")
    r = encontrado[id_transacao]
    return jsonify(_resultado(r, False) | {"cliente": r["cliente"], "valor": r["valor"],
                                            "data_hora": r["data_hora"].isoformat()})


@bp.get("/estado")
def estado():
    sessoes = current_app.config["SESSOES"]
    artefatos = current_app.config["ARTEFATOS"]
    fim_treino = artefatos.historico["data_hora"].max()
    ultima = repositorio.ultima_data_api(sessoes)
    return jsonify({
        "versao_api": VERSAO_API,
        "chave": g.chave_api.nome,
        "limite_por_minuto": g.chave_api.limite_por_minuto,
        "modelo": artefatos.detector.escolhido,
        "fim_do_historico_de_treino": pd.Timestamp(fim_treino).isoformat(),
        "ultima_transacao_recebida": ultima.isoformat() if ultima else None,
        "max_transacoes_por_pedido": MAX_LOTE,
    })


@bp.get("/openapi.json")
def especificacao():
    from .openapi import especificacao as gerar
    return jsonify(gerar(request.host_url.rstrip("/")))


def instalar(app: Flask) -> None:
    app.config.setdefault("LIMITADOR", chaves_api.LimitadorPedidos())
    app.register_blueprint(bp)


def e_pedido_api() -> bool:
    return request.path.startswith(bp.url_prefix + "/") or request.path == bp.url_prefix
