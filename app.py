"""Interface web do detetor de transações incomuns.

Execução, na raiz do projeto, depois de `python treinar.py`:
    python app.py
e abra http://127.0.0.1:5000
"""
from __future__ import annotations

import csv
import io
import secrets
from collections import OrderedDict
from pathlib import Path

import pandas as pd
from flask import Flask, Response, abort, render_template, request, send_from_directory

from detector.artefatos import carregar
from detector.entrada import MAX_BYTES, ler_csv, validar
from detector.exemplos import DESCRICOES, criar_exemplos
from detector.exemplos import NOMES as NOMES_EXEMPLOS
from detector.explicacao import formatar_decimal, formatar_mt, nome_categoria
from detector.avaliacao import NOMES_PERIODO
from detector.gerador import CATEGORIAS, CIDADES, NOMES_TIPO_FRAUDE, PAISES_ESTRANHOS, PAIS_HABITUAL
from detector.modelos import NOMES, ORDEM_SIMPLICIDADE
from detector.perfis import perfil_cliente
from detector.servico import avaliar_transacoes

RAIZ = Path(__file__).parent
CAMPOS = ("cliente", "data_hora", "valor", "cidade", "pais", "categoria")
MAX_LINHAS_TABELA = 500
MAX_RESULTADOS = 20
GRAFICOS = ("matriz_confusao.png", "importancia.png", "comparacao.png")
INICIO_FORMULA = ("=", "+", "-", "@")


def _neutralizar(texto: str) -> str:
    """Impede que um texto do ficheiro vire fórmula ao abrir o CSV no Excel."""
    return "'" + texto if texto.lstrip().startswith(INICIO_FORMULA) else texto


