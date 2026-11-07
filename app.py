"""Interface web do detetor de transações incomuns.

Execução, na raiz do projeto, depois de `python treinar.py`:
    python app.py
e abra http://127.0.0.1:5000
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import pandas as pd
from flask import Flask, render_template, request

from detector.artefatos import carregar
from detector.entrada import MAX_BYTES, validar
from detector.exemplos import DESCRICOES, NOMES, criar_exemplos
from detector.explicacao import formatar_decimal, formatar_mt, nome_categoria
from detector.gerador import CATEGORIAS, CIDADES, PAISES_ESTRANHOS, PAIS_HABITUAL
from detector.servico import avaliar_transacoes

RAIZ = Path(__file__).parent
CAMPOS = ("cliente", "data_hora", "valor", "cidade", "pais", "categoria")


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
            exemplos=[(n, DESCRICOES[n]) for n in NOMES],
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

    return app


app = criar_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
