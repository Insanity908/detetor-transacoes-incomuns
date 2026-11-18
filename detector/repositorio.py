"""Gravar e ler análises na base de dados, convertendo de e para os DataFrames do serviço."""
from __future__ import annotations

import json
import secrets
from datetime import datetime

import pandas as pd
from sqlalchemy import func, insert, select
from sqlalchemy.orm import sessionmaker

from .alertas import criar_para_analise
from .banco import Analise, TransacaoAvaliada

COLUNAS = ("id_transacao", "cliente", "data_hora", "valor", "cidade", "pais", "categoria",
           "risco", "anomalia", "nivel", "motivos", "historico_curto")
ORIGENS = ("ficheiro", "formulario", "exemplo", "api")


def guardar_analise(sessoes: sessionmaker, resultado: pd.DataFrame, origem: str, modelo: str,
                    nome_ficheiro: str | None = None, utilizador_id: int | None = None,
                    gerar_alertas: bool = False, chave_api_id: int | None = None,
                    versao_modelo: int | None = None) -> str:
    """Grava o resultado pela ordem em que está e devolve a chave usada nos endereços da interface.

    Com gerar_alertas, cada transação com alerta entra na fila de alertas, na mesma transação do banco.
    """
    if origem not in ORIGENS:
        raise ValueError(f"Origem desconhecida: {origem}")
    chave = secrets.token_urlsafe(16)
    linhas = [
        {
            "ordem": i,
            "id_transacao": str(r["id_transacao"]),
            "cliente": str(r["cliente"]),
            "data_hora": pd.Timestamp(r["data_hora"]).to_pydatetime(),
            "valor": float(r["valor"]),
            "cidade": str(r["cidade"]),
            "pais": str(r["pais"]),
            "categoria": str(r["categoria"]),
            "risco": float(r["risco"]),
            "anomalia": float(r["anomalia"]),
            "nivel": str(r["nivel"]),
            "motivos": json.dumps(list(r["motivos"]), ensure_ascii=False),
            "historico_curto": bool(r.get("historico_curto", False)),
            "fraude_simulada": None if pd.isna(r.get("fraude_simulada")) else bool(r["fraude_simulada"]),
            "tipo_fraude_simulada": (str(r["tipo_fraude_simulada"])[:30] or None)
            if isinstance(r.get("tipo_fraude_simulada"), str) else None,
        }
        for i, r in enumerate(resultado.to_dict("records"))
    ]
    with sessoes.begin() as sessao:
        analise = Analise(chave=chave, origem=origem, nome_ficheiro=(nome_ficheiro or None) and nome_ficheiro[:255],
                          criada_em=datetime.now().replace(microsecond=0), total=len(linhas),
                          alertas=sum(x["nivel"] != "Normal" for x in linhas), modelo=modelo,
                          utilizador_id=utilizador_id, chave_api_id=chave_api_id, versao_modelo=versao_modelo)
        sessao.add(analise)
        sessao.flush()
        if linhas:
            sessao.execute(insert(TransacaoAvaliada), [x | {"analise_id": analise.id} for x in linhas])
        if gerar_alertas:
            criar_para_analise(sessao, analise.id, analise.criada_em)
    return chave


def obter_analise(sessoes: sessionmaker, chave: str) -> Analise | None:
    with sessoes() as sessao:
        return sessao.scalar(select(Analise).where(Analise.chave == chave))


def obter_transacoes(sessoes: sessionmaker, analise: Analise, so_alertas: bool = False,
                     limite: int | None = None) -> pd.DataFrame:
    """As transações da análise, pela ordem gravada, no mesmo formato que o serviço devolve."""
    consulta = (select(*(getattr(TransacaoAvaliada, c) for c in COLUNAS))
                .where(TransacaoAvaliada.analise_id == analise.id).order_by(TransacaoAvaliada.ordem))
    if so_alertas:
        consulta = consulta.where(TransacaoAvaliada.nivel != "Normal")
    if limite is not None:
        consulta = consulta.limit(limite)
    with sessoes() as sessao:
        linhas = [dict(zip(COLUNAS, r, strict=True)) for r in sessao.execute(consulta)]
    for linha in linhas:
        linha["motivos"] = json.loads(linha["motivos"])
    df = pd.DataFrame(linhas, columns=list(COLUNAS))
    df["data_hora"] = pd.to_datetime(df["data_hora"])
    return df


