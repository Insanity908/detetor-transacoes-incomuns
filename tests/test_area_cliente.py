"""Tarefa 15: acesso do cliente (entrada, sessão e senha), separado do da equipa."""
import pytest
from sqlalchemy import select

from app import criar_app
from detector import acesso_cliente, clientes, contas_bancarias
from detector.banco import AcessoCliente, EventoAuditoria
from tests.conftest import ClienteComCsrf, criar_cliente

SENHA = "senha-do-cliente-1"


@pytest.fixture()
def app(pasta_pequena):
    return criar_app(pasta_pequena)


@pytest.fixture()
def sessoes(app):
    return app.config["SESSOES"]


@pytest.fixture()
def cli(sessoes):
    dados = clientes.validar(dict(nome="Ana Maria Sitoe", data_nascimento="1990-05-10", tipo_documento="NUIT",
                                  numero_documento="123456789", telefone="841234567", email="",
                                  cidade="MAPUTO", morada="Av. 24 de Julho 100"))
    return clientes.criar(sessoes, dados, "teste")


def _browser(app):
    app.test_client_class = ClienteComCsrf
    return app.test_client()


def _entrar(browser, codigo, senha=SENHA):
    return browser.post("/cliente/entrar", data={"codigo": codigo, "senha": senha})


@pytest.fixture()
def acesso(sessoes, cli):
    acesso_cliente.criar_ou_redefinir(sessoes, cli.id, SENHA)


def _acoes(sessoes):
    with sessoes() as s:
        return [(e.acao, e.nome_utilizador, e.utilizador_id, e.alvo) for e in s.scalars(select(EventoAuditoria))]


def test_balcao_cria_acesso_e_cliente_e_mandado_mudar_a_senha(app, sessoes, cli):
    balcao = criar_cliente(app, "balcao")
    r = balcao.post(f"/balcao/clientes/{cli.id}/acesso", data={"senha": SENHA})
    assert r.status_code == 302
    assert ("acesso_cliente_criado", "teste.balcao", balcao.utilizador.id, cli.codigo) in _acoes(sessoes)
    c = _browser(app)
    r = _entrar(c, cli.codigo)
    assert r.status_code == 302 and r.headers["Location"].endswith("/cliente/senha")
    assert c.get("/cliente/").headers["Location"].endswith("/cliente/senha")
    assert ("entrada_cliente", f"cliente:{cli.codigo}", None, cli.codigo) in _acoes(sessoes)


def test_balcao_sem_senha_nao_cria_acesso(app, sessoes, cli):
    balcao = criar_cliente(app, "balcao")
    r = balcao.post(f"/balcao/clientes/{cli.id}/acesso", data={"senha": ""})
    assert r.status_code == 400
    with sessoes() as s:
        assert s.get(AcessoCliente, cli.id) is None


def test_redefinir_regista_acao_propria(app, sessoes, cli, acesso):
    balcao = criar_cliente(app, "balcao")
    balcao.post(f"/balcao/clientes/{cli.id}/acesso", data={"senha": "outra"})
    assert any(a[0] == "senha_cliente_redefinida" for a in _acoes(sessoes))


def test_mudar_senha_e_entrar_na_area(app, sessoes, cli, acesso):
    c = _browser(app)
    _entrar(c, cli.codigo)
    r = c.post("/cliente/senha", data={"senha_atual": SENHA, "senha_nova": "nova", "confirmacao": "diferente"})
    assert r.status_code == 400
    r = c.post("/cliente/senha", data={"senha_atual": "errada", "senha_nova": "nova", "confirmacao": "nova"})
    assert r.status_code == 400
    r = c.post("/cliente/senha", data={"senha_atual": SENHA, "senha_nova": "nova", "confirmacao": "nova"})
    assert r.status_code == 200
    assert c.get("/cliente/").status_code == 200
    out = _browser(app)
    assert _entrar(out, cli.codigo, "nova").headers["Location"].endswith("/cliente/")


def test_sem_regras_de_forca_mas_senha_vazia_recusada(app, sessoes, cli, acesso):
    c = _browser(app)
    _entrar(c, cli.codigo)
    r = c.post("/cliente/senha", data={"senha_atual": SENHA, "senha_nova": "", "confirmacao": ""})
    assert r.status_code == 400 and "Escreva uma senha." in r.get_data(as_text=True)


