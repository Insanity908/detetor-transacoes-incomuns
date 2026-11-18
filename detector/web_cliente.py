"""Área do cliente: saldo e movimentos, detalhe de um movimento e transferências."""
from __future__ import annotations

from datetime import datetime

from flask import Flask, abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from . import acesso, clientes, contas_bancarias
from .autorizacao import PedidoDebito, autorizar
from .banco import ContaBancaria, Movimento
from .dinheiro import para_centavos
from .contas_bancarias import NOMES_TIPO
from .gerador import CATEGORIA_TRANSFERENCIA, CIDADES, PAIS_HABITUAL, PAISES_ESTRANHOS

ENDPOINTS = frozenset({"cliente_inicio", "cliente_movimento", "cliente_transferir", "cliente_transferir_post"})
ULTIMOS = 50
PAISES = (PAIS_HABITUAL, *PAISES_ESTRANHOS)
ESTADOS = {"autorizado": "Concluída", "retido": "Em verificação", "cancelado": "Cancelada"}
SEM_DESTINO = -1  # id que nunca existe: o detetor recusa com "Conta de destino não encontrada."


def estado_texto(tipo: str, estado: str, motivo: str | None) -> str:
    """O estado de um movimento em palavras do cliente."""
    if estado == "recusado":
        return f"Recusada — {motivo}" if motivo else "Recusada"
    if estado == "autorizado" and tipo == "transferencia_recebida":
        return "Recebida"
    return ESTADOS.get(estado, estado)


def instalar(app: Flask, sessoes: sessionmaker) -> None:
    @app.context_processor
    def _contexto_cliente_web():
        return {"estado_texto": estado_texto, "nomes_tipo_cliente": NOMES_TIPO}

    def _numeros(movimentos: list[Movimento]) -> dict[int, str]:
        """Número da conta da outra parte (nunca o nome: é de outro cliente)."""
        ids = {m.contraparte_id for m in movimentos if m.contraparte_id}
        if not ids:
            return {}
        with sessoes() as s:
            return dict(s.execute(select(ContaBancaria.id, ContaBancaria.numero)
                                  .where(ContaBancaria.id.in_(ids))).all())

    def _conta():
        return clientes.obter(sessoes, g.cliente.id).conta

    @app.get("/cliente/")
    def cliente_inicio():
        conta = _conta()
        movimentos = contas_bancarias.extrato(sessoes, conta.id, ULTIMOS)
        return render_template("cliente_inicio.html", conta=conta, movimentos=movimentos,
                               numeros=_numeros(movimentos))

    @app.get("/cliente/movimentos/<int:movimento_id>")
    def cliente_movimento(movimento_id):
        conta = _conta()
        with sessoes() as s:
            m = s.scalar(select(Movimento).where(Movimento.id == movimento_id, Movimento.conta_id == conta.id))
            if m is None:  # de outro cliente ou inexistente: a resposta é a mesma
                abort(404)
            s.expunge(m)
        return render_template("cliente_movimento.html", m=m, numeros=_numeros([m]))

    def _pagina(valores=None, erro=None, resultado=None, estado=200):
        return render_template("cliente_transferir.html", conta=_conta(), valores=valores or {}, erro=erro,
                               resultado=resultado, demonstracao=current_app.config["CONFIGURACAO"].modo_demonstracao,
                               paises=PAISES), estado

    @app.get("/cliente/transferir")
    def cliente_transferir():
        return _pagina()

    @app.post("/cliente/transferir")
    def cliente_transferir_post():
        f = request.form
        valores = {"conta_destino": f.get("conta_destino", "").strip()[:20], "valor": f.get("valor", "").strip()[:30],
                   "descricao": " ".join(f.get("descricao", "").split())[:140]}
        artefatos = current_app.config["ARTEFATOS"]
        if artefatos is None:
            return _pagina(valores, "O detetor ainda não está pronto: não é possível fazer transferências "
                                    "sem os modelos treinados.", None, 503)
        try:
            valor = para_centavos(valores["valor"])
            if not valores["conta_destino"]:
                raise ValueError("Indique a conta de destino.")
        except ValueError as exc:
            return _pagina(valores, str(exc), None, 400)
        c = g.cliente
        cidade, pais = c.cidade, PAIS_HABITUAL
        if current_app.config["CONFIGURACAO"].modo_demonstracao:
            cidade = f.get("cidade", "").strip().upper()
            cidade = cidade if cidade in CIDADES else c.cidade
            pais = f.get("pais", "").strip().upper()
            pais = pais if pais in PAISES else PAIS_HABITUAL
            valores.update(cidade=cidade, pais=pais)
        with sessoes() as s:
            destino = s.scalar(select(ContaBancaria.id).where(ContaBancaria.numero == valores["conta_destino"]))
        pedido = PedidoDebito(conta_id=_conta().id, tipo="transferencia_enviada", valor=valor, momento=datetime.now(),
                              cidade=cidade, pais=pais, categoria=CATEGORIA_TRANSFERENCIA, canal="cliente",
                              por=f"cliente:{c.codigo}", contraparte_id=SEM_DESTINO if destino is None else destino,
                              descricao=valores["descricao"] or None)
        [d] = autorizar(sessoes, artefatos, [pedido], current_app.config["CORTES"], "banco")
        acesso.auditar("transferencia_cliente", alvo=c.codigo, nome_utilizador=f"cliente:{c.codigo}",
                       detalhe=f"para {valores['conta_destino']}; {valor} cêntimos; {d.estado}"
                               + (f"; {d.motivo}" if d.motivo else ""))
        texto = estado_texto("transferencia_enviada", d.estado, d.motivo)
        if d.movimento_id:  # post/redirect/get: atualizar a página não repete a transferência
            flash(texto, {"autorizado": "sucesso", "retido": "retido"}.get(d.estado, "erro"))
            return redirect(url_for("cliente_movimento", movimento_id=d.movimento_id), 303)
        resultado = {"texto": texto, "estado": d.estado, "movimento_id": None}
        return _pagina({"cidade": valores.get("cidade"), "pais": valores.get("pais")}, None, resultado)
