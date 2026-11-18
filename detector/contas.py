"""Contas de utilizador: criar, alterar, verificar a senha e bloquear depois de várias tentativas falhadas."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from werkzeug.security import check_password_hash, generate_password_hash

from .banco import PERFIS, Utilizador

NOMES_PERFIL = {"analista": "Analista", "supervisor": "Supervisor", "administrador": "Administrador"}
SENHA_MINIMA = 10
MAX_TENTATIVAS = 5
TEMPO_BLOQUEIO = timedelta(minutes=15)
PADRAO_NOME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,49}$")
# Comparar sempre com um hash, mesmo quando o utilizador não existe, para a resposta demorar o mesmo tempo
# e não revelar que nomes existem.
_HASH_FALSO = generate_password_hash("senha-que-nunca-confere")


@dataclass(frozen=True)
class ResultadoEntrada:
    utilizador: Utilizador | None
    motivo: str | None = None  # credenciais | inativo | bloqueado (quando utilizador é None)
    bloqueado_agora: bool = False


def _agora() -> datetime:
    return datetime.now().replace(microsecond=0)


def normalizar_nome(nome: str) -> str:
    return (nome or "").strip().lower()


def validar_nome(nome: str) -> str:
    nome = normalizar_nome(nome)
    if not PADRAO_NOME.match(nome):
        raise ValueError("O nome de utilizador deve ter 3 a 50 caracteres: letras minúsculas, números, '.', '_' ou '-'.")
    return nome


def validar_senha(senha: str, nome_utilizador: str = "") -> None:
    if len(senha or "") < SENHA_MINIMA:
        raise ValueError(f"A senha deve ter pelo menos {SENHA_MINIMA} caracteres.")
    if nome_utilizador and nome_utilizador.lower() in senha.lower():
        raise ValueError("A senha não pode conter o nome de utilizador.")


def validar_perfil(perfil: str) -> str:
    if perfil not in PERFIS:
        raise ValueError(f"Perfil desconhecido: {perfil}")
    return perfil


def contar(sessoes: sessionmaker) -> int:
    with sessoes() as sessao:
        return sessao.scalar(select(func.count(Utilizador.id))) or 0


def obter(sessoes: sessionmaker, utilizador_id: int) -> Utilizador | None:
    with sessoes() as sessao:
        return sessao.get(Utilizador, utilizador_id)


def obter_por_nome(sessoes: sessionmaker, nome_utilizador: str) -> Utilizador | None:
    with sessoes() as sessao:
        return sessao.scalar(select(Utilizador).where(Utilizador.nome_utilizador == normalizar_nome(nome_utilizador)))


def listar(sessoes: sessionmaker) -> list[Utilizador]:
    with sessoes() as sessao:
        return list(sessao.scalars(select(Utilizador).order_by(Utilizador.ativo.desc(), Utilizador.nome_utilizador)))


def criar(sessoes: sessionmaker, nome_utilizador: str, nome: str, perfil: str, senha: str,
          trocar_senha: bool = False) -> Utilizador:
    nome_utilizador = validar_nome(nome_utilizador)
    nome = (nome or "").strip()[:120] or nome_utilizador
    validar_perfil(perfil)
    validar_senha(senha, nome_utilizador)
    with sessoes.begin() as sessao:
        if sessao.scalar(select(Utilizador.id).where(Utilizador.nome_utilizador == nome_utilizador)):
            raise ValueError(f"Já existe um utilizador '{nome_utilizador}'.")
        utilizador = Utilizador(nome_utilizador=nome_utilizador, nome=nome, perfil=perfil,
                                senha_hash=generate_password_hash(senha), ativo=True, trocar_senha=trocar_senha,
                                tentativas_falhadas=0, versao_sessao=1, criado_em=_agora())
        sessao.add(utilizador)
    return utilizador


def _administradores_ativos(sessao, excepto: int | None = None) -> int:
    consulta = select(func.count(Utilizador.id)).where(Utilizador.perfil == "administrador", Utilizador.ativo)
    if excepto is not None:
        consulta = consulta.where(Utilizador.id != excepto)
    return sessao.scalar(consulta) or 0


def alterar(sessoes: sessionmaker, utilizador_id: int, *, nome: str | None = None, perfil: str | None = None,
            ativo: bool | None = None) -> list[str]:
    """Muda o nome, o perfil ou o estado. Devolve a lista do que mudou. Nunca deixa o sistema sem administrador."""
    mudancas = []
    with sessoes.begin() as sessao:
        u = sessao.get(Utilizador, utilizador_id)
        if u is None:
            raise ValueError("Utilizador não encontrado.")
        perde_admin = (u.perfil == "administrador" and u.ativo
                       and ((perfil is not None and perfil != "administrador") or ativo is False))
        if perde_admin and _administradores_ativos(sessao, excepto=u.id) == 0:
            raise ValueError("Tem de existir pelo menos um administrador ativo.")
        if nome is not None and nome.strip() and nome.strip()[:120] != u.nome:
            u.nome = nome.strip()[:120]
            mudancas.append(f"nome: {u.nome}")
        if perfil is not None and perfil != u.perfil:
            u.perfil = validar_perfil(perfil)
            u.versao_sessao += 1  # o novo perfil vale já: as sessões abertas terminam
            mudancas.append(f"perfil: {perfil}")
        if ativo is not None and ativo != u.ativo:
            u.ativo = ativo
            if ativo:
                u.tentativas_falhadas, u.bloqueado_ate = 0, None
            else:
                u.versao_sessao += 1
            mudancas.append("ativado" if ativo else "desativado")
    return mudancas


def definir_senha(sessoes: sessionmaker, utilizador_id: int, senha: str, trocar_senha: bool = False) -> None:
    """Grava uma senha nova e termina as outras sessões. Com trocar_senha, obriga a mudá-la ao entrar."""
    with sessoes.begin() as sessao:
        u = sessao.get(Utilizador, utilizador_id)
        if u is None:
            raise ValueError("Utilizador não encontrado.")
        validar_senha(senha, u.nome_utilizador)
        if check_password_hash(u.senha_hash, senha):
            raise ValueError("A senha nova tem de ser diferente da atual.")
        u.senha_hash = generate_password_hash(senha)
        u.trocar_senha = trocar_senha
        u.tentativas_falhadas, u.bloqueado_ate = 0, None
        u.versao_sessao += 1


def confirmar_senha(utilizador: Utilizador, senha: str) -> bool:
    return check_password_hash(utilizador.senha_hash, senha or "")


def autenticar(sessoes: sessionmaker, nome_utilizador: str, senha: str) -> ResultadoEntrada:
    """Verifica as credenciais. Depois de MAX_TENTATIVAS falhadas seguidas, a conta fica bloqueada uns minutos."""
    agora = _agora()
    with sessoes.begin() as sessao:
        u = sessao.scalar(select(Utilizador).where(Utilizador.nome_utilizador == normalizar_nome(nome_utilizador)))
        if u is None:
            check_password_hash(_HASH_FALSO, senha or "")
            return ResultadoEntrada(None, "credenciais")
        if u.bloqueado_ate is not None and u.bloqueado_ate > agora:
            check_password_hash(_HASH_FALSO, senha or "")
            return ResultadoEntrada(None, "bloqueado")
        if not check_password_hash(u.senha_hash, senha or ""):
            u.tentativas_falhadas += 1
            if u.tentativas_falhadas >= MAX_TENTATIVAS:
                u.bloqueado_ate, u.tentativas_falhadas = agora + TEMPO_BLOQUEIO, 0
                return ResultadoEntrada(None, "credenciais", bloqueado_agora=True)
            return ResultadoEntrada(None, "credenciais")
        if not u.ativo:
            return ResultadoEntrada(None, "inativo")
        u.tentativas_falhadas, u.bloqueado_ate, u.ultimo_acesso = 0, None, agora
        sessao.flush()
        sessao.expunge(u)
    return ResultadoEntrada(u)
