"""Páginas do balcão: lista e pesquisa de clientes, abertura de ficha e conta, edição, depósitos e bloqueio."""
from __future__ import annotations

from datetime import datetime

from flask import Flask, abort, current_app, flash, redirect, render_template, request, url_for
from markupsafe import Markup
from sqlalchemy.orm import sessionmaker

from . import acesso, acesso_cliente, clientes, contas_bancarias
from .autorizacao import PedidoDebito, autorizar
from .acesso import auditar, exige_area, utilizador_atual
from .dinheiro import de_centavos, para_centavos
from .explicacao import formatar_decimal
from .gerador import CATEGORIA_LEVANTAMENTO, CIDADES

ENDPOINTS = frozenset({"balcao_clientes", "balcao_cliente_novo", "balcao_cliente_novo_post", "balcao_cliente",
                       "balcao_cliente_editar", "balcao_cliente_deposito", "balcao_cliente_bloquear",
                       "balcao_cliente_levantamento", "balcao_cliente_acesso"})
acesso.ENDPOINTS_BALCAO.update(ENDPOINTS)
NOMES_TIPO = {"deposito": "Depósito", "levantamento": "Levantamento", "compra": "Compra",
              "transferencia_enviada": "Transferência enviada", "transferencia_recebida": "Transferência recebida"}
NOMES_ESTADO = {"autorizado": "Autorizado", "recusado": "Recusado", "retido": "Retido", "cancelado": "Cancelado"}
CAMPOS_FICHA = ("nome", "data_nascimento", "tipo_documento", "numero_documento", "telefone", "email", "cidade", "morada")


