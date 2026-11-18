"""Controlo de acesso da interface: entrada e saída, sessões, perfis, proteção CSRF, cabeçalhos de segurança,
gestão de utilizadores (administrador) e consulta da auditoria (supervisor e administrador).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import date, timedelta
from functools import wraps
from logging import Logger
from urllib.parse import urlsplit

from flask import Flask, abort, current_app, g, redirect, render_template, request, session, url_for
from sqlalchemy.orm import sessionmaker

from . import auditoria, contas
from .banco import PERFIS

PUBLICOS = frozenset({"static", "saude", "entrar", "entrar_post"})
ENDPOINTS_BALCAO: set[str] = set()  # preenchido por web_balcao: as únicas páginas que o perfil Balcão abre
LIVRES_DURANTE_TROCA = frozenset({"senha", "senha_post", "sair"})  # quando a senha tem de ser mudada
METODOS_PROTEGIDOS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CAMPO_CSRF = "csrf_token"
MENSAGENS_ENTRADA = {
    "credenciais": "Nome de utilizador ou senha incorretos.",
    "inativo": "Esta conta está desativada. Fale com o administrador.",
    "bloqueado": "Conta bloqueada temporariamente por excesso de tentativas. Tente de novo mais tarde.",
}


# ------------------------------------------------------------------ utilitários usados pelas rotas
def utilizador_atual():
    return g.get("utilizador")


def auditar(acao: str, alvo: str | None = None, detalhe: str | None = None, *, nome_utilizador: str = "") -> None:
    auditoria.registar(current_app.config["SESSOES"], acao, utilizador_atual(), nome_utilizador=nome_utilizador,
                       alvo=alvo, detalhe=detalhe, ip=request.remote_addr)


def exige(perfil_minimo: str):
    """Só deixa passar quem tem pelo menos este perfil; os outros recebem 403 e a tentativa fica registada."""
    if perfil_minimo not in PERFIS:
        raise ValueError(perfil_minimo)

    def decorador(vista):
        @wraps(vista)
        def envolvida(*args, **kwargs):
            u = utilizador_atual()
            if u is None:
                return redirect(url_for("entrar"))
            if not u.pode(perfil_minimo):
                auditar("acesso_negado", alvo=request.path, detalhe=f"exige perfil {perfil_minimo}")
                return _negado()
            return vista(*args, **kwargs)
        return envolvida
    return decorador


def exige_area(area: str):
    """Só deixa passar quem tem acesso a esta área; os outros recebem 403 e a tentativa fica registada."""
    def decorador(vista):
        @wraps(vista)
        def envolvida(*args, **kwargs):
            u = utilizador_atual()
            if u is None:
                return redirect(url_for("entrar"))
            if not u.tem_area(area):
                auditar("acesso_negado", alvo=request.path, detalhe=f"exige a área {area}")
                return _negado()
            return vista(*args, **kwargs)
        return envolvida
    return decorador


def _negado():
    return render_template("erro.html", titulo="Acesso negado",
                           mensagem="O seu perfil não dá acesso a esta página."), 403


def impressao(u) -> str:
    """Identifica a conta concreta: o número da conta sozinho podia coincidir com o de outra instalação que use
    a mesma CHAVE_SECRETA. Inclui o hash da senha (com sal aleatório), por isso também muda com a senha."""
    texto = f"{u.id}:{u.nome_utilizador}:{u.criado_em.isoformat()}:{u.senha_hash}"
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:32]


def e_area_cliente(caminho: str) -> bool:
    """As páginas /cliente/ têm a sua própria sessão e o seu próprio filtro (detector/acesso_cliente.py)."""
    return caminho == "/cliente" or caminho.startswith("/cliente/")


def _guardar_na_sessao(u) -> None:
    session.update(uid=u.id, versao=u.versao_sessao, conta=impressao(u))


def token_csrf() -> str:
    if CAMPO_CSRF not in session:
        session[CAMPO_CSRF] = secrets.token_urlsafe(32)
    return session[CAMPO_CSRF]


def _destino_seguro(destino: str | None) -> str:
    """Só aceita caminhos deste site, para o 'próximo' do login não levar a outro endereço."""
    if not destino:
        return "/"
    partes = urlsplit(destino)
    if partes.scheme or partes.netloc or not destino.startswith("/") or destino.startswith("//") or "\\" in destino:
        return "/"
    return destino


def _data(texto: str | None) -> date | None:
    try:
        return date.fromisoformat(texto) if texto else None
    except ValueError:
        return None


# ------------------------------------------------------------------ instalação na aplicação
def instalar(app: Flask, sessoes: sessionmaker, registo: Logger, horas_sessao: int, producao: bool) -> None:
    app.config.update(
        SESSION_COOKIE_NAME="detetor_sessao",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=horas_sessao),
    )

    @app.context_processor
    def _contexto_acesso():
        return {"utilizador": utilizador_atual(), "csrf_token": token_csrf, "nomes_perfil": contas.NOMES_PERFIL}

    # A ordem importa: primeiro saber quem é, depois validar o formulário (o erro 413 do upload já tem utilizador).
    @app.before_request
    def _identificar():
        g.utilizador = None
        if request.path.startswith("/api/v1/"):  # a API autentica-se por chave (detector/api.py), nunca pela sessão
            return None
        if e_area_cliente(request.path):  # a área do cliente tem o seu próprio filtro
            return None
        uid = session.get("uid")
        if uid is not None:
            u = contas.obter(sessoes, uid)
            if (u is not None and u.ativo and u.versao_sessao == session.get("versao")
                    and hmac.compare_digest(str(session.get("conta", "")), impressao(u))):
                g.utilizador = u
            else:  # conta desativada, senha ou perfil mudados: a sessão deixa de valer
                session.clear()
        if request.endpoint in PUBLICOS:
            return None
        if g.utilizador is None:
            proximo = request.full_path.rstrip("?") if request.method == "GET" else None
            return redirect(url_for("entrar", proximo=proximo))
        if g.utilizador.trocar_senha and request.endpoint not in LIVRES_DURANTE_TROCA:
            return redirect(url_for("senha"))
        if g.utilizador.perfil == "balcao" and request.endpoint not in LIVRES_DURANTE_TROCA | ENDPOINTS_BALCAO:
            if request.path == "/" and request.method == "GET":
                return redirect("/balcao/clientes")
            auditar("acesso_negado", alvo=request.path, detalhe="o perfil Balcão só abre a sua área")
            return _negado()
        return None

    @app.before_request
    def _verificar_csrf():
        if request.method not in METODOS_PROTEGIDOS or request.path.startswith("/api/v1/"):  # sem cookies: sem CSRF
            return None
        enviado = request.form.get(CAMPO_CSRF) or request.headers.get("X-CSRF-Token") or ""
        esperado = session.get(CAMPO_CSRF, "")
        if not esperado or not hmac.compare_digest(enviado, esperado):
            registo.warning("Pedido %s %s recusado: token CSRF em falta ou inválido", request.method, request.path)
            return render_template("erro.html", titulo="O formulário expirou",
                                   mensagem="Por segurança, o pedido foi recusado. Recarregue a página e tente de novo."), 400
        return None

    @app.after_request
    def _cabecalhos(resposta):
        resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
        resposta.headers.setdefault("X-Frame-Options", "DENY")
        resposta.headers.setdefault("Referrer-Policy", "same-origin")
        resposta.headers.setdefault("Content-Security-Policy",
                                    "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                    "frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        if request.endpoint != "static":  # páginas com dados não ficam na cache do navegador depois de sair
            resposta.headers.setdefault("Cache-Control", "no-store")
        return resposta

    # ------------------------------------------------------------ entrada e saída
    def _pagina_entrada(erro=None, nome="", estado=200):
        return render_template("entrar.html", erro=erro, nome=nome, sem_contas=contas.contar(sessoes) == 0,
                               proximo=_destino_seguro(request.values.get("proximo"))), estado

    @app.get("/entrar")
    def entrar():
        if utilizador_atual() is not None:
            return redirect("/")
        return _pagina_entrada()

    @app.post("/entrar")
    def entrar_post():
        nome = contas.normalizar_nome(request.form.get("nome_utilizador", ""))[:50]
        resultado = contas.autenticar(sessoes, nome, request.form.get("senha", ""))
        if resultado.utilizador is None:
            g.utilizador = None
            auditar("entrada_falhada", alvo=nome or None, detalhe=resultado.motivo, nome_utilizador=nome)
            if resultado.bloqueado_agora:
                auditar("conta_bloqueada", alvo=nome, nome_utilizador=nome,
                        detalhe=f"{contas.MAX_TENTATIVAS} tentativas falhadas seguidas")
                registo.warning("Conta '%s' bloqueada por tentativas falhadas (IP %s)", nome, request.remote_addr)
            return _pagina_entrada(MENSAGENS_ENTRADA[resultado.motivo], nome, 401)
        u = resultado.utilizador
        session.clear()  # sessão nova: um identificador antigo não serve depois de entrar
        session.permanent = True
        _guardar_na_sessao(u)
        g.utilizador = u
        auditar("entrada")
        registo.info("Entrada de '%s' (%s)", u.nome_utilizador, u.perfil)
        return redirect(_destino_seguro(request.form.get("proximo")))

    @app.post("/sair")
    def sair():
        auditar("saida")
        session.clear()
        return redirect(url_for("entrar"))

    @app.get("/conta/senha")
    def senha():
        return render_template("senha.html", erro=None, sucesso=False)

    @app.post("/conta/senha")
    def senha_post():
        u = utilizador_atual()
        atual, nova = request.form.get("senha_atual", ""), request.form.get("senha_nova", "")
        erro = None
        if not contas.confirmar_senha(u, atual):
            erro = "A senha atual não está correta."
        elif nova != request.form.get("confirmacao", ""):
            erro = "A confirmação não é igual à senha nova."
        else:
            try:
                contas.definir_senha(sessoes, u.id, nova)
            except ValueError as exc:
                erro = str(exc)
        if erro:
            return render_template("senha.html", erro=erro, sucesso=False), 400
        auditar("senha_alterada", alvo=u.nome_utilizador)
        _guardar_na_sessao(contas.obter(sessoes, u.id))  # esta sessão continua; as outras terminam
        g.utilizador = contas.obter(sessoes, u.id)
        return render_template("senha.html", erro=None, sucesso=True)

    # ------------------------------------------------------------ gestão de utilizadores
    def _lista_utilizadores(erro=None, valores=None, estado=200, criado=None):
        return render_template("utilizadores.html", utilizadores=contas.listar(sessoes), perfis=PERFIS,
                               erro=erro, valores=valores or {}, criado=criado), estado

    @app.get("/utilizadores")
    @exige("administrador")
    def utilizadores():
        return _lista_utilizadores()

    @app.post("/utilizadores")
    @exige("administrador")
    def utilizadores_post():
        valores = {c: request.form.get(c, "").strip() for c in ("nome_utilizador", "nome", "perfil")}
        try:
            novo = contas.criar(sessoes, valores["nome_utilizador"], valores["nome"], valores["perfil"],
                                request.form.get("senha", ""), trocar_senha=True)
        except ValueError as exc:
            return _lista_utilizadores(str(exc), valores, 400)
        auditar("utilizador_criado", alvo=novo.nome_utilizador, detalhe=f"perfil {novo.perfil}")
        return _lista_utilizadores(criado=novo.nome_utilizador)

    def _ficha(alvo, erro=None, mensagem=None, estado=200):
        return render_template("utilizador.html", alvo=alvo, perfis=PERFIS, erro=erro, mensagem=mensagem), estado

    def _alvo(utilizador_id: int):
        alvo = contas.obter(sessoes, utilizador_id)
        if alvo is None:
            abort(404)
        return alvo

    @app.get("/utilizadores/<int:utilizador_id>")
    @exige("administrador")
    def utilizador(utilizador_id):
        return _ficha(_alvo(utilizador_id))

    @app.post("/utilizadores/<int:utilizador_id>")
    @exige("administrador")
    def utilizador_post(utilizador_id):
        alvo = _alvo(utilizador_id)
        ativo = request.form.get("ativo") == "1"
        if alvo.id == utilizador_atual().id and not ativo:
            return _ficha(alvo, "Não pode desativar a sua própria conta.", estado=400)
        try:
            mudancas = contas.alterar(sessoes, alvo.id, nome=request.form.get("nome"),
                                      perfil=request.form.get("perfil"), ativo=ativo)
        except ValueError as exc:
            return _ficha(alvo, str(exc), estado=400)
        if mudancas:
            auditar("utilizador_alterado", alvo=alvo.nome_utilizador, detalhe="; ".join(mudancas))
        if alvo.id == utilizador_atual().id:  # o próprio administrador mudou o seu perfil: mantém a sessão
            _guardar_na_sessao(contas.obter(sessoes, alvo.id))
        return _ficha(contas.obter(sessoes, alvo.id), mensagem="Alterações gravadas." if mudancas else "Nada mudou.")

    @app.post("/utilizadores/<int:utilizador_id>/senha")
    @exige("administrador")
    def utilizador_senha(utilizador_id):
        alvo = _alvo(utilizador_id)
        try:
            contas.definir_senha(sessoes, alvo.id, request.form.get("senha", ""), trocar_senha=True)
        except ValueError as exc:
            return _ficha(alvo, str(exc), estado=400)
        auditar("senha_redefinida", alvo=alvo.nome_utilizador)
        if alvo.id == utilizador_atual().id:
            _guardar_na_sessao(contas.obter(sessoes, alvo.id))
        return _ficha(contas.obter(sessoes, alvo.id),
                      mensagem="Senha redefinida. O utilizador terá de a mudar ao entrar.")

    # ------------------------------------------------------------ auditoria
    @app.get("/auditoria")
    @exige("supervisor")
    def ver_auditoria():
        a = request.args
        filtro = auditoria.Filtro(utilizador=a.get("utilizador", "").strip()[:50],
                                  acao=a.get("acao", "") if a.get("acao") in auditoria.ACOES else "",
                                  alvo=a.get("alvo", "").strip()[:255], desde=_data(a.get("desde")),
                                  ate=_data(a.get("ate")))
        pagina = a.get("pagina", "1")
        pagina = int(pagina) if pagina.isdigit() and int(pagina) > 0 else 1
        eventos, total = auditoria.listar(sessoes, filtro, pagina)
        paginas = max(1, -(-total // auditoria.POR_PAGINA))
        consulta = {k: v for k, v in a.items() if k != "pagina" and v}
        return render_template("auditoria.html", eventos=eventos, total=total, pagina=pagina, paginas=paginas,
                               filtro=filtro, acoes=auditoria.ACOES, nomes=auditoria.nomes_registados(sessoes),
                               consulta=consulta)
