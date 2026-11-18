"""Fila de alertas: estados, atribuição, decisão, comentários, histórico, notificações e indicadores.

Ciclo de vida:  Novo -> Em análise -> Fraude confirmada | Falso alarme
- Qualquer utilizador pode assumir um alerta novo; o supervisor também o pode atribuir a alguém.
- Decide quem tem o alerta (ou um supervisor), sempre com uma justificação.
- O responsável (ou um supervisor) pode devolvê-lo à fila; só um supervisor reabre um alerta decidido.

Cada mudança é um UPDATE condicionado ao estado esperado: se duas pessoas agirem ao mesmo tempo,
só a primeira consegue e a outra recebe uma mensagem clara em vez de sobrescrever.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from .banco import ESTADOS_ABERTOS, Alerta, EventoAlerta, TransacaoAvaliada, Utilizador

NOMES_ESTADO = {"novo": "Novo", "em_analise": "Em análise", "fraude": "Fraude confirmada",
                "falso_alarme": "Falso alarme"}
DECISOES = ("fraude", "falso_alarme")
VISTAS = {"abertos": "Abertos", "novos": "Novos", "meus": "Os meus", "encerrados": "Encerrados"}
NIVEIS = ("Alto risco", "Atenção")
POR_PAGINA = 50
MAX_TEXTO = 2000
NOTA_MINIMA = 5


class AlertaMudou(ValueError):
    """O alerta já não está no estado em que a pessoa o viu (outra pessoa agiu primeiro) ou não pode agir."""


def _agora() -> datetime:
    return datetime.now().replace(microsecond=0)


# ------------------------------------------------------------ criação
def criar_para_analise(sessao: Session, analise_id: int, momento: datetime) -> int:
    """Cria um alerta novo por cada transação com alerta desta análise. Corre dentro da gravação da análise."""
    ids = sessao.scalars(select(TransacaoAvaliada.id)
                         .where(TransacaoAvaliada.analise_id == analise_id, TransacaoAvaliada.nivel != "Normal")
                         .order_by(TransacaoAvaliada.ordem)).all()
    if ids:
        sessao.execute(insert(Alerta), [{"transacao_id": i, "criado_em": momento, "estado": "novo"} for i in ids])
    return len(ids)


# ------------------------------------------------------------ quem pode o quê
def ve_tudo(u: Utilizador) -> bool:
    return u.pode("supervisor")


def pode_ver(alerta: Alerta, u: Utilizador) -> bool:
    """O analista vê os alertas novos (para os poder assumir) e os que tem ou decidiu."""
    return ve_tudo(u) or alerta.estado == "novo" or u.id in (alerta.atribuido_a, alerta.decidido_por)


def pode_trabalhar(alerta: Alerta, u: Utilizador) -> bool:
    """Decidir, devolver: o responsável ou um supervisor."""
    return alerta.estado == "em_analise" and (ve_tudo(u) or alerta.atribuido_a == u.id)


def _visiveis(consulta, u: Utilizador):
    if ve_tudo(u):
        return consulta
    return consulta.where(or_(Alerta.estado == "novo", Alerta.atribuido_a == u.id, Alerta.decidido_por == u.id))


# ------------------------------------------------------------ consultas
@dataclass(frozen=True)
class Filtro:
    vista: str = "abertos"
    nivel: str = ""
    cliente: str = ""
    responsavel: int | None = None


def listar(sessoes: sessionmaker, u: Utilizador, filtro: Filtro = Filtro(),
           pagina: int = 1) -> tuple[list[Alerta], int]:
    """Os alertas da vista pedida, os de maior risco primeiro (os encerrados: os decididos há menos tempo)."""
    consulta = _visiveis(select(Alerta).join(Alerta.transacao), u)
    if filtro.vista == "novos":
        consulta = consulta.where(Alerta.estado == "novo")
    elif filtro.vista == "meus":
        consulta = consulta.where(Alerta.estado == "em_analise", Alerta.atribuido_a == u.id)
    elif filtro.vista == "encerrados":
        consulta = consulta.where(Alerta.estado.in_(DECISOES))
    else:
        consulta = consulta.where(Alerta.estado.in_(ESTADOS_ABERTOS))
    if filtro.nivel in NIVEIS:
        consulta = consulta.where(TransacaoAvaliada.nivel == filtro.nivel)
    if filtro.cliente:
        consulta = consulta.where(TransacaoAvaliada.cliente == filtro.cliente.strip().upper())
    if filtro.responsavel is not None:
        consulta = consulta.where(Alerta.atribuido_a == filtro.responsavel)
    if filtro.vista == "encerrados":
        ordem = (Alerta.decidido_em.desc(), Alerta.id.desc())
    else:
        ordem = (TransacaoAvaliada.risco.desc(), Alerta.criado_em, Alerta.id)
    with sessoes() as sessao:
        total = sessao.scalar(select(func.count()).select_from(consulta.subquery())) or 0
        alertas = sessao.scalars(consulta.order_by(*ordem).offset((max(1, pagina) - 1) * POR_PAGINA)
                                 .limit(POR_PAGINA)).unique().all()
    return list(alertas), total


def contar_por_vista(sessoes: sessionmaker, u: Utilizador) -> dict[str, int]:
    with sessoes() as sessao:
        def n(*condicoes):
            return sessao.scalar(_visiveis(select(func.count(Alerta.id)), u).where(*condicoes)) or 0
        return {"abertos": n(Alerta.estado.in_(ESTADOS_ABERTOS)), "novos": n(Alerta.estado == "novo"),
                "meus": n(Alerta.estado == "em_analise", Alerta.atribuido_a == u.id),
                "encerrados": n(Alerta.estado.in_(DECISOES))}


def obter(sessoes: sessionmaker, alerta_id: int) -> Alerta | None:
    with sessoes() as sessao:
        return sessao.get(Alerta, alerta_id)


def historico(sessoes: sessionmaker, alerta_id: int) -> list[EventoAlerta]:
    with sessoes() as sessao:
        return list(sessao.scalars(select(EventoAlerta).where(EventoAlerta.alerta_id == alerta_id)
                                   .order_by(EventoAlerta.momento, EventoAlerta.id)))


def outros_do_cliente(sessoes: sessionmaker, alerta: Alerta, u: Utilizador, limite: int = 10) -> list[Alerta]:
    consulta = (_visiveis(select(Alerta).join(Alerta.transacao), u)
                .where(TransacaoAvaliada.cliente == alerta.transacao.cliente, Alerta.id != alerta.id)
                .order_by(Alerta.criado_em.desc(), Alerta.id.desc()).limit(limite))
    with sessoes() as sessao:
        return list(sessao.scalars(consulta).unique())


def responsaveis_possiveis(sessoes: sessionmaker) -> list[Utilizador]:
    with sessoes() as sessao:
        return list(sessao.scalars(select(Utilizador).where(Utilizador.ativo).order_by(Utilizador.nome)))


# ------------------------------------------------------------ mudanças
def _evento(sessao: Session, alerta_id: int, u: Utilizador, tipo: str, de: str | None = None,
            para: str | None = None, texto: str | None = None) -> None:
    sessao.add(EventoAlerta(alerta_id=alerta_id, momento=_agora(), utilizador_id=u.id,
                            nome_utilizador=u.nome_utilizador, tipo=tipo, de=de, para=para, texto=texto))


def _nota(texto: str | None, obrigatoria: bool) -> str | None:
    texto = (texto or "").strip()
    if len(texto) > MAX_TEXTO:
        raise ValueError(f"O texto passa de {MAX_TEXTO} caracteres.")
    if obrigatoria and len(texto) < NOTA_MINIMA:
        raise ValueError("Escreva uma justificação (pelo menos algumas palavras).")
    return texto or None


def _mudar(sessao: Session, alerta_id: int, condicoes: list, valores: dict, erro: str) -> None:
    resultado = sessao.execute(update(Alerta).where(Alerta.id == alerta_id, *condicoes).values(**valores))
    if resultado.rowcount != 1:
        raise AlertaMudou(erro)


def assumir(sessoes: sessionmaker, alerta_id: int, u: Utilizador) -> None:
    with sessoes.begin() as sessao:
        _mudar(sessao, alerta_id, [Alerta.estado == "novo"],
               {"estado": "em_analise", "atribuido_a": u.id, "atribuido_em": _agora()},
               "Este alerta já não está novo: outra pessoa assumiu-o entretanto.")
        _evento(sessao, alerta_id, u, "estado", "novo", "em_analise")
        _evento(sessao, alerta_id, u, "atribuicao", None, u.nome_utilizador)


def atribuir(sessoes: sessionmaker, alerta_id: int, responsavel_id: int, u: Utilizador) -> Utilizador:
    if not ve_tudo(u):
        raise PermissionError("Só um supervisor pode atribuir alertas a outras pessoas.")
    with sessoes.begin() as sessao:
        alvo = sessao.get(Utilizador, responsavel_id)
        alerta = sessao.get(Alerta, alerta_id)
        if alvo is None or not alvo.ativo:
            raise ValueError("Escolha um utilizador ativo.")
        if alerta is None or alerta.estado not in ESTADOS_ABERTOS:
            raise AlertaMudou("Só se atribuem alertas abertos.")
        if alerta.atribuido_a == alvo.id:
            raise ValueError(f"O alerta já está com {alvo.nome_utilizador}.")
        anterior = alerta.responsavel.nome_utilizador if alerta.responsavel else None
        estado_antes, dono_antes = alerta.estado, alerta.atribuido_a
        condicao = Alerta.atribuido_a == dono_antes if dono_antes is not None else Alerta.atribuido_a.is_(None)
        _mudar(sessao, alerta_id, [Alerta.estado == estado_antes, condicao],
               {"estado": "em_analise", "atribuido_a": alvo.id, "atribuido_em": _agora()},
               "O alerta mudou entretanto. Veja o estado atual e tente de novo.")
        if estado_antes != "em_analise":
            _evento(sessao, alerta_id, u, "estado", estado_antes, "em_analise")
        _evento(sessao, alerta_id, u, "atribuicao", anterior, alvo.nome_utilizador)
        sessao.expunge(alvo)
    return alvo


def _condicao_trabalho(u: Utilizador) -> list:
    return [Alerta.estado == "em_analise"] + ([] if ve_tudo(u) else [Alerta.atribuido_a == u.id])


def devolver(sessoes: sessionmaker, alerta_id: int, u: Utilizador, texto: str | None = None) -> None:
    texto = _nota(texto, obrigatoria=False)
    with sessoes.begin() as sessao:
        anterior = sessao.scalar(select(Utilizador.nome_utilizador).join(Alerta, Alerta.atribuido_a == Utilizador.id)
                                 .where(Alerta.id == alerta_id))
        _mudar(sessao, alerta_id, _condicao_trabalho(u),
               {"estado": "novo", "atribuido_a": None, "atribuido_em": None},
               "Só o responsável pelo alerta (ou um supervisor) o pode devolver, e só enquanto está em análise.")
        _evento(sessao, alerta_id, u, "estado", "em_analise", "novo", texto)
        _evento(sessao, alerta_id, u, "atribuicao", anterior, None)


def decidir(sessoes: sessionmaker, alerta_id: int, decisao: str, texto: str, u: Utilizador) -> None:
    if decisao not in DECISOES:
        raise ValueError("Decisão desconhecida.")
    texto = _nota(texto, obrigatoria=True)
    with sessoes.begin() as sessao:
        _mudar(sessao, alerta_id, _condicao_trabalho(u),
               {"estado": decisao, "decidido_por": u.id, "decidido_em": _agora()},
               "Só o responsável pelo alerta (ou um supervisor) o pode decidir, e só enquanto está em análise.")
        _evento(sessao, alerta_id, u, "estado", "em_analise", decisao, texto)


def reabrir(sessoes: sessionmaker, alerta_id: int, texto: str, u: Utilizador) -> None:
    if not ve_tudo(u):
        raise PermissionError("Só um supervisor pode reabrir um alerta decidido.")
    texto = _nota(texto, obrigatoria=True)
    with sessoes.begin() as sessao:
        alerta = sessao.get(Alerta, alerta_id)
        if alerta is None or alerta.estado not in DECISOES:
            raise AlertaMudou("Só se reabrem alertas decididos.")
        estado_antes = alerta.estado
        valores = {"estado": "em_analise", "decidido_por": None, "decidido_em": None}
        if alerta.atribuido_a is None:
            valores.update(atribuido_a=u.id, atribuido_em=_agora())
        _mudar(sessao, alerta_id, [Alerta.estado == estado_antes], valores, "O alerta mudou entretanto.")
        _evento(sessao, alerta_id, u, "estado", estado_antes, "em_analise", texto)


def comentar(sessoes: sessionmaker, alerta_id: int, texto: str, u: Utilizador) -> None:
    texto = _nota(texto, obrigatoria=False)
    if not texto:
        raise ValueError("O comentário está vazio.")
    with sessoes.begin() as sessao:
        _evento(sessao, alerta_id, u, "comentario", texto=texto)


# ------------------------------------------------------------ notificações (sino)
def _novos_alto_risco(u: Utilizador):
    return (select(Alerta).join(Alerta.transacao)
            .where(Alerta.id > (u.ultimo_alerta_visto or 0), Alerta.estado == "novo",
                   TransacaoAvaliada.nivel == "Alto risco"))


def contar_notificacoes(sessoes: sessionmaker, u: Utilizador) -> int:
    with sessoes() as sessao:
        return sessao.scalar(select(func.count()).select_from(_novos_alto_risco(u).subquery())) or 0


def notificacoes(sessoes: sessionmaker, u: Utilizador, limite: int = 50) -> list[Alerta]:
    with sessoes() as sessao:
        return list(sessao.scalars(_novos_alto_risco(u).order_by(Alerta.id.desc()).limit(limite)).unique())


def marcar_vistas(sessoes: sessionmaker, u: Utilizador) -> int:
    with sessoes.begin() as sessao:
        maximo = sessao.scalar(select(func.max(Alerta.id))) or 0
        sessao.execute(update(Utilizador).where(Utilizador.id == u.id).values(ultimo_alerta_visto=maximo))
    return maximo


# ------------------------------------------------------------ indicadores
@dataclass
class Indicadores:
    dias: int
    inicio: date
    por_dia: list[dict] = field(default_factory=list)  # {"dia", "alto", "atencao", "total"}
    criados: int = 0
    decididos: int = 0
    fraudes: int = 0
    falsos: int = 0
    taxa_falsos: float | None = None
    horas_decisao: float | None = None  # média
    horas_decisao_mediana: float | None = None
    abertos: dict = field(default_factory=dict)  # estado -> n
    valor_em_risco: float = 0.0  # soma dos valores dos alertas abertos (agora)
    valor_em_risco_alto: float = 0.0
    valor_fraude: float = 0.0  # soma das fraudes confirmadas no período
    por_pessoa: list[dict] = field(default_factory=list)


def indicadores(sessoes: sessionmaker, dias: int = 30, hoje: date | None = None) -> Indicadores:
    hoje = hoje or date.today()
    inicio = hoje - timedelta(days=dias - 1)
    desde = datetime.combine(inicio, datetime.min.time())
    colunas = (Alerta.criado_em, Alerta.estado, Alerta.atribuido_a, Alerta.decidido_por, Alerta.decidido_em,
               TransacaoAvaliada.nivel, TransacaoAvaliada.valor)
    with sessoes() as sessao:
        linhas = sessao.execute(select(*colunas).join(Alerta.transacao).where(or_(
            Alerta.criado_em >= desde, Alerta.decidido_em >= desde, Alerta.estado.in_(ESTADOS_ABERTOS)))).all()
        nomes = dict(sessao.execute(select(Utilizador.id, Utilizador.nome_utilizador)).all())
    ind = Indicadores(dias=dias, inicio=inicio)

    contagem = {inicio + timedelta(days=i): Counter() for i in range(dias)}
    for l in linhas:
        if l.criado_em >= desde and l.criado_em.date() in contagem:
            contagem[l.criado_em.date()]["alto" if l.nivel == "Alto risco" else "atencao"] += 1
    ind.por_dia = [{"dia": d, "alto": c["alto"], "atencao": c["atencao"], "total": c["alto"] + c["atencao"]}
                   for d, c in contagem.items()]
    ind.criados = sum(d["total"] for d in ind.por_dia)

    decididas = [l for l in linhas if l.decidido_em is not None and l.decidido_em >= desde]
    ind.decididos = len(decididas)
    ind.fraudes = sum(l.estado == "fraude" for l in decididas)
    ind.falsos = sum(l.estado == "falso_alarme" for l in decididas)
    ind.taxa_falsos = ind.falsos / ind.decididos if ind.decididos else None
    horas = sorted((l.decidido_em - l.criado_em).total_seconds() / 3600 for l in decididas)
    if horas:
        ind.horas_decisao = sum(horas) / len(horas)
        meio = len(horas) // 2
        ind.horas_decisao_mediana = horas[meio] if len(horas) % 2 else (horas[meio - 1] + horas[meio]) / 2
    ind.valor_fraude = sum(l.valor for l in decididas if l.estado == "fraude")

    abertos = [l for l in linhas if l.estado in ESTADOS_ABERTOS]
    ind.abertos = {e: sum(l.estado == e for l in abertos) for e in ESTADOS_ABERTOS}
    ind.valor_em_risco = sum(l.valor for l in abertos)
    ind.valor_em_risco_alto = sum(l.valor for l in abertos if l.nivel == "Alto risco")

    pessoas = {l.atribuido_a for l in abertos if l.atribuido_a} | {l.decidido_por for l in decididas if l.decidido_por}
    for pid in pessoas:
        minhas = [l for l in decididas if l.decidido_por == pid]
        h = [(l.decidido_em - l.criado_em).total_seconds() / 3600 for l in minhas]
        ind.por_pessoa.append({
            "nome": nomes.get(pid, "—"),
            "em_analise": sum(l.atribuido_a == pid and l.estado == "em_analise" for l in abertos),
            "decididos": len(minhas),
            "fraudes": sum(l.estado == "fraude" for l in minhas),
            "falsos": sum(l.estado == "falso_alarme" for l in minhas),
            "horas_decisao": sum(h) / len(h) if h else None,
        })
    ind.por_pessoa.sort(key=lambda p: (-p["em_analise"] - p["decididos"], p["nome"]))
    return ind

