"""Tarefa 15: acesso do cliente (entrada, sessão e senha), separado do da equipa."""
from datetime import datetime, timedelta

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
    assert c.get("/cliente/senha").status_code == 200  # ainda autenticado como cliente
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
    assert c.get("/cliente/senha").status_code == 200  # ainda autenticado como cliente
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


def test_entrada_do_cliente_mantem_o_csrf_e_a_equipa(app, cli, acesso):
    """O token CSRF é partilhado: rodá-lo na entrada do cliente estragava os formulários da equipa abertos."""
    c = criar_cliente(app, "administrador")
    with c.session_transaction() as s:
        s["csrf_token"] = "antigo"
    _entrar(c, cli.codigo)
    with c.session_transaction() as s:
        assert s.get("csrf_token") == "antigo" and "uid" in s and s["cliente_id"] == cli.id
    assert "expirou" not in c.post("/utilizadores", data={"csrf_token": "antigo"}).get_data(as_text=True)


def test_saida_da_equipa_mantem_a_sessao_do_cliente(app, cli, acesso):
    c = criar_cliente(app, "administrador")
    _entrar(c, cli.codigo)
    c.post("/sair")
    with c.session_transaction() as s:
        assert "uid" not in s and "versao" not in s and "conta" not in s and s["cliente_id"] == cli.id
    assert c.get("/cliente/senha").status_code == 200  # ainda autenticado como cliente


def test_entrada_da_equipa_mantem_a_sessao_do_cliente(app, cli, acesso, sessoes):
    from detector import contas
    from tests.conftest import SENHA_TESTE
    contas.criar(sessoes, "chefe", "Chefe", "administrador", SENHA_TESTE)
    c = _browser(app)
    _entrar(c, cli.codigo)
    r = c.post("/entrar", data={"nome_utilizador": "chefe", "senha": SENHA_TESTE})
    assert r.status_code == 302
    with c.session_transaction() as s:
        assert "uid" in s and s["cliente_id"] == cli.id


def test_saida_do_cliente_mantem_o_csrf_da_equipa(app, cli, acesso):
    c = criar_cliente(app, "administrador")
    with c.session_transaction() as s:
        s["csrf_token"] = "antigo"
    _entrar(c, cli.codigo)
    c.post("/cliente/sair")
    with c.session_transaction() as s:
        assert s.get("csrf_token") == "antigo"


def test_area_do_cliente_funciona_sem_modelos(tmp_path, cli_sem_modelos=None):
    app = criar_app(tmp_path / "vazia")
    c = _browser(app)
    assert c.get("/cliente/entrar").status_code == 200


# ------------------------------------------------------------ Tarefa 16: saldo, movimentos e transferências
from pathlib import Path

from detector import autorizacao
from detector.contas_bancarias import verificar_saldos
from detector.auditoria import ACOES
from detector.configuracao import ler_configuracao

RAIZ = Path(__file__).resolve().parent.parent
NOVA_SENHA = "senha-nova-2"
DADOS_ANA = dict(nome="Ana Maria Sitoe", data_nascimento="1990-05-10", tipo_documento="NUIT",
                 numero_documento="123456789", telefone="841234567", email="", cidade="MAPUTO",
                 morada="Av. 24 de Julho 100")
DADOS_BRUNO = dict(DADOS_ANA, nome="Bruno Matsinhe Secreto", numero_documento="987654321", cidade="BEIRA")


def _forcar_nivel(monkeypatch, nivel, risco, capturar=None):
    def falso(artefatos, df, historico_extra=None, cortes=None):
        if capturar is not None:
            capturar.extend(df.to_dict("records"))
        r = df.copy().reset_index(drop=True)
        r["risco"], r["anomalia"], r["nivel"] = risco, 10.0, nivel
        r["motivos"] = [["teste"]] * len(r)
        r["historico_curto"] = False
        return r
    monkeypatch.setattr(autorizacao, "avaliar_transacoes", falso)


@pytest.fixture()
def outro(sessoes):
    return clientes.criar(sessoes, clientes.validar(DADOS_BRUNO), "teste")


def _sessao(app, cli):
    c = _browser(app)
    _entrar(c, cli.codigo)
    c.post("/cliente/senha", data={"senha_atual": SENHA, "senha_nova": NOVA_SENHA, "confirmacao": NOVA_SENHA})
    return c


