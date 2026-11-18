"""Chaves de acesso à API e limite de pedidos por chave.

Cada chave tem 'dtf_' + 40 caracteres aleatórios. Só o SHA-256 fica no banco (como as chaves são longas e
aleatórias, não precisam de um hash lento como as senhas); a chave completa só é mostrada ao ser criada.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from .banco import ChaveApi

PREFIXO = "dtf_"
LIMITE_PADRAO = 600  # pedidos por minuto
LIMITE_MAXIMO = 60_000


def _hash(chave: str) -> str:
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()


def _agora() -> datetime:
    return datetime.now().replace(microsecond=0)


@dataclass(frozen=True)
class ChaveAutenticada:
    id: int
    nome: str
    limite_por_minuto: int


def criar(sessoes: sessionmaker, nome: str, criada_por: str, limite_por_minuto: int = LIMITE_PADRAO) -> tuple[str, ChaveApi]:
    """Devolve a chave completa (para mostrar uma vez) e o registo gravado."""
    nome = (nome or "").strip()
    if not 3 <= len(nome) <= 80:
        raise ValueError("Dê à chave um nome entre 3 e 80 caracteres (por exemplo, o sistema que a vai usar).")
    if not 1 <= int(limite_por_minuto) <= LIMITE_MAXIMO:
        raise ValueError(f"O limite tem de estar entre 1 e {LIMITE_MAXIMO} pedidos por minuto.")
    chave = PREFIXO + secrets.token_urlsafe(30)
    registo = ChaveApi(nome=nome, prefixo=chave[:12], hash=_hash(chave), limite_por_minuto=int(limite_por_minuto),
                       ativa=True, criada_em=_agora(), criada_por=criada_por[:50], pedidos=0, transacoes=0)
    with sessoes.begin() as sessao:
        sessao.add(registo)
    return chave, registo


def autenticar(sessoes: sessionmaker, chave: str | None) -> ChaveAutenticada | None:
    if not chave or not chave.startswith(PREFIXO) or len(chave) > 200:
        return None
    with sessoes() as sessao:
        registo = sessao.scalar(select(ChaveApi).where(ChaveApi.hash == _hash(chave), ChaveApi.ativa))
    if registo is None:
        return None
    return ChaveAutenticada(registo.id, registo.nome, registo.limite_por_minuto)


def registar_uso(sessoes: sessionmaker, chave_id: int, transacoes: int) -> None:
    with sessoes.begin() as sessao:
        sessao.execute(update(ChaveApi).where(ChaveApi.id == chave_id).values(
            ultimo_uso=_agora(), pedidos=ChaveApi.pedidos + 1, transacoes=ChaveApi.transacoes + transacoes))


def listar(sessoes: sessionmaker) -> list[ChaveApi]:
    with sessoes() as sessao:
        return list(sessao.scalars(select(ChaveApi).order_by(ChaveApi.ativa.desc(), ChaveApi.criada_em.desc())))


def obter(sessoes: sessionmaker, chave_id: int) -> ChaveApi | None:
    with sessoes() as sessao:
        return sessao.get(ChaveApi, chave_id)


def revogar(sessoes: sessionmaker, chave_id: int) -> ChaveApi:
    with sessoes.begin() as sessao:
        registo = sessao.get(ChaveApi, chave_id)
        if registo is None:
            raise ValueError("Chave não encontrada.")
        if not registo.ativa:
            raise ValueError("Esta chave já estava revogada.")
        registo.ativa = False
        sessao.flush()
        sessao.expunge(registo)
    return registo


class LimitadorPedidos:
    """Janela deslizante de 60 s por chave, em memória (um único processo: o waitress usa threads)."""

    def __init__(self, janela: float = 60.0, relogio=time.monotonic):
        self.janela, self.relogio = janela, relogio
        self._pedidos: dict[int, deque] = {}
        self._trinco = threading.Lock()

    def permitir(self, chave_id: int, limite: int) -> tuple[bool, int]:
        """(permitido, segundos até poder tentar de novo)."""
        agora = self.relogio()
        with self._trinco:
            fila = self._pedidos.setdefault(chave_id, deque())
            while fila and fila[0] <= agora - self.janela:
                fila.popleft()
            if len(fila) >= limite:
                return False, max(1, int(fila[0] + self.janela - agora) + 1)
            fila.append(agora)
            return True, 0
