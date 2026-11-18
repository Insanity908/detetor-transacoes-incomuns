"""Operações sobre as contas do banco: depósitos, extrato, bloqueio e verificação de saldos (cêntimos inteiros)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from detector.banco import CANAIS_MOVIMENTO, ContaBancaria, Movimento

TIPOS_ENTRADA = ("deposito", "transferencia_recebida")


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
