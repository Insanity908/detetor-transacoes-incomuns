"""Páginas da API na interface: documentação (gerada da especificação OpenAPI) e gestão das chaves (administrador)."""
from __future__ import annotations

import json

from flask import Flask, abort, render_template, request
from sqlalchemy.orm import sessionmaker

from . import chaves_api
from .acesso import auditar, exige, utilizador_atual
from .openapi import especificacao

ENDPOINTS = frozenset({"api_docs", "chaves", "chaves_post", "chave_revogar"})
METODOS = ("get", "post", "put", "patch", "delete")


def _resolver(esquema: dict, componentes: dict) -> dict:
    if "$ref" in esquema:
        return componentes[esquema["$ref"].rsplit("/", 1)[1]]
    return esquema


def instalar(app: Flask, sessoes: sessionmaker) -> None:
    @app.get("/api/docs")
    def api_docs():
        espec = especificacao(request.host_url.rstrip("/"))
        componentes = espec["components"]["schemas"]
        operacoes = [{"metodo": m.upper(), "caminho": caminho, **op}
                     for caminho, item in espec["paths"].items() for m, op in item.items() if m in METODOS]
        transacao = componentes["Transacao"]
        campos = [{"nome": n, "obrigatorio": n in transacao["required"], **_resolver(d, componentes)}
                  for n, d in transacao["properties"].items()]
        exemplo = {"transacoes": [{c["nome"]: c["example"] for c in campos if "example" in c}]}
        return render_template("api_docs.html", espec=espec, operacoes=operacoes, campos=campos,
                               exemplo=json.dumps(exemplo, ensure_ascii=False, indent=2), base=request.host_url.rstrip("/"))

    def _pagina(nova=None, erro=None, valores=None, estado=200):
        return render_template("chaves.html", chaves=chaves_api.listar(sessoes), nova=nova, erro=erro,
                               valores=valores or {}, limite_padrao=chaves_api.LIMITE_PADRAO), estado

    @app.get("/chaves")
    @exige("administrador")
    def chaves():
        return _pagina()

    @app.post("/chaves")
    @exige("administrador")
    def chaves_post():
        valores = {"nome": request.form.get("nome", ""), "limite": request.form.get("limite", "")}
        limite = valores["limite"].strip()
        try:
            texto, registo = chaves_api.criar(sessoes, valores["nome"], utilizador_atual().nome_utilizador,
                                              int(limite) if limite.isdigit() else chaves_api.LIMITE_PADRAO)
        except ValueError as exc:
            return _pagina(erro=str(exc), valores=valores, estado=400)
        auditar("chave_criada", alvo=registo.prefixo, detalhe=f"{registo.nome}; {registo.limite_por_minuto}/min")
        return _pagina(nova={"chave": texto, "nome": registo.nome})

    @app.post("/chaves/<int:chave_id>/revogar")
    @exige("administrador")
    def chave_revogar(chave_id):
        if chaves_api.obter(sessoes, chave_id) is None:
            abort(404)
        try:
            registo = chaves_api.revogar(sessoes, chave_id)
        except ValueError as exc:
            return _pagina(erro=str(exc), estado=400)
        auditar("chave_revogada", alvo=registo.prefixo, detalhe=registo.nome)
        return _pagina()
