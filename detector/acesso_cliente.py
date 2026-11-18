"""Acesso do cliente à sua área (/cliente/): senha, entrada com bloqueio e sessão própria, separada da da equipa."""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from logging import Logger

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from werkzeug.security import check_password_hash, generate_password_hash

from . import acesso, clientes
from .banco import AcessoCliente, Cliente
from .contas import MAX_TENTATIVAS, TEMPO_BLOQUEIO, validar_senha

CHAVES_SESSAO = ("cliente_id", "cliente_versao", "cliente_conta")
ENDPOINTS = frozenset({"cliente_inicio", "cliente_entrar", "cliente_entrar_post", "cliente_sair", "cliente_senha",
                       "cliente_senha_post"})
PUBLICOS = frozenset({"cliente_entrar", "cliente_entrar_post"})
LIVRES_DURANTE_TROCA = frozenset({"cliente_senha", "cliente_senha_post", "cliente_sair"})
MENSAGENS = {
    "credenciais": "Código de cliente ou senha incorretos.",
    "bloqueado": "Acesso bloqueado temporariamente por excesso de tentativas. Tente de novo mais tarde.",
    "conta_bloqueada": "A sua conta está bloqueada. Dirija-se ao balcão.",
}
_HASH_FALSO = generate_password_hash("senha-que-nunca-confere")


class ErroEntrada(ValueError):
    def __init__(self, motivo: str, bloqueado_agora: bool = False):
        super().__init__(MENSAGENS[motivo])
        self.motivo, self.bloqueado_agora = motivo, bloqueado_agora


def _agora() -> datetime:
    return datetime.now().replace(microsecond=0)


def criar_ou_redefinir(sessoes: sessionmaker, cliente_id: int, senha: str) -> bool:
    """Cria o acesso ou redefine a senha (obriga a mudá-la ao entrar e termina as sessões abertas).
    Devolve True se o acesso foi criado agora."""
    validar_senha(senha)
    with sessoes.begin() as sessao:
        if sessao.get(Cliente, cliente_id) is None:
            raise ValueError("Cliente não encontrado.")
        a = sessao.get(AcessoCliente, cliente_id)
        novo = a is None
        if novo:
            a = AcessoCliente(cliente_id=cliente_id, versao_sessao=0)
            sessao.add(a)
        a.senha_hash, a.trocar_senha = generate_password_hash(senha), True
        a.tentativas_falhadas, a.bloqueado_ate = 0, None
        a.versao_sessao = (a.versao_sessao or 0) + 1
    return novo


def obter_acesso(sessoes: sessionmaker, cliente_id: int) -> AcessoCliente | None:
    with sessoes() as sessao:
        return sessao.get(AcessoCliente, cliente_id)


def impressao(cliente: Cliente, a: AcessoCliente) -> str:
    """Identifica o acesso concreto (inclui o hash da senha, que muda com a senha)."""
    texto = f"cliente:{cliente.id}:{cliente.codigo}:{a.senha_hash}"
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:32]


def entrar(sessoes: sessionmaker, codigo: str, senha: str) -> Cliente:
    """Verifica o código e a senha. Depois de MAX_TENTATIVAS falhadas seguidas, bloqueia TEMPO_BLOQUEIO."""
    agora = _agora()
    codigo = (codigo or "").strip().upper()
    erro: ErroEntrada | None = None
    with sessoes.begin() as sessao:
        cliente = sessao.scalar(select(Cliente).where(Cliente.codigo == codigo))
        a = sessao.get(AcessoCliente, cliente.id) if cliente else None
        if a is None:
            check_password_hash(_HASH_FALSO, senha or "")
            erro = ErroEntrada("credenciais")
        elif a.bloqueado_ate is not None and a.bloqueado_ate > agora:
            check_password_hash(_HASH_FALSO, senha or "")
            erro = ErroEntrada("bloqueado")
        elif not check_password_hash(a.senha_hash, senha or ""):
            a.tentativas_falhadas += 1
            bloqueado_agora = a.tentativas_falhadas >= MAX_TENTATIVAS
            if bloqueado_agora:
                a.bloqueado_ate, a.tentativas_falhadas = agora + TEMPO_BLOQUEIO, 0
            erro = ErroEntrada("credenciais", bloqueado_agora)  # levantado depois do commit: a falha fica gravada
        elif cliente.conta is not None and cliente.conta.estado == "bloqueada":
            erro = ErroEntrada("conta_bloqueada")
        else:
            a.tentativas_falhadas, a.bloqueado_ate, a.ultimo_acesso = 0, None, agora
            sessao.flush()
            _ = cliente.conta
            sessao.expunge_all()
    if erro is not None:
        raise erro
    return cliente