def _saldos(sessoes, *clis):
    from detector.banco import ContaBancaria
    with sessoes() as s:
        contas = [s.get(ContaBancaria, c.conta.id) for c in clis]
        return [(x.saldo, x.saldo_retido) for x in contas]


def _depositar(sessoes, cli, centavos):
    contas_bancarias.depositar(sessoes, cli.conta.id, centavos, "balcao", "teste")


def test_inicio_mostra_saldos_e_movimentos_com_estado(app, sessoes, cli, outro, acesso, monkeypatch):
    _depositar(sessoes, cli, 100_000)
    c = _sessao(app, cli)
    _forcar_nivel(monkeypatch, "Normal", 5.0)
    c.post("/cliente/transferir", data={"conta_destino": outro.conta.numero, "valor": "100,00", "descricao": "renda"})
    _forcar_nivel(monkeypatch, "Alto risco", 90.0)
    c.post("/cliente/transferir", data={"conta_destino": outro.conta.numero, "valor": "200,00"})
    c.post("/cliente/transferir", data={"conta_destino": cli.conta.numero, "valor": "1,00"})
    html = c.get("/cliente/").get_data(as_text=True)
    assert "Concluída" in html and "Em verificação" in html and "Recusada — A conta de destino não pode" in html
    assert "900,00 MT" in html and "700,00 MT" in html and "200,00 MT" in html  # saldo, disponível, em verificação
    assert cli.conta.numero in html


def test_movimento_recebido_mostra_numero_e_nunca_o_nome(app, sessoes, cli, outro, acesso, monkeypatch):
    _depositar(sessoes, cli, 100_000)
    _forcar_nivel(monkeypatch, "Normal", 5.0)
    _sessao(app, cli).post("/cliente/transferir", data={"conta_destino": outro.conta.numero, "valor": "10,00"})
    acesso_cliente.criar_ou_redefinir(sessoes, outro.id, SENHA)
    html = _sessao(app, outro).get("/cliente/").get_data(as_text=True)
    assert "Recebida" in html and cli.conta.numero in html and "Ana Maria" not in html
    assert "Bruno" not in _sessao(app, cli).get("/cliente/").get_data(as_text=True)


def test_transferencia_normal_conclui_e_move_saldos(app, sessoes, cli, outro, acesso, monkeypatch):
    _depositar(sessoes, cli, 100_000)
    _forcar_nivel(monkeypatch, "Normal", 5.0)
    r = _sessao(app, cli).post("/cliente/transferir", data={"conta_destino": outro.conta.numero, "valor": "250,50"}, follow_redirects=True)
    assert r.status_code == 200 and "Concluída" in r.get_data(as_text=True)
    assert _saldos(sessoes, cli, outro) == [(74_950, 0), (25_050, 0)]
    assert verificar_saldos(sessoes) == []


def test_transferencia_retida_fica_em_verificacao(app, sessoes, cli, outro, acesso, monkeypatch):
    _depositar(sessoes, cli, 100_000)
    _forcar_nivel(monkeypatch, "Alto risco", 90.0)
    r = _sessao(app, cli).post("/cliente/transferir", data={"conta_destino": outro.conta.numero, "valor": "250,00"}, follow_redirects=True)
    assert "Em verificação" in r.get_data(as_text=True)
    assert _saldos(sessoes, cli, outro) == [(100_000, 25_000), (0, 0)]
    assert verificar_saldos(sessoes) == []