def test_cinco_senhas_erradas_bloqueiam(app, sessoes, cli, acesso):
    c = _browser(app)
    for _ in range(5):
        assert _entrar(c, cli.codigo, "errada").status_code == 401
    r = _entrar(c, cli.codigo, SENHA)
    assert r.status_code == 401 and "bloqueado" in r.get_data(as_text=True)
    nomes = [a[0] for a in _acoes(sessoes)]
    assert nomes.count("entrada_cliente_falhada") >= 5 and "acesso_cliente_bloqueado" in nomes


def test_mensagem_igual_para_codigo_inexistente(app, cli, acesso):
    c = _browser(app)
    a = _entrar(c, cli.codigo, "errada").get_data(as_text=True)
    b = _entrar(c, "C9999", "errada").get_data(as_text=True)
    assert "Código de cliente ou senha incorretos." in a and "Código de cliente ou senha incorretos." in b


def test_sessao_da_equipa_nao_abre_area_do_cliente(app):
    adm = criar_cliente(app, "administrador")
    r = adm.get("/cliente/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/cliente/entrar")


def test_sessao_do_cliente_nao_abre_paginas_da_equipa(app, cli, acesso):
    c = _browser(app)
    _entrar(c, cli.codigo)
    r = c.get("/alertas")
    assert r.status_code == 302 and "/entrar" in r.headers["Location"] and "/cliente" not in r.headers["Location"]


def test_pagina_de_entrada_do_cliente_nao_vai_para_entrar_da_equipa(app):
    c = _browser(app)
    assert c.get("/cliente/entrar").status_code == 200


def test_post_sem_csrf_recusado(app, cli, acesso):
    c = _browser(app)
    assert c.post("/cliente/entrar", data={"codigo": cli.codigo, "senha": SENHA, "csrf_token": ""}).status_code == 400
    _entrar(c, cli.codigo)
    c.post("/cliente/senha", data={"senha_atual": SENHA, "senha_nova": "n", "confirmacao": "n"})
    assert c.post("/cliente/sair", data={"csrf_token": "errado"}).status_code == 400


def test_redefinir_termina_a_sessao_aberta(app, sessoes, cli, acesso):
    c = _browser(app)
    _entrar(c, cli.codigo)
    c.post("/cliente/senha", data={"senha_atual": SENHA, "senha_nova": "n", "confirmacao": "n"})
    assert c.get("/cliente/").status_code == 200
    acesso_cliente.criar_ou_redefinir(sessoes, cli.id, "outra")
    r = c.get("/cliente/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/cliente/entrar")


def test_conta_bloqueada_termina_a_sessao_e_impede_entrada(app, sessoes, cli, acesso):
    c = _browser(app)
    _entrar(c, cli.codigo)
    c.post("/cliente/senha", data={"senha_atual": SENHA, "senha_nova": "n", "confirmacao": "n"})
    contas_bancarias.bloquear(sessoes, cli.conta.id, True, "teste")
    assert c.get("/cliente/").headers["Location"].endswith("/cliente/entrar")
    r = _entrar(_browser(app), cli.codigo, "n")
    assert r.status_code == 401


def test_sair_limpa_so_a_sessao_do_cliente(app, sessoes, cli, acesso):
    c = criar_cliente(app, "administrador")
    _entrar(c, cli.codigo)
    r = c.post("/cliente/sair")
    assert r.headers["Location"].endswith("/cliente/entrar")
    with c.session_transaction() as s:
        assert "uid" in s and "cliente_id" not in s
    assert ("saida_cliente", f"cliente:{cli.codigo}", None, cli.codigo) in _acoes(sessoes)


def test_entrada_do_cliente_roda_o_csrf_e_mantem_a_equipa(app, cli, acesso):
    c = criar_cliente(app, "administrador")
    with c.session_transaction() as s:
        s["csrf_token"] = "antigo"
    _entrar(c, cli.codigo)
    with c.session_transaction() as s:
        assert s.get("csrf_token") != "antigo" and "uid" in s and s["cliente_id"] == cli.id


def test_area_do_cliente_funciona_sem_modelos(tmp_path, cli_sem_modelos=None):
    app = criar_app(tmp_path / "vazia")
    c = _browser(app)
    assert c.get("/cliente/entrar").status_code == 200