def mudar_senha(sessoes: sessionmaker, cliente_id: int, atual: str, nova: str, confirmacao: str) -> None:
    """Muda a senha do cliente (pede a atual e a confirmação) e termina as outras sessões."""
    with sessoes.begin() as sessao:
        a = sessao.get(AcessoCliente, cliente_id)
        if a is None:
            raise ValueError("Acesso não encontrado.")
        if not check_password_hash(a.senha_hash, atual or ""):
            raise ValueError("A senha atual não está correta.")
        if nova != confirmacao:
            raise ValueError("A confirmação não é igual à senha nova.")
        validar_senha(nova)
        a.senha_hash, a.trocar_senha = generate_password_hash(nova), False
        a.tentativas_falhadas, a.bloqueado_ate = 0, None
        a.versao_sessao += 1


# ------------------------------------------------------------------ instalação
def _auditar(acao: str, codigo: str, detalhe: str | None = None) -> None:
    acesso.auditar(acao, alvo=codigo or None, detalhe=detalhe, nome_utilizador=f"cliente:{codigo}" if codigo else "—")


def _guardar(c: Cliente, a: AcessoCliente) -> None:
    session.update(cliente_id=c.id, cliente_versao=a.versao_sessao, cliente_conta=impressao(c, a))


def _limpar() -> None:
    for chave in CHAVES_SESSAO:
        session.pop(chave, None)


def instalar(app: Flask, sessoes: sessionmaker, registo: Logger) -> None:
    @app.context_processor
    def _contexto_cliente():
        return {"cliente_atual": g.get("cliente"), "acesso_atual": g.get("acesso_cliente")}

    @app.before_request
    def _identificar_cliente():
        g.cliente = g.acesso_cliente = None
        if not acesso.e_area_cliente(request.path):
            return None
        cid = session.get("cliente_id")
        if cid is not None:
            c, a = clientes.obter(sessoes, cid), obter_acesso(sessoes, cid)
            if c is None or a is None or a.versao_sessao != session.get("cliente_versao") or not hmac.compare_digest(
                    str(session.get("cliente_conta", "")), impressao(c, a)):
                _limpar()
            elif c.conta is not None and c.conta.estado == "bloqueada":
                _limpar()
                flash(MENSAGENS["conta_bloqueada"], "erro")
            else:
                g.cliente, g.acesso_cliente = c, a
        if request.endpoint in PUBLICOS:
            return None
        if g.cliente is None:
            return redirect(url_for("cliente_entrar"))
        if g.acesso_cliente.trocar_senha and request.endpoint not in LIVRES_DURANTE_TROCA:
            return redirect(url_for("cliente_senha"))
        return None

    def _pagina_entrada(erro=None, codigo="", estado=200):
        return render_template("cliente_entrar.html", erro=erro, codigo=codigo), estado

    @app.get("/cliente/")
    def cliente_inicio():
        return render_template("cliente_inicio.html")

    @app.get("/cliente/entrar")
    def cliente_entrar():
        if g.cliente is not None:
            return redirect(url_for("cliente_inicio"))
        return _pagina_entrada()

    @app.post("/cliente/entrar")
    def cliente_entrar_post():
        codigo = request.form.get("codigo", "").strip().upper()[:10]
        try:
            c = entrar(sessoes, codigo, request.form.get("senha", ""))
        except ErroEntrada as exc:
            _auditar("entrada_cliente_falhada", codigo, exc.motivo)
            if exc.bloqueado_agora:
                _auditar("acesso_cliente_bloqueado", codigo, f"{MAX_TENTATIVAS} tentativas falhadas seguidas")
                registo.warning("Acesso do cliente '%s' bloqueado por tentativas falhadas (IP %s)", codigo,
                                request.remote_addr)
            return _pagina_entrada(str(exc), codigo, 401)
        _limpar()  # sessão nova só do cliente: não apaga a sessão da equipa no mesmo navegador
        session.pop(acesso.CAMPO_CSRF, None)
        session.permanent = True
        a = obter_acesso(sessoes, c.id)
        _guardar(c, a)
        _auditar("entrada_cliente", c.codigo)
        return redirect(url_for("cliente_senha" if a.trocar_senha else "cliente_inicio"))

    @app.post("/cliente/sair")
    def cliente_sair():
        _auditar("saida_cliente", g.cliente.codigo)
        _limpar()
        session.pop(acesso.CAMPO_CSRF, None)
        return redirect(url_for("cliente_entrar"))

    @app.get("/cliente/senha")
    def cliente_senha():
        return render_template("cliente_senha.html", erro=None, sucesso=False)

    @app.post("/cliente/senha")
    def cliente_senha_post():
        c = g.cliente
        f = request.form
        try:
            mudar_senha(sessoes, c.id, f.get("senha_atual", ""), f.get("senha_nova", ""), f.get("confirmacao", ""))
        except ValueError as exc:
            return render_template("cliente_senha.html", erro=str(exc), sucesso=False), 400
        a = obter_acesso(sessoes, c.id)
        _guardar(c, a)  # esta sessão continua; as outras terminam
        g.acesso_cliente = a
        _auditar("senha_cliente_alterada", c.codigo)
        return render_template("cliente_senha.html", erro=None, sucesso=True)
