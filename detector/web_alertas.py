"""Páginas da gestão de alertas: fila, ficha do alerta (atribuição, decisão, comentários, histórico),
painel de indicadores (supervisor) e notificações (o sino no cabeçalho).
"""
from __future__ import annotations

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy.orm import sessionmaker

from . import alertas
from .acesso import auditar, exige, utilizador_atual
from .banco import ESTADOS_ABERTOS

ENDPOINTS = frozenset({"fila_alertas", "alerta", "alerta_acao", "painel", "ver_notificacoes"})
ACOES = ("assumir", "atribuir", "devolver", "decidir", "reabrir", "comentar")
PERIODOS = (7, 30, 90)


def _inteiro(texto: str | None, padrao: int | None = None) -> int | None:
    return int(texto) if texto and texto.isdigit() else padrao


def instalar(app: Flask, sessoes: sessionmaker) -> None:
    @app.context_processor
    def _contexto_alertas():
        u = utilizador_atual()
        novas = alertas.contar_notificacoes(sessoes, u) if u is not None and not u.trocar_senha else 0
        return {"notificacoes_novas": novas, "nomes_estado": alertas.NOMES_ESTADO}

    @app.get("/alertas")
    def fila_alertas():
        u = utilizador_atual()
        a = request.args
        vista = a.get("vista") if a.get("vista") in alertas.VISTAS else "abertos"
        filtro = alertas.Filtro(vista=vista, nivel=a.get("nivel", "") if a.get("nivel") in alertas.NIVEIS else "",
                                cliente=a.get("cliente", "").strip()[:100],
                                responsavel=_inteiro(a.get("responsavel")) if alertas.ve_tudo(u) else None)
        pagina = _inteiro(a.get("pagina"), 1) or 1
        lista, total = alertas.listar(sessoes, u, filtro, pagina)
        consulta = {k: v for k, v in a.items() if k != "pagina" and v}
        return render_template(
            "alertas.html", alertas=lista, total=total, filtro=filtro, pagina=pagina,
            paginas=max(1, -(-total // alertas.POR_PAGINA)), vistas=alertas.VISTAS,
            contagens=alertas.contar_por_vista(sessoes, u), niveis=alertas.NIVEIS, consulta=consulta,
            ve_tudo=alertas.ve_tudo(u),
            responsaveis=alertas.responsaveis_possiveis(sessoes) if alertas.ve_tudo(u) else [])

    def _alerta_visivel(alerta_id: int):
        alerta = alertas.obter(sessoes, alerta_id)
        if alerta is None or not alertas.pode_ver(alerta, utilizador_atual()):
            abort(404)  # o analista não fica a saber que existe um alerta de outra pessoa com este número
        return alerta

    @app.get("/alertas/<int:alerta_id>")
    def alerta(alerta_id):
        u = utilizador_atual()
        a = _alerta_visivel(alerta_id)
        rascunho = session.pop("rascunho_alerta", None)  # texto de um pedido recusado, para não se perder
        rascunho = rascunho["texto"] if isinstance(rascunho, dict) and rascunho.get("id") == a.id else ""
        auditar("consultar_alerta", alvo=str(a.id),
                detalhe=f"{alertas.NOMES_ESTADO[a.estado]}; cliente {a.transacao.cliente}")
        return render_template(
            "alerta.html", a=a, t=a.transacao, historico=alertas.historico(sessoes, a.id),
            outros=alertas.outros_do_cliente(sessoes, a, u), pode_trabalhar=alertas.pode_trabalhar(a, u),
            ve_tudo=alertas.ve_tudo(u), abertos=ESTADOS_ABERTOS, rascunho=rascunho,
            responsaveis=alertas.responsaveis_possiveis(sessoes) if alertas.ve_tudo(u) else [])

    @app.post("/alertas/<int:alerta_id>/<acao>")
    def alerta_acao(alerta_id, acao):
        if acao not in ACOES:
            abort(404)
        u = utilizador_atual()
        a = _alerta_visivel(alerta_id)
        texto = request.form.get("texto", "")
        try:
            if acao == "assumir":
                alertas.assumir(sessoes, a.id, u)
                auditar("assumir_alerta", alvo=str(a.id))
                flash("O alerta está agora consigo.", "sucesso")
            elif acao == "atribuir":
                alvo = alertas.atribuir(sessoes, a.id, _inteiro(request.form.get("responsavel"), 0), u)
                auditar("atribuir_alerta", alvo=str(a.id), detalhe=f"para {alvo.nome_utilizador}")
                flash(f"Alerta atribuído a {alvo.nome}.", "sucesso")
            elif acao == "devolver":
                alertas.devolver(sessoes, a.id, u, texto)
                auditar("devolver_alerta", alvo=str(a.id))
                flash("O alerta voltou à fila como novo.", "sucesso")
            elif acao == "decidir":
                decisao = request.form.get("decisao", "")
                alertas.decidir(sessoes, a.id, decisao, texto, u)
                auditar("decidir_alerta", alvo=str(a.id), detalhe=alertas.NOMES_ESTADO.get(decisao))
                flash(f"Alerta encerrado: {alertas.NOMES_ESTADO[decisao].lower()}.", "sucesso")
            elif acao == "reabrir":
                alertas.reabrir(sessoes, a.id, texto, u)
                auditar("reabrir_alerta", alvo=str(a.id))
                flash("Alerta reaberto.", "sucesso")
            else:
                alertas.comentar(sessoes, a.id, texto, u)
                auditar("comentar_alerta", alvo=str(a.id))
                flash("Comentário gravado.", "sucesso")
        except PermissionError as exc:
            auditar("acesso_negado", alvo=request.path, detalhe=str(exc))
            flash(str(exc), "erro")
        except ValueError as exc:  # inclui AlertaMudou
            flash(str(exc), "erro")
            if texto.strip():
                session["rascunho_alerta"] = {"id": a.id, "texto": texto[:alertas.MAX_TEXTO]}
        return redirect(url_for("alerta", alerta_id=a.id))

    @app.get("/painel")
    @exige("supervisor")
    def painel():
        dias = _inteiro(request.args.get("dias"), 30)
        dias = dias if dias in PERIODOS else 30
        ind = alertas.indicadores(sessoes, dias)
        maximo = max((d["total"] for d in ind.por_dia), default=0)
        return render_template("painel.html", ind=ind, periodos=PERIODOS, maximo=maximo or 1)

    @app.get("/notificacoes")
    def ver_notificacoes():
        u = utilizador_atual()
        lista = alertas.notificacoes(sessoes, u)
        u.ultimo_alerta_visto = alertas.marcar_vistas(sessoes, u)  # o sino desta página já aparece a zero
        return render_template("notificacoes.html", lista=lista)