def instalar(app: Flask, sessoes: sessionmaker) -> None:
    @app.template_filter("mtc")
    def _mtc(centavos):
        """Cêntimos para '1.500,00 MT' (sempre com duas casas, como num extrato)."""
        return f"{formatar_decimal(de_centavos(int(centavos)), 2)} MT"

    @app.context_processor
    def _contexto_balcao():
        return {"cidades": CIDADES, "nomes_tipo": NOMES_TIPO, "nomes_estado_mov": NOMES_ESTADO}

    def _cliente(cliente_id: int):
        c = clientes.obter(sessoes, cliente_id)
        if c is None:
            abort(404)
        return c

    def _ficha(c, erro=None, estado=200):
        return render_template("balcao_cliente.html", c=c, conta=c.conta, erro=erro,
                               extrato=contas_bancarias.extrato(sessoes, c.conta.id),
                               tem_acesso=acesso_cliente.obter_acesso(sessoes, c.id) is not None), estado

    @app.get("/balcao/clientes")
    @exige_area("balcao")
    def balcao_clientes():
        q = request.args.get("q", "").strip()[:100]
        return render_template("balcao_clientes.html", q=q, lista=clientes.pesquisar(sessoes, q))

    @app.get("/balcao/clientes/novo")
    @exige_area("balcao")
    def balcao_cliente_novo():
        return render_template("balcao_cliente_novo.html", valores={}, erro=None)

    @app.post("/balcao/clientes/novo")
    @exige_area("balcao")
    def balcao_cliente_novo_post():
        valores = {c: request.form.get(c, "").strip() for c in CAMPOS_FICHA}
        u = utilizador_atual()
        try:
            c = clientes.criar(sessoes, clientes.validar(valores), por=u.nome_utilizador)
        except ValueError as exc:
            return render_template("balcao_cliente_novo.html", valores=valores, erro=str(exc)), 400
        auditar("cliente_criado", alvo=c.codigo, detalhe=f"{c.tipo_documento} {c.numero_documento}")
        auditar("conta_aberta", alvo=c.codigo, detalhe=f"conta {c.conta.numero}")
        flash("Cliente registado e conta aberta.", "sucesso")
        return redirect(url_for("balcao_cliente", cliente_id=c.id))

    @app.get("/balcao/clientes/<int:cliente_id>")
    @exige_area("balcao")
    def balcao_cliente(cliente_id):
        return _ficha(_cliente(cliente_id))

    @app.post("/balcao/clientes/<int:cliente_id>/editar")
    @exige_area("balcao")
    def balcao_cliente_editar(cliente_id):
        c = _cliente(cliente_id)
        try:
            mudancas = clientes.editar(sessoes, c.id, request.form.get("telefone", ""), request.form.get("email", ""),
                                       request.form.get("morada", ""), request.form.get("cidade", ""),
                                       utilizador_atual().nome_utilizador)
        except ValueError as exc:
            return _ficha(c, str(exc), 400)
        if mudancas:
            auditar("cliente_alterado", alvo=c.codigo, detalhe="; ".join(mudancas))
        flash("Alterações gravadas." if mudancas else "Nada mudou.", "sucesso")
        return redirect(url_for("balcao_cliente", cliente_id=c.id))

    @app.post("/balcao/clientes/<int:cliente_id>/deposito")
    @exige_area("balcao")
    def balcao_cliente_deposito(cliente_id):
        c = _cliente(cliente_id)
        descricao = " ".join(request.form.get("descricao", "").split())[:140] or None
        try:
            valor = para_centavos(request.form.get("valor", ""))
            contas_bancarias.depositar(sessoes, c.conta.id, valor, "balcao", utilizador_atual().nome_utilizador,
                                       descricao=descricao)
        except ValueError as exc:
            return _ficha(c, str(exc), 400)
        auditar("deposito", alvo=c.codigo, detalhe=f"{valor} cêntimos" + (f"; {descricao}" if descricao else ""))
        flash("Depósito registado.", "sucesso")
        return redirect(url_for("balcao_cliente", cliente_id=c.id))

    @app.post("/balcao/clientes/<int:cliente_id>/levantamento")
    @exige_area("balcao")
    def balcao_cliente_levantamento(cliente_id):
        c = _cliente(cliente_id)
        artefatos = current_app.config["ARTEFATOS"]
        if artefatos is None:
            return _ficha(c, "O detetor ainda não está pronto: não é possível fazer levantamentos.", 503)
        try:
            valor = para_centavos(request.form.get("valor", ""))
        except ValueError as exc:
            return _ficha(c, str(exc), 400)
        cidade = request.form.get("cidade", "").strip().upper()
        if cidade not in CIDADES:
            cidade = c.cidade
        u = utilizador_atual()
        pedido = PedidoDebito(conta_id=c.conta.id, tipo="levantamento", valor=valor, momento=datetime.now(),
                              cidade=cidade, pais="MZ", categoria=CATEGORIA_LEVANTAMENTO, canal="balcao",
                              por=u.nome_utilizador)
        [d] = autorizar(sessoes, artefatos, [pedido], current_app.config["CORTES"], "banco")
        auditar("levantamento", alvo=c.codigo, detalhe=f"{valor} cêntimos; {d.estado}" + (f"; {d.motivo}" if d.motivo else ""))
        if d.estado == "autorizado":
            flash("Levantamento autorizado. Entregue o dinheiro.", "sucesso")
        elif d.estado == "retido":
            flash(Markup("Em verificação. <strong>Não entregue o dinheiro</strong> até a equipa de fraude decidir."),
                  "retido")
        else:
            flash(d.motivo or "Levantamento recusado.", "erro")
        return redirect(url_for("balcao_cliente", cliente_id=c.id))

    @app.post("/balcao/clientes/<int:cliente_id>/acesso")
    @exige_area("balcao")
    def balcao_cliente_acesso(cliente_id):
        c = _cliente(cliente_id)
        try:
            novo = acesso_cliente.criar_ou_redefinir(sessoes, c.id, request.form.get("senha", ""))
        except ValueError as exc:
            return _ficha(c, str(exc), 400)
        auditar("acesso_cliente_criado" if novo else "senha_cliente_redefinida", alvo=c.codigo)
        flash("Acesso criado. O cliente muda a senha ao entrar." if novo
              else "Senha redefinida. O cliente muda-a ao entrar e as sessões abertas terminaram.", "sucesso")
        return redirect(url_for("balcao_cliente", cliente_id=c.id))

    @app.post("/balcao/clientes/<int:cliente_id>/bloquear")
    @exige_area("balcao")
    def balcao_cliente_bloquear(cliente_id):
        c = _cliente(cliente_id)
        bloquear = request.form.get("bloquear") == "1"
        contas_bancarias.bloquear(sessoes, c.conta.id, bloquear, utilizador_atual().nome_utilizador)
        auditar("conta_bancaria_bloqueada" if bloquear else "conta_bancaria_desbloqueada", alvo=c.codigo, detalhe=c.conta.numero)
        flash("Conta bloqueada." if bloquear else "Conta desbloqueada.", "sucesso")
        return redirect(url_for("balcao_cliente", cliente_id=c.id))
