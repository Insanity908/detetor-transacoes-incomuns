"""Registo de auditoria: cada ação fica gravada no banco com quem a fez, quando e sobre o quê."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, insert, select
from sqlalchemy.orm import sessionmaker

from .banco import EventoAuditoria, Utilizador

ACOES = {
    "entrada": "Entrou no sistema",
    "entrada_falhada": "Tentativa de entrada falhada",
    "conta_bloqueada": "Conta bloqueada por tentativas falhadas",
    "saida": "Saiu do sistema",
    "acesso_negado": "Acesso negado",
    "testar_transacao": "Testou transação",
    "analisar_ficheiro": "Analisou ficheiro",
    "consultar_analise": "Consultou análise",
    "descarregar_analise": "Descarregou resultado",
    "consultar_perfil": "Consultou perfil de cliente",
    "consultar_alerta": "Consultou alerta",
    "assumir_alerta": "Assumiu alerta",
    "atribuir_alerta": "Atribuiu alerta",
    "devolver_alerta": "Devolveu alerta à fila",
    "decidir_alerta": "Decidiu alerta",
    "reabrir_alerta": "Reabriu alerta",
    "comentar_alerta": "Comentou alerta",
    "chave_criada": "Criou chave da API",
    "chave_revogada": "Revogou chave da API",
    "utilizador_criado": "Criou utilizador",
    "utilizador_alterado": "Alterou utilizador",
    "senha_redefinida": "Redefiniu senha de utilizador",
    "senha_alterada": "Alterou a própria senha",
}
POR_PAGINA = 50


@dataclass(frozen=True)
class Filtro:
    utilizador: str = ""
    acao: str = ""
    alvo: str = ""
    desde: date | None = None
    ate: date | None = None


def registar(sessoes: sessionmaker, acao: str, utilizador: Utilizador | None = None, *, nome_utilizador: str = "",
             alvo: str | None = None, detalhe: str | None = None, ip: str | None = None) -> None:
    if acao not in ACOES:
        raise ValueError(f"Ação de auditoria desconhecida: {acao}")
    with sessoes.begin() as sessao:
        sessao.execute(insert(EventoAuditoria), [{
            "momento": datetime.now().replace(microsecond=0),
            "utilizador_id": utilizador.id if utilizador else None,
            "nome_utilizador": (utilizador.nome_utilizador if utilizador else nome_utilizador or "—")[:50],
            "acao": acao,
            "alvo": alvo[:255] if alvo else None,
            "detalhe": detalhe,
            "ip": (ip or "")[:45] or None,
        }])


def _filtrar(consulta, filtro: Filtro):
    if filtro.utilizador:
        consulta = consulta.where(EventoAuditoria.nome_utilizador == filtro.utilizador.strip().lower())
    if filtro.acao:
        consulta = consulta.where(EventoAuditoria.acao == filtro.acao)
    if filtro.alvo:
        consulta = consulta.where(EventoAuditoria.alvo == filtro.alvo.strip())
    if filtro.desde:
        consulta = consulta.where(EventoAuditoria.momento >= datetime.combine(filtro.desde, datetime.min.time()))
    if filtro.ate:
        consulta = consulta.where(EventoAuditoria.momento < datetime.combine(filtro.ate + timedelta(days=1),
                                                                              datetime.min.time()))
    return consulta


def listar(sessoes: sessionmaker, filtro: Filtro = Filtro(), pagina: int = 1,
           por_pagina: int = POR_PAGINA) -> tuple[list[EventoAuditoria], int]:
    """Os eventos mais recentes primeiro, e o total que corresponde ao filtro."""
    pagina = max(1, pagina)
    with sessoes() as sessao:
        total = sessao.scalar(_filtrar(select(func.count(EventoAuditoria.id)), filtro)) or 0
        consulta = (_filtrar(select(EventoAuditoria), filtro)
                    .order_by(EventoAuditoria.momento.desc(), EventoAuditoria.id.desc())
                    .offset((pagina - 1) * por_pagina).limit(por_pagina))
        return list(sessao.scalars(consulta)), total


def nomes_registados(sessoes: sessionmaker) -> list[str]:
    with sessoes() as sessao:
        return list(sessao.scalars(select(EventoAuditoria.nome_utilizador).distinct()
                                   .order_by(EventoAuditoria.nome_utilizador)))
