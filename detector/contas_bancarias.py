"""Operações sobre as contas do banco: depósitos, extrato, bloqueio e verificação de saldos (cêntimos inteiros)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from detector.banco import CANAIS_MOVIMENTO, ContaBancaria, Movimento, TIPOS_MOVIMENTO

TIPOS_ENTRADA = ("deposito", "transferencia_recebida")
ESTADOS_DEBITO = ("autorizado", "retido", "recusado")


def _agora() -> datetime:
    return datetime.now().replace(microsecond=0)


def creditar(s: Session, conta_id: int, valor: int, tipo: str, canal: str, por: str,
             momento: datetime | None = None, descricao: str | None = None,
             contraparte_id: int | None = None) -> Movimento:
    """Soma `valor` ao saldo e regista o movimento autorizado, na sessão aberta (o commit é de quem chama)."""
    if valor <= 0:
        raise ValueError("O valor tem de ser maior que zero.")
    if canal not in CANAIS_MOVIMENTO:
        raise ValueError(f"Canal inválido: {canal}.")
    conta = s.get(ContaBancaria, conta_id)
    if conta is None:
        raise ValueError("Conta não encontrada.")
    if conta.estado == "bloqueada":
        raise ValueError("A conta está bloqueada e não pode receber dinheiro.")
    s.execute(update(ContaBancaria).where(ContaBancaria.id == conta_id).values(saldo=ContaBancaria.saldo + valor))
    saldo_apos = s.execute(select(ContaBancaria.saldo).where(ContaBancaria.id == conta_id)
                           .execution_options(populate_existing=True)).scalar_one()
    agora = _agora()
    movimento = Movimento(conta_id=conta_id, tipo=tipo, valor=valor, estado="autorizado", canal=canal,
                          momento=momento or agora, registado_em=agora, descricao=descricao,
                          contraparte_id=contraparte_id, saldo_apos=saldo_apos, criado_por=por)
    s.add(movimento)
    s.flush()
    return movimento


def _saldo(s: Session, conta_id: int) -> int:
    return s.execute(select(ContaBancaria.saldo).where(ContaBancaria.id == conta_id)
                     .execution_options(populate_existing=True)).scalar_one()


def debitar(s: Session, conta: ContaBancaria, tipo: str, valor: int, canal: str, por: str, momento: datetime,
            estado: str, motivo: str | None = None, contraparte_id: int | None = None,
            transacao_id: int | None = None, id_transacao: str | None = None,
            descricao: str | None = None) -> Movimento:
    """Regista uma saída: autorizada (debita), retida (reserva) ou recusada (só grava). Sem commit.

    O controlo do saldo é um UPDATE condicional, por isso dois pedidos em simultâneo não gastam o mesmo dinheiro.
    """
    if valor <= 0:
        raise ValueError("O valor tem de ser maior que zero.")
    if tipo not in TIPOS_MOVIMENTO or tipo in TIPOS_ENTRADA:
        raise ValueError(f"Tipo de saída inválido: {tipo}.")
    if canal not in CANAIS_MOVIMENTO:
        raise ValueError(f"Canal inválido: {canal}.")
    if estado not in ESTADOS_DEBITO:
        raise ValueError(f"Estado inválido para um débito: {estado}.")
    destino = s.get(ContaBancaria, contraparte_id) if contraparte_id is not None else None
    if contraparte_id is not None and destino is None:
        raise ValueError("Conta de destino não encontrada.")

    saldo_apos = None
    if estado != "recusado":
        if conta.estado == "bloqueada":
            estado, motivo = "recusado", "A conta está bloqueada."
        elif tipo == "transferencia_enviada" and destino is not None and destino.estado == "bloqueada":
            estado, motivo = "recusado", "A conta de destino está bloqueada."
    if estado != "recusado":
        if estado == "autorizado":
            valores = {"saldo": ContaBancaria.saldo - valor}
        else:
            valores = {"saldo_retido": ContaBancaria.saldo_retido + valor}
        feito = s.execute(update(ContaBancaria).where(ContaBancaria.id == conta.id,
                                                      ContaBancaria.saldo - ContaBancaria.saldo_retido >= valor)
                          .values(**valores)).rowcount
        if not feito:
            estado, motivo = "recusado", "Saldo insuficiente."
        elif estado == "autorizado":
            saldo_apos = _saldo(s, conta.id)
    agora = _agora()
    movimento = Movimento(conta_id=conta.id, tipo=tipo, valor=valor, estado=estado, motivo=motivo,
                          contraparte_id=contraparte_id, canal=canal, momento=momento, registado_em=agora,
                          transacao_id=transacao_id, id_transacao=id_transacao, descricao=descricao,
                          saldo_apos=saldo_apos, criado_por=por)
    s.add(movimento)
    s.flush()
    if estado == "autorizado" and tipo == "transferencia_enviada" and destino is not None:  # sem destino: a saída é para fora do banco
        creditar(s, contraparte_id, valor, "transferencia_recebida", canal, por, momento, descricao, conta.id)
    return movimento


def liquidar(s: Session, movimento_id: int, executar: bool, por: str) -> Movimento:
    """Resolve um movimento retido: executa-o (debita e credita o destino) ou cancela-o. Sem commit."""
    # Reclama o movimento de forma atómica: só uma liquidação consegue passar de "retido" a outro estado.
    reclamado = s.execute(update(Movimento).where(Movimento.id == movimento_id, Movimento.estado == "retido")
                          .values(estado="cancelado")).rowcount
    if not reclamado:
        if s.get(Movimento, movimento_id) is None:
            raise ValueError("Movimento não encontrado.")
        raise ValueError("O movimento não está retido.")
    movimento = s.execute(select(Movimento).where(Movimento.id == movimento_id)
                          .execution_options(populate_existing=True)).scalar_one()
    valor = movimento.valor
    s.execute(update(ContaBancaria).where(ContaBancaria.id == movimento.conta_id)
              .values(saldo_retido=ContaBancaria.saldo_retido - valor))
    if not executar:
        movimento.motivo = "Fraude confirmada."
    else:
        origem_estado = s.execute(select(ContaBancaria.estado).where(ContaBancaria.id == movimento.conta_id)
                                  .execution_options(populate_existing=True)).scalar_one()
        destino = s.get(ContaBancaria, movimento.contraparte_id) if movimento.contraparte_id else None
        if origem_estado == "bloqueada":
            movimento.motivo = "A conta foi bloqueada entretanto."
        elif movimento.tipo == "transferencia_enviada" and movimento.contraparte_id is not None and (destino is None or destino.estado == "bloqueada"):
            movimento.motivo = "A conta de destino foi bloqueada entretanto."
        else:
            s.execute(update(ContaBancaria).where(ContaBancaria.id == movimento.conta_id)
                      .values(saldo=ContaBancaria.saldo - valor))
            movimento.estado = "autorizado"
            movimento.saldo_apos = _saldo(s, movimento.conta_id)
            if movimento.tipo == "transferencia_enviada" and movimento.contraparte_id is not None:
                creditar(s, movimento.contraparte_id, valor, "transferencia_recebida", movimento.canal, por,
                         movimento.momento, movimento.descricao, movimento.conta_id)
    s.flush()
    return movimento


def movimento_da_transacao(s: Session, transacao_id: int) -> Movimento | None:
    """O movimento ligado a uma transação avaliada, se existir."""
    return s.scalar(select(Movimento).where(Movimento.transacao_id == transacao_id).order_by(Movimento.id).limit(1))


def depositar(sessoes, conta_id: int, valor: int, canal: str, por: str, momento: datetime | None = None,
              descricao: str | None = None) -> Movimento:
    """Deposita dinheiro numa conta ativa: saldo e movimento mudam na mesma transação."""
    with sessoes() as s:
        movimento = creditar(s, conta_id, valor, "deposito", canal, por, momento, descricao)
        s.commit()
        s.refresh(movimento)
        s.expunge(movimento)
        return movimento


def extrato(sessoes, conta_id: int, limite: int = 100) -> list[Movimento]:
    """Movimentos da conta, os mais recentes primeiro."""
    with sessoes() as s:
        movimentos = list(s.scalars(select(Movimento).where(Movimento.conta_id == conta_id)
                                    .order_by(Movimento.momento.desc(), Movimento.id.desc()).limit(limite)))
        s.expunge_all()
        return movimentos


def bloquear(sessoes, conta_id: int, bloquear: bool, por: str) -> None:
    """Bloqueia ou desbloqueia a conta."""
    with sessoes() as s:
        conta = s.get(ContaBancaria, conta_id)
        if conta is None:
            raise ValueError("Conta não encontrada.")
        conta.estado = "bloqueada" if bloquear else "ativa"
        s.commit()


def verificar_saldos(sessoes) -> list[str]:
    """Uma linha por conta cujo saldo ou retido não bate com os movimentos; lista vazia = tudo certo."""
    problemas = []
    with sessoes() as s:
        def soma(conta_id: int, estado: str, entrada: bool) -> int:
            filtro = Movimento.tipo.in_(TIPOS_ENTRADA) if entrada else Movimento.tipo.not_in(TIPOS_ENTRADA)
            return s.scalar(select(func.coalesce(func.sum(Movimento.valor), 0))
                            .where(Movimento.conta_id == conta_id, Movimento.estado == estado, filtro))

        for conta in s.scalars(select(ContaBancaria).order_by(ContaBancaria.id)):
            esperado = soma(conta.id, "autorizado", True) - soma(conta.id, "autorizado", False)
            retido = soma(conta.id, "retido", False)
            if conta.saldo != esperado:
                problemas.append(f"Conta {conta.numero}: saldo {conta.saldo} difere dos movimentos ({esperado}).")
            if conta.saldo_retido != retido:
                problemas.append(f"Conta {conta.numero}: retido {conta.saldo_retido} difere dos movimentos ({retido}).")
    return problemas