def listar_analises(sessoes: sessionmaker, origem: str | None = None, limite: int = 10,
                    utilizador_id: int | None = None) -> list[Analise]:
    """As análises mais recentes; com utilizador_id, só as feitas por esse utilizador."""
    consulta = select(Analise).order_by(Analise.criada_em.desc(), Analise.id.desc()).limit(limite)
    if origem is not None:
        consulta = consulta.where(Analise.origem == origem)
    if utilizador_id is not None:
        consulta = consulta.where(Analise.utilizador_id == utilizador_id)
    with sessoes() as sessao:
        return list(sessao.scalars(consulta))


def contar_analises(sessoes: sessionmaker) -> int:
    with sessoes() as sessao:
        return sessao.scalar(select(func.count(Analise.id))) or 0


# ------------------------------------------------------------ transações recebidas pela API
def historico_api(sessoes: sessionmaker, clientes: set[str], desde: datetime) -> pd.DataFrame:
    """As transações destes clientes recebidas pela API desde `desde`, no formato do histórico do treino.

    A coluna fraude vem das decisões dos analistas: uma fraude confirmada não entra no "habitual" do cliente.
    """
    from .banco import Alerta
    colunas = ("id_transacao", "cliente", "data_hora", "valor", "cidade", "pais", "categoria")
    consulta = (select(*(getattr(TransacaoAvaliada, c) for c in colunas), Alerta.estado)
                .join(Analise, Analise.id == TransacaoAvaliada.analise_id)
                .outerjoin(Alerta, Alerta.transacao_id == TransacaoAvaliada.id)
                .where(Analise.origem == "api", TransacaoAvaliada.cliente.in_(sorted(clientes)),
                       TransacaoAvaliada.data_hora >= desde)
                .order_by(TransacaoAvaliada.data_hora, TransacaoAvaliada.id))
    with sessoes() as sessao:
        linhas = sessao.execute(consulta).all()
    df = pd.DataFrame([dict(zip(colunas, x[:-1], strict=True)) | {"fraude": int(x[-1] == "fraude")} for x in linhas],
                      columns=[*colunas, "fraude"])
    df["data_hora"] = pd.to_datetime(df["data_hora"])
    return df


def resultados_api(sessoes: sessionmaker, ids: list[str]) -> dict[str, dict]:
    """O resultado já gravado de cada id_transacao recebido antes pela API (para não avaliar duas vezes)."""
    from .banco import Alerta
    if not ids:
        return {}
    consulta = (select(TransacaoAvaliada, Alerta.id, Alerta.estado)
                .join(Analise, Analise.id == TransacaoAvaliada.analise_id)
                .outerjoin(Alerta, Alerta.transacao_id == TransacaoAvaliada.id)
                .where(Analise.origem == "api", TransacaoAvaliada.id_transacao.in_(sorted(set(ids))))
                .order_by(TransacaoAvaliada.id))
    resultado = {}
    with sessoes() as sessao:
        for t, alerta_id, estado in sessao.execute(consulta):
            resultado.setdefault(t.id_transacao, {
                "id_transacao": t.id_transacao, "cliente": t.cliente, "data_hora": t.data_hora, "valor": t.valor,
                "risco": t.risco, "anomalia": t.anomalia, "nivel": t.nivel, "motivos": json.loads(t.motivos),
                "alerta_id": alerta_id, "estado_alerta": estado})
    return resultado


def ultima_data_api(sessoes: sessionmaker) -> datetime | None:
    """A data_hora mais recente recebida pela API (o simulador continua a partir daqui)."""
    with sessoes() as sessao:
        return sessao.scalar(select(func.max(TransacaoAvaliada.data_hora))
                             .join(Analise, Analise.id == TransacaoAvaliada.analise_id).where(Analise.origem == "api"))
