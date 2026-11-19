"""Área do cliente: saldo e movimentos, detalhe de um movimento, transferências e pagamentos."""
from __future__ import annotations

from datetime import datetime

from flask import Flask, abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from . import acesso, clientes, contas_bancarias, locais
from .autorizacao import PedidoDebito, autorizar
from .banco import ContaBancaria, Movimento, TransacaoAvaliada
from .dinheiro import para_centavos
from .contas_bancarias import NOMES_TIPO
from .gerador import CATEGORIA_TRANSFERENCIA, CATEGORIAS, PAIS_HABITUAL

ENDPOINTS = frozenset({"cliente_inicio", "cliente_movimento", "cliente_transferir", "cliente_transferir_post",
                       "cliente_pagar", "cliente_pagar_post"})
ULTIMOS = 50
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
            avaliada = s.get(TransacaoAvaliada, m.transacao_id) if m.transacao_id else None
            onde = None if avaliada is None else {"categoria": avaliada.categoria,
                                                  "local": locais.descrever_local(avaliada.cidade, avaliada.pais)}
            s.expunge(m)
        return render_template("cliente_movimento.html", m=m, numeros=_numeros([m]), onde=onde)

    def _demo() -> bool:
        return current_app.config["CONFIGURACAO"].modo_demonstracao

    def _pagina(modelo, valores=None, erro=None, resultado=None, estado=200):
        return render_template(modelo, conta=_conta(), valores=valores or {}, erro=erro, resultado=resultado,
                               demonstracao=_demo(), locais=locais.opcoes_local(),
                               categorias=CATEGORIAS), estado

    def _onde_e_quando(f, valores: dict) -> tuple[str, str, datetime]:
        """Local e momento: escolhidos no modo demonstração; senão a cidade do cliente e agora."""
        c, agora = g.cliente, datetime.now().replace(microsecond=0)
        if not _demo():
            return c.cidade, PAIS_HABITUAL, agora
        valores.update(local=f.get("local", "").strip()[:20], data=f.get("data", "").strip()[:10],
                       hora=f.get("hora", "").strip()[:5])
        cidade, pais = locais.ler_local(valores["local"] or locais.valor_local(c.cidade, PAIS_HABITUAL), c.cidade)
        return cidade, pais, locais.ler_momento(valores["data"], valores["hora"], agora)

    def _concluir(d, tipo: str, modelo: str, valores: dict):
        texto = estado_texto(tipo, d.estado, d.motivo)
        if d.movimento_id:  # post/redirect/get: atualizar a página não repete a operação
            flash(texto, {"autorizado": "sucesso", "retido": "retido"}.get(d.estado, "erro"))
            return redirect(url_for("cliente_movimento", movimento_id=d.movimento_id), 303)
        resultado = {"texto": texto, "estado": d.estado, "movimento_id": None}
        return _pagina(modelo, {k: valores.get(k) for k in ("local", "data", "hora")}, None, resultado)

    SEM_MODELOS_MSG = "O detetor ainda não está pronto: não é possível {} sem os modelos treinados."

    @app.get("/cliente/transferir")
    def cliente_transferir():
        return _pagina("cliente_transferir.html")

    @app.post("/cliente/transferir")
    def cliente_transferir_post():
        f, modelo = request.form, "cliente_transferir.html"
        valores = {"conta_destino": f.get("conta_destino", "").strip()[:20], "valor": f.get("valor", "").strip()[:30],
                   "descricao": " ".join(f.get("descricao", "").split())[:140]}
        artefatos = current_app.config["ARTEFATOS"]
        if artefatos is None:
            return _pagina(modelo, valores, SEM_MODELOS_MSG.format("fazer transferências"), None, 503)
        try:
            valor = para_centavos(valores["valor"])
            if not valores["conta_destino"]:
                raise ValueError("Indique a conta de destino.")
            cidade, pais, momento = _onde_e_quando(f, valores)
        except ValueError as exc:
            return _pagina(modelo, valores, str(exc), None, 400)
        c = g.cliente
        with sessoes() as s:
            destino = s.scalar(select(ContaBancaria.id).where(ContaBancaria.numero == valores["conta_destino"]))
        pedido = PedidoDebito(conta_id=_conta().id, tipo="transferencia_enviada", valor=valor, momento=momento,
                              cidade=cidade, pais=pais, categoria=CATEGORIA_TRANSFERENCIA, canal="cliente",
                              por=f"cliente:{c.codigo}", contraparte_id=SEM_DESTINO if destino is None else destino,
                              descricao=valores["descricao"] or None)
        [d] = autorizar(sessoes, artefatos, [pedido], current_app.config["CORTES"], "banco")
        acesso.auditar("transferencia_cliente", alvo=c.codigo, nome_utilizador=f"cliente:{c.codigo}",
                       detalhe=f"para {valores['conta_destino']}; {valor} cêntimos; {d.estado}"
                               + (f"; {d.motivo}" if d.motivo else ""))
        return _concluir(d, "transferencia_enviada", modelo, valores)

    @app.get("/cliente/pagar")
    def cliente_pagar():
        return _pagina("cliente_pagar.html")

    @app.post("/cliente/pagar")
    def cliente_pagar_post():
        f, modelo = request.form, "cliente_pagar.html"
        valores = {"categoria": f.get("categoria", "").strip().upper()[:30],
                   "comerciante": " ".join(f.get("comerciante", "").split()), "valor": f.get("valor", "").strip()[:30]}
        artefatos = current_app.config["ARTEFATOS"]
        if artefatos is None:
            return _pagina(modelo, valores, SEM_MODELOS_MSG.format("fazer pagamentos"), None, 503)
        try:
            if valores["categoria"] not in CATEGORIAS:
                raise ValueError("Escolha a categoria do pagamento.")
            if not valores["comerciante"] or len(valores["comerciante"]) > 140:
                raise ValueError("Indique o comerciante (até 140 caracteres).")
            valor = para_centavos(valores["valor"])
            cidade, pais, momento = _onde_e_quando(f, valores)
        except ValueError as exc:
            return _pagina(modelo, valores, str(exc), None, 400)
        c = g.cliente
        pedido = PedidoDebito(conta_id=_conta().id, tipo="compra", valor=valor, momento=momento, cidade=cidade,
                              pais=pais, categoria=valores["categoria"], canal="cliente", por=f"cliente:{c.codigo}",
                              descricao=valores["comerciante"])
        [d] = autorizar(sessoes, artefatos, [pedido], current_app.config["CORTES"], "banco")
        acesso.auditar("pagamento_cliente", alvo=c.codigo, nome_utilizador=f"cliente:{c.codigo}",
                       detalhe=f"{valores['categoria']}; {valores['comerciante']}; {valor} cêntimos; {d.estado}"
                               + (f"; {d.motivo}" if d.motivo else ""))
        return _concluir(d, "compra", modelo, valores)