@pytest.mark.parametrize("destino,trecho", [("PROPRIA", "própria conta"), ("000000000", "não encontrada")])
def test_destino_proprio_ou_inexistente_e_recusado(app, sessoes, cli, acesso, monkeypatch, destino, trecho):
    _depositar(sessoes, cli, 100_000)
    _forcar_nivel(monkeypatch, "Normal", 5.0)
    numero = cli.conta.numero if destino == "PROPRIA" else destino
    r = _sessao(app, cli).post("/cliente/transferir", data={"conta_destino": numero, "valor": "10,00"}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Recusada" in html and trecho in html
    assert _saldos(sessoes, cli) == [(100_000, 0)]
    assert verificar_saldos(sessoes) == []


def test_valor_invalido_da_400_com_mensagem(app, cli, outro, acesso, monkeypatch):
    _forcar_nivel(monkeypatch, "Normal", 5.0)
    r = _sessao(app, cli).post("/cliente/transferir", data={"conta_destino": outro.conta.numero, "valor": "abc"})
    assert r.status_code == 400 and 'role="alert"' in r.get_data(as_text=True)


def test_movimento_proprio_abre_e_alheio_ou_inexistente_da_404_igual(app, sessoes, cli, outro, acesso):
    _depositar(sessoes, cli, 100_000)
    _depositar(sessoes, outro, 5_000)
    c = _sessao(app, cli)
    proprio = contas_bancarias.extrato(sessoes, cli.conta.id)[0].id
    alheio = contas_bancarias.extrato(sessoes, outro.conta.id)[0].id
    assert c.get(f"/cliente/movimentos/{proprio}").status_code == 200
    a, b = c.get(f"/cliente/movimentos/{alheio}"), c.get("/cliente/movimentos/99999")
    assert a.status_code == b.status_code == 404 and a.get_data() == b.get_data()
    assert "Banco Demonstração" in a.get_data(as_text=True)


def test_transferir_sem_modelos_da_503(tmp_path):
    app = criar_app(tmp_path / "vazia")
    s = app.config["SESSOES"]
    c0 = clientes.criar(s, clientes.validar(DADOS_ANA), "teste")
    acesso_cliente.criar_ou_redefinir(s, c0.id, SENHA)
    c = _sessao(app, c0)
    assert c.get("/cliente/transferir").status_code == 200
    r = c.post("/cliente/transferir", data={"conta_destino": "1", "valor": "1,00"})
    assert r.status_code == 503 and "modelos" in r.get_data(as_text=True)


def test_modo_demonstracao_mostra_e_usa_a_partir_de(app, sessoes, cli, outro, acesso, monkeypatch):
    _depositar(sessoes, cli, 100_000)
    visto = []
    _forcar_nivel(monkeypatch, "Normal", 5.0, visto)
    c = _sessao(app, cli)
    html = c.get("/cliente/transferir").get_data(as_text=True)
    assert 'name="local"' in html and 'name="data"' in html and "data-envio-unico" in html
    c.post("/cliente/transferir", data={"conta_destino": outro.conta.numero, "valor": "10,00", "local": "EXT:NG"})
    assert (visto[0]["cidade"], visto[0]["pais"]) == (cli.cidade, "NG")
    c.post("/cliente/transferir", data={"conta_destino": outro.conta.numero, "valor": "10,00", "local": "MZ:TETE"})
    assert (visto[1]["cidade"], visto[1]["pais"]) == ("TETE", "MZ")


def test_sem_modo_demonstracao_campos_ausentes_e_ignorados(pasta_pequena, monkeypatch):
    monkeypatch.setenv("MODO_DEMONSTRACAO", "0")
    app = criar_app(pasta_pequena)
    s = app.config["SESSOES"]
    a = clientes.criar(s, clientes.validar(DADOS_ANA), "teste")
    b = clientes.criar(s, clientes.validar(DADOS_BRUNO), "teste")
    acesso_cliente.criar_ou_redefinir(s, a.id, SENHA)
    _depositar(s, a, 100_000)
    visto = []
    _forcar_nivel(monkeypatch, "Normal", 5.0, visto)
    c = _sessao(app, a)
    html = c.get("/cliente/transferir").get_data(as_text=True)
    assert 'name="local"' not in html and 'name="data"' not in html
    c.post("/cliente/transferir", data={"conta_destino": b.conta.numero, "valor": "10,00", "local": "EXT:NG",
                                        "data": "2026-01-01", "hora": "03:00"})
    assert (visto[0]["cidade"], visto[0]["pais"]) == ("MAPUTO", "MZ")


def test_transferencia_e_auditada(app, sessoes, cli, outro, acesso, monkeypatch):
    _depositar(sessoes, cli, 100_000)
    _forcar_nivel(monkeypatch, "Normal", 5.0)
    _sessao(app, cli).post("/cliente/transferir", data={"conta_destino": outro.conta.numero, "valor": "10,00"})
    with sessoes() as s:
        e = s.scalars(select(EventoAuditoria).where(EventoAuditoria.acao == "transferencia_cliente")).one()
    assert e.nome_utilizador == f"cliente:{cli.codigo}" and e.alvo == cli.codigo
    assert outro.conta.numero in e.detalhe and "1000" in e.detalhe and "autorizado" in e.detalhe
    assert "transferencia_cliente" in ACOES


def test_erros_do_cliente_usam_a_marca_do_cliente(app, cli, acesso):
    c = _sessao(app, cli)
    csrf = c.post("/cliente/transferir", data={"csrf_token": "errado", "valor": "1"})
    nao = c.get("/cliente/movimentos/99999")
    for r, estado in ((csrf, 400), (nao, 404)):
        assert r.status_code == estado and "Detetor de Fraudes" not in r.get_data(as_text=True)
        assert "Banco Demonstração" in r.get_data(as_text=True)


def test_erro_500_do_cliente_usa_a_marca_do_cliente(app, cli, acesso, monkeypatch):
    c = _sessao(app, cli)
    monkeypatch.setattr("detector.web_cliente.contas_bancarias.extrato", lambda *a, **k: 1 / 0)
    r = c.get("/cliente/")
    assert r.status_code == 500 and "Detetor de Fraudes" not in r.get_data(as_text=True)
    assert "Banco Demonstração" in r.get_data(as_text=True)


def test_erro_da_equipa_mantem_a_marca_da_equipa(app):
    c = criar_cliente(app, "administrador")
    r = c.post("/utilizadores", data={"csrf_token": "errado"})
    assert r.status_code == 400 and "Banco Demonstração" not in r.get_data(as_text=True)


def test_envio_unico_partilhado_e_reativa_no_pageshow():
    js = (RAIZ / "static" / "envio_unico.js").read_text(encoding="utf-8")
    assert "pageshow" in js and "persisted" in js and "disabled = false" in js
    for t in ("balcao_cliente.html", "cliente_transferir.html"):
        texto = (RAIZ / "templates" / t).read_text(encoding="utf-8")
        assert "envio_unico.js" in texto and "dataset.enviado" not in texto


def test_modo_demonstracao_na_configuracao(monkeypatch):
    for nome in ("MODO_DEMONSTRACAO", "AMBIENTE"):
        monkeypatch.delenv(nome, raising=False)
    assert ler_configuracao(None).modo_demonstracao is True
    monkeypatch.setenv("AMBIENTE", "producao")
    monkeypatch.setenv("CHAVE_SECRETA", "x" * 40)
    assert ler_configuracao(None).modo_demonstracao is False
    monkeypatch.setenv("MODO_DEMONSTRACAO", "sim")
    assert ler_configuracao(None).modo_demonstracao is True
    monkeypatch.setenv("AMBIENTE", "desenvolvimento")
    monkeypatch.setenv("MODO_DEMONSTRACAO", "não")
    assert ler_configuracao(None).modo_demonstracao is False


def test_transferencia_redireciona_e_atualizar_nao_repete(app, sessoes, cli, outro, acesso, monkeypatch):
    _depositar(sessoes, cli, 100_000)
    _forcar_nivel(monkeypatch, "Normal", 5.0)
    c = _sessao(app, cli)
    r = c.post("/cliente/transferir", data={"conta_destino": outro.conta.numero, "valor": "10,00"})
    assert r.status_code == 303 and "/cliente/movimentos/" in r.headers["Location"]
    antes = len(contas_bancarias.extrato(sessoes, cli.conta.id))
    for _ in range(2):  # atualizar a página de destino é um GET
        seguida = c.get(r.headers["Location"])
        assert seguida.status_code == 200
    assert "Concluída" in seguida.get_data(as_text=True)
    assert len(contas_bancarias.extrato(sessoes, cli.conta.id)) == antes
    assert _saldos(sessoes, cli, outro) == [(99_000, 0), (1_000, 0)]
    assert verificar_saldos(sessoes) == []


# ---------------------------------------------------------------- pagamentos, local e data/hora simulada
def _movimentos(sessoes):
    from detector.banco import Movimento
    with sessoes() as s:
        return s.scalars(select(Movimento).order_by(Movimento.id)).all()


def test_pagamento_normal_autorizado(app, sessoes, cli, acesso, monkeypatch):
    _depositar(sessoes, cli, 100_000)
    _forcar_nivel(monkeypatch, "Normal", 5.0)
    c = _sessao(app, cli)
    r = c.post("/cliente/pagar", data={"categoria": "ELETRONICA", "comerciante": "Loja X", "valor": "150,00"})
    assert r.status_code == 303 and "/cliente/movimentos/" in r.headers["Location"]
    html = c.get(r.headers["Location"]).get_data(as_text=True)
    assert "Concluída" in html and "Loja X" in html
    m = _movimentos(sessoes)[-1]
    assert (m.tipo, m.estado, m.descricao) == ("compra", "autorizado", "Loja X")
    assert _saldos(sessoes, cli) == [(85_000, 0)]
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_pagamento_suspeito_fica_retido_com_local_e_hora(app, sessoes, cli, acesso, monkeypatch):
    _depositar(sessoes, cli, 1_000_000)
    visto = []
    _forcar_nivel(monkeypatch, "Alto risco", 80.0, visto)
    ontem = datetime.now() - timedelta(days=1)
    r = _sessao(app, cli).post("/cliente/pagar", data={
        "categoria": "ELETRONICA", "comerciante": "Loja X", "valor": "8000", "local": "EXT:NG",
        "data": f"{ontem:%Y-%m-%d}", "hora": "03:00"}, follow_redirects=True)
    assert "Em verificação" in r.get_data(as_text=True)
    assert (visto[0]["pais"], visto[0]["categoria"]) == ("NG", "ELETRONICA")
    m = _movimentos(sessoes)[-1]
    assert m.estado == "retido" and m.momento == ontem.replace(hour=3, minute=0, second=0, microsecond=0)


def test_pagamento_sem_saldo_e_conta_bloqueada(app, sessoes, cli, acesso, monkeypatch):
    _depositar(sessoes, cli, 1_000)
    _forcar_nivel(monkeypatch, "Normal", 5.0)
    c = _sessao(app, cli)
    dados = {"categoria": "MERCEARIA", "comerciante": "Mercado", "valor": "50,00"}
    assert "Recusada — Saldo insuficiente." in c.post("/cliente/pagar", data=dados, follow_redirects=True).get_data(as_text=True)
    contas_bancarias.bloquear(sessoes, cli.conta.id, True, "teste")
    dados["valor"] = "5,00"
    html = c.post("/cliente/pagar", data=dados, follow_redirects=True).get_data(as_text=True)
    assert "A sua conta está bloqueada" in html  # a sessão termina: o pagamento nem chega a ser pedido
    assert _saldos(sessoes, cli) == [(1_000, 0)]


@pytest.mark.parametrize("dados,erro", [
    ({"categoria": "BRINQUEDOS", "comerciante": "X", "valor": "10"}, "Escolha a categoria"),
    ({"categoria": "MERCEARIA", "comerciante": "", "valor": "10"}, "Indique o comerciante"),
    ({"categoria": "MERCEARIA", "comerciante": "x" * 141, "valor": "10"}, "Indique o comerciante"),
    ({"categoria": "MERCEARIA", "comerciante": "X", "valor": "10", "local": "EXT:US"}, "Escolha um local da lista."),
    ({"categoria": "MERCEARIA", "comerciante": "X", "valor": "10", "local": "MZ:LISBOA"}, "Escolha um local da lista."),
    ({"categoria": "MERCEARIA", "comerciante": "X", "valor": "10", "data": "2099-01-01"}, "não pode ser futura"),
    ({"categoria": "MERCEARIA", "comerciante": "X", "valor": "10", "data": "2020-01-01"}, "30 dias"),
    ({"categoria": "MERCEARIA", "comerciante": "X", "valor": "10", "data": "31/12/2026"}, "inválida"),
])
def test_pagamento_dados_invalidos_400(app, sessoes, cli, acesso, monkeypatch, dados, erro):
    _depositar(sessoes, cli, 100_000)
    _forcar_nivel(monkeypatch, "Normal", 5.0)
    r = _sessao(app, cli).post("/cliente/pagar", data=dados)
    assert r.status_code == 400 and erro in r.get_data(as_text=True)


def test_transferencia_local_invalido_400(app, sessoes, cli, outro, acesso, monkeypatch):
    _depositar(sessoes, cli, 100_000)
    _forcar_nivel(monkeypatch, "Normal", 5.0)
    r = _sessao(app, cli).post("/cliente/transferir", data={"conta_destino": outro.conta.numero, "valor": "10",
                                                            "local": "EXT:US"})
    assert r.status_code == 400 and "Escolha um local da lista." in r.get_data(as_text=True)


def test_inicio_tem_botoes_pagar_e_transferir(app, cli, acesso):
    html = _sessao(app, cli).get("/cliente/").get_data(as_text=True)
    assert 'href="/cliente/pagar"' in html and 'href="/cliente/transferir"' in html