def _csv_excel(resultado: pd.DataFrame) -> bytes:
    """CSV no formato do Excel em português: `;`, vírgula decimal e UTF-8 com BOM."""
    buffer = io.StringIO()
    escrita = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    escrita.writerow(["id_transacao", "cliente", "data_hora", "valor", "cidade", "pais", "categoria",
                      "risco", "anomalia", "nivel", "motivos"])
    numero = lambda v: f"{v:.2f}".replace(".", ",")
    for r in resultado.to_dict("records"):
        escrita.writerow([_neutralizar(str(r["id_transacao"])), _neutralizar(str(r["cliente"])),
                          r["data_hora"].strftime("%Y-%m-%d %H:%M:%S"), numero(r["valor"]),
                          _neutralizar(str(r["cidade"])), _neutralizar(str(r["pais"])),
                          _neutralizar(str(r["categoria"])), numero(r["risco"]), numero(r["anomalia"]),
                          r["nivel"], _neutralizar(" | ".join(r["motivos"]))])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def criar_app(pasta_modelos: Path = RAIZ / "modelos") -> Flask:
    app = Flask(__name__, template_folder=str(RAIZ / "templates"), static_folder=str(RAIZ / "static"))
    app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES + 64 * 1024
    app.config["ARTEFATOS"] = artefatos = carregar(Path(pasta_modelos))
    app.config["RESULTADOS"] = OrderedDict()  # downloads em memória; nada é gravado em disco
    exemplos = criar_exemplos(artefatos.historico) if artefatos else {}
    clientes = sorted(artefatos.historico["cliente"].unique()) if artefatos else []

    @app.template_filter("mt")
    def _mt(valor):
        return formatar_mt(float(valor))

    @app.template_filter("decimal")
    def _decimal(valor, casas=1):
        return formatar_decimal(float(valor), casas)

    @app.template_filter("pct")
    def _pct(valor):
        return "—" if valor is None else f"{formatar_decimal(100 * float(valor))}%"

    @app.template_filter("categoria")
    def _categoria(valor):
        return nome_categoria(str(valor).strip().upper())

    @app.context_processor
    def _contexto():
        return {"aba": request.endpoint}

    @app.before_request
    def _exige_modelos():
        if app.config["ARTEFATOS"] is None and request.endpoint != "static":
            return render_template("sem_modelos.html"), 503
        return None

    def _formulario(erro=None, valores=None, linhas=None, estado=200):
        return render_template(
            "testar.html", erro=erro, valores=valores or {}, linhas=linhas, clientes=clientes,
            cidades=CIDADES, paises=(PAIS_HABITUAL, "ZA", *PAISES_ESTRANHOS), categorias=CATEGORIAS,
            exemplos=[(n, DESCRICOES[n]) for n in NOMES_EXEMPLOS],
        ), estado

    @app.get("/")
    def testar():
        return _formulario()

    @app.post("/")
    def testar_post():
        nome = request.form.get("exemplo")
        valores = {c: request.form.get(c, "").strip() for c in CAMPOS}
        if nome:
            if nome not in exemplos:
                return _formulario(erro="Exemplo desconhecido.", estado=400)
            transacoes = pd.DataFrame(exemplos[nome])
            valores = {}
        else:
            try:
                transacoes = validar(pd.DataFrame([{"id_transacao": "FORM-1", **valores}]))
            except ValueError as exc:
                mensagem = str(exc).removeprefix("Linha 2: ")
                return _formulario(erro=mensagem[0].upper() + mensagem[1:], valores=valores, estado=400)
        resultado = avaliar_transacoes(app.config["ARTEFATOS"], transacoes)
        return _formulario(valores=valores, linhas=resultado.to_dict("records"))

    def _analisar(erro=None, estado=200, chave=None, so_alertas=False):
        resultado = app.config["RESULTADOS"].get(chave) if chave else None
        linhas, total, alertas = [], 0, 0
        if resultado is not None:
            total, alertas = len(resultado), int((resultado["nivel"] != "Normal").sum())
            visiveis = resultado[resultado["nivel"] != "Normal"] if so_alertas else resultado
            linhas = visiveis.head(MAX_LINHAS_TABELA).to_dict("records")
        return render_template("analisar.html", erro=erro, chave=chave if resultado is not None else None,
                               linhas=linhas, total=total, alertas=alertas, so_alertas=so_alertas,
                               limite=MAX_LINHAS_TABELA), estado

    @app.get("/analisar")
    def analisar():
        return _analisar()

    @app.post("/analisar")
    def analisar_post():
        ficheiro = request.files.get("ficheiro")
        if ficheiro is None or not ficheiro.filename:
            return _analisar(erro="Escolha um ficheiro CSV para enviar.", estado=400)
        try:
            transacoes = validar(ler_csv(ficheiro.read()))
        except ValueError as exc:
            return _analisar(erro=str(exc), estado=400)
        resultado = avaliar_transacoes(app.config["ARTEFATOS"], transacoes)
        resultado = resultado.sort_values("risco", ascending=False, kind="stable").reset_index(drop=True)
        guardados = app.config["RESULTADOS"]
        chave = secrets.token_urlsafe(16)
        guardados[chave] = resultado
        while len(guardados) > MAX_RESULTADOS:
            guardados.popitem(last=False)
        return _analisar(chave=chave)

    @app.get("/analisar/<chave>.csv")
    def analisar_download(chave):
        resultado = app.config["RESULTADOS"].get(chave)
        if resultado is None:
            abort(404)
        return Response(_csv_excel(resultado), mimetype="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="resultado.csv"'})

    @app.get("/analisar/<chave>")
    def analisar_ver(chave):
        if chave not in app.config["RESULTADOS"]:
            abort(404)
        return _analisar(chave=chave, so_alertas=request.args.get("alertas") == "1")

    @app.errorhandler(413)
    def _grande_demais(_):
        return _analisar(erro="O ficheiro passa de 5 MB.", estado=413)

    @app.get("/perfil")
    def perfil():
        codigo = request.args.get("cliente", "").strip()
        dados = {"clientes": clientes, "codigo": codigo, "perfil": None, "marcadas": []}
        if not codigo:
            return render_template("perfil.html", **dados)
        art = app.config["ARTEFATOS"]
        dados["perfil"] = perfil_cliente(art.historico, codigo)
        if dados["perfil"] is None:
            return render_template("perfil.html", **dados, erro="Cliente não encontrado."), 404
        teste = art.teste_avaliado
        if len(teste):
            marcadas = teste[(teste["cliente"].astype(str).str.strip() == codigo) & (teste["nivel"] != "Normal")]
            dados["marcadas"] = marcadas.sort_values("data_hora").to_dict("records")
        dados["maximo_hora"] = max(dados["perfil"]["horas"]) or 1
        return render_template("perfil.html", **dados)

    @app.get("/modelos")
    def modelos():
        return render_template("modelos.html", res=app.config["ARTEFATOS"].resultados, nomes=NOMES,
                               ordem=[*ORDEM_SIMPLICIDADE, "isolation_forest"], periodos=NOMES_PERIODO,
                               tipos=NOMES_TIPO_FRAUDE)

    @app.get("/graficos/<nome>")
    def graficos(nome):
        if nome not in GRAFICOS:
            abort(404)
        return send_from_directory(app.config["ARTEFATOS"].pasta / "graficos", nome)

    return app


app = criar_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
