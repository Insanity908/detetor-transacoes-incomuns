"""Fase 2: entrada no sistema, perfis, CSRF, auditoria e integridade dos modelos."""
import io
import json
import re

import pytest

from app import criar_app
from detector import auditoria, contas
from detector.artefatos import carregar, gravar, verificar_integridade
from tests.conftest import CHAVE_TESTE, SENHA_TESTE, criar_cliente

CSV = ("cliente,data_hora,valor,cidade,pais,categoria\n"
       "C0001,2026-05-01 14:30,150,Maputo,MZ,MERCEARIA\nC0001,2026-05-01 15:00,90000,Maputo,MZ,MERCEARIA\n")
FORM = dict(cliente="C0001", data_hora="2026-05-01T14:30", valor="150", cidade="Maputo", pais="MZ",
            categoria="MERCEARIA")


@pytest.fixture()
def app(pasta_pequena):
    return criar_app(pasta_pequena)


def _sessoes(app):
    return app.config["SESSOES"]


def _entrar(cliente, nome, senha=SENHA_TESTE, **extra):
    return cliente.post("/entrar", data={"nome_utilizador": nome, "senha": senha, **extra})


def _enviar(cliente, nome="lote.csv"):
    return cliente.post("/analisar", data={"ficheiro": (io.BytesIO(CSV.encode()), nome)},
                        content_type="multipart/form-data")


def _acoes(app, **filtro):
    eventos, _ = auditoria.listar(_sessoes(app), auditoria.Filtro(**filtro), por_pagina=500)
    return [(e.nome_utilizador, e.acao, e.alvo) for e in eventos]


# ------------------------------------------------------------ nada abre sem entrar
@pytest.mark.parametrize("caminho", ["/", "/analisar", "/perfil?cliente=C0001", "/modelos", "/auditoria",
                                     "/utilizadores", "/utilizadores/1", "/conta/senha", "/graficos/comparacao.png",
                                     "/analisar/abc", "/analisar/abc.csv", "/nao-existe"])
def test_paginas_pedem_entrada(app, caminho):
    r = criar_cliente(app, None).get(caminho)
    assert r.status_code == 302 and r.headers["Location"].startswith("/entrar")


def test_post_sem_sessao_nao_avalia_nada(app):
    r = criar_cliente(app, None).post("/", data=FORM)
    assert r.status_code == 302 and "/entrar" in r.headers["Location"]
    assert _acoes(app, acao="testar_transacao") == []


def test_saude_e_entrada_sao_publicas(app):
    cliente = criar_cliente(app, None)
    assert cliente.get("/saude").status_code == 200
    html = cliente.get("/entrar").get_data(as_text=True)
    assert 'name="senha"' in html and 'name="csrf_token"' in html and "Testar transação" not in html


def test_sem_contas_explica_como_criar_o_administrador(app):
    assert "gerir.py criar-admin" in criar_cliente(app, None).get("/entrar").get_data(as_text=True)


# ------------------------------------------------------------ entrar e sair
def test_entrada_valida_leva_ao_destino_pedido(app):
    contas.criar(_sessoes(app), "ana", "Ana Silva", "analista", SENHA_TESTE)
    cliente = criar_cliente(app, None)
    proximo = re.search(r'name="proximo" value="([^"]*)"',
                        cliente.get("/perfil?cliente=C0001", follow_redirects=True).get_data(as_text=True)).group(1)
    assert proximo == "/perfil?cliente=C0001"
    r = _entrar(cliente, " ANA ", proximo=proximo)
    assert r.status_code == 302 and r.headers["Location"] == "/perfil?cliente=C0001"
    html = cliente.get("/").get_data(as_text=True)
    assert "Ana Silva" in html and "Analista" in html
    assert ("ana", "entrada", None) in _acoes(app)
    assert contas.obter_por_nome(_sessoes(app), "ana").ultimo_acesso is not None


def test_cookie_de_sessao_protegido(app):
    contas.criar(_sessoes(app), "ana", "Ana", "analista", SENHA_TESTE)
    cookie = _entrar(criar_cliente(app, None), "ana").headers["Set-Cookie"]
    assert "detetor_sessao=" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie


@pytest.mark.parametrize("destino", ["//exemplo.com/x", "https://exemplo.com", "/\\exemplo.com", "javascript:x"])
def test_destino_externo_e_ignorado(app, destino):
    contas.criar(_sessoes(app), "ana", "Ana", "analista", SENHA_TESTE)
    assert _entrar(criar_cliente(app, None), "ana", proximo=destino).headers["Location"] == "/"


@pytest.mark.parametrize("nome,senha", [("ana", "senha-errada-999"), ("ninguem", SENHA_TESTE)])
def test_credenciais_erradas_dao_a_mesma_mensagem(app, nome, senha):
    contas.criar(_sessoes(app), "ana", "Ana", "analista", SENHA_TESTE)
    r = _entrar(criar_cliente(app, None), nome, senha)
    assert r.status_code == 401 and "Nome de utilizador ou senha incorretos." in r.get_data(as_text=True)
    assert (nome, "entrada_falhada", nome) in _acoes(app)


def test_conta_bloqueia_depois_de_cinco_tentativas(app):
    contas.criar(_sessoes(app), "ana", "Ana", "analista", SENHA_TESTE)
    cliente = criar_cliente(app, None)
    for _ in range(contas.MAX_TENTATIVAS):
        _entrar(cliente, "ana", "errada-errada")
    r = _entrar(cliente, "ana")  # a senha certa já não chega
    assert r.status_code == 401 and "bloqueada" in r.get_data(as_text=True)
    assert ("ana", "conta_bloqueada", "ana") in _acoes(app)
    import gerir
    assert gerir.desbloquear("ana") == 0
    assert _entrar(cliente, "ana").status_code == 302


def test_conta_desativada_nao_entra(app):
    u = contas.criar(_sessoes(app), "ana", "Ana", "analista", SENHA_TESTE)
    contas.alterar(_sessoes(app), u.id, ativo=False)
    r = _entrar(criar_cliente(app, None), "ana")
    assert r.status_code == 401 and "desativada" in r.get_data(as_text=True)


def test_senha_guardada_so_como_hash(app):
    u = contas.criar(_sessoes(app), "ana", "Ana", "analista", SENHA_TESTE)
    assert SENHA_TESTE not in u.senha_hash and u.senha_hash.startswith(("scrypt:", "pbkdf2:"))


def test_sair_termina_a_sessao(cliente):
    assert cliente.get("/sair").status_code == 405  # só por POST, com token: um link não faz sair ninguém
    r = cliente.post("/sair")
    assert r.status_code == 302 and cliente.get("/").status_code == 302
    assert ("teste.administrador", "saida", None) in _acoes(cliente.application)


# ------------------------------------------------------------ CSRF
@pytest.mark.parametrize("token", ["", "token-falso"])
def test_post_sem_token_valido_e_recusado(cliente, token):
    r = cliente.post("/", data=FORM | {"csrf_token": token})
    assert r.status_code == 400 and "O formulário expirou" in r.get_data(as_text=True)
    assert _acoes(cliente.application, acao="testar_transacao") == []


def test_entrada_tambem_exige_token(app):
    contas.criar(_sessoes(app), "ana", "Ana", "analista", SENHA_TESTE)
    assert _entrar(criar_cliente(app, None), "ana", csrf_token="").status_code == 400


def test_todos_os_formularios_post_levam_o_token(cliente):
    paginas = ["/", "/analisar", "/utilizadores", f"/utilizadores/{cliente.utilizador.id}", "/conta/senha"]
    for caminho in paginas:
        html = cliente.get(caminho).get_data(as_text=True)
        formularios = re.findall(r'<form method="post".*?</form>', html, re.S)
        assert formularios, caminho
        assert all('name="csrf_token"' in f for f in formularios), caminho


def test_cabecalhos_de_seguranca(cliente):
    r = cliente.get("/")
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"] and r.headers["Cache-Control"] == "no-store"


# ------------------------------------------------------------ perfis
@pytest.mark.parametrize("perfil,auditoria_ok,utilizadores_ok", [("analista", False, False),
                                                                  ("supervisor", True, False),
                                                                  ("administrador", True, True)])
def test_cada_perfil_ve_o_que_lhe_compete(app, perfil, auditoria_ok, utilizadores_ok):
    cliente = criar_cliente(app, perfil)
    assert cliente.get("/auditoria").status_code == (200 if auditoria_ok else 403)
    assert cliente.get("/utilizadores").status_code == (200 if utilizadores_ok else 403)
    assert cliente.post("/utilizadores", data={"nome_utilizador": "intruso", "nome": "X", "perfil": "administrador",
                                               "senha": SENHA_TESTE}).status_code == (200 if utilizadores_ok else 403)
    html = cliente.get("/").get_data(as_text=True)
    assert ('href="/auditoria"' in html) == auditoria_ok and ('href="/utilizadores"' in html) == utilizadores_ok
    for caminho in ("/", "/analisar", "/perfil", "/modelos"):
        assert cliente.get(caminho).status_code == 200
    if not utilizadores_ok:
        assert contas.obter_por_nome(_sessoes(app), "intruso") is None
        assert (f"teste.{perfil}", "acesso_negado", "/utilizadores") in _acoes(app)


def test_analista_so_ve_as_suas_analises(app):
    ana, rui = criar_cliente(app, "analista", "ana"), criar_cliente(app, "analista", "rui")
    chave = re.search(r"/analisar/([\w-]+)\.csv", _enviar(ana, "da_ana.csv").get_data(as_text=True)).group(1)
    assert ana.get(f"/analisar/{chave}").status_code == 200
    assert rui.get(f"/analisar/{chave}").status_code == 404
    assert rui.get(f"/analisar/{chave}.csv").status_code == 404
    assert "da_ana.csv" not in rui.get("/analisar").get_data(as_text=True)
    supervisor = criar_cliente(app, "supervisor")
    html = supervisor.get("/analisar").get_data(as_text=True)
    assert "da_ana.csv" in html and "<th>Utilizador</th>" in html and ">ana<" in html
    assert supervisor.get(f"/analisar/{chave}").status_code == 200


# ------------------------------------------------------------ gestão de contas
def test_administrador_cria_conta_que_tem_de_mudar_a_senha(cliente):
    app = cliente.application
    r = cliente.post("/utilizadores", data={"nome_utilizador": "Maria.J", "nome": "Maria João", "perfil": "supervisor",
                                            "senha": "temporaria-123"})
    assert r.status_code == 200 and "maria.j" in r.get_data(as_text=True)
    assert ("teste.administrador", "utilizador_criado", "maria.j") in _acoes(app)

    maria = criar_cliente(app, None)
    assert _entrar(maria, "maria.j", "temporaria-123").status_code == 302
    assert maria.get("/analisar").headers["Location"] == "/conta/senha"  # nada abre antes de mudar a senha
    assert "Escolha uma senha nova" in maria.get("/conta/senha").get_data(as_text=True)
    r = maria.post("/conta/senha", data={"senha_atual": "temporaria-123", "senha_nova": "a-minha-senha-nova",
                                         "confirmacao": "a-minha-senha-nova"})
    assert r.status_code == 200 and "Senha alterada" in r.get_data(as_text=True)
    assert maria.get("/analisar").status_code == 200
    assert ("maria.j", "senha_alterada", "maria.j") in _acoes(app)


@pytest.mark.parametrize("dados,erro", [
    (dict(nome_utilizador="ab", nome="X", perfil="analista", senha=SENHA_TESTE), "3 a 50 caracteres"),
    (dict(nome_utilizador="novo", nome="X", perfil="dono", senha=SENHA_TESTE), "Perfil desconhecido"),
    (dict(nome_utilizador="novo", nome="X", perfil="analista", senha="curta"), "pelo menos 10"),
    (dict(nome_utilizador="novo", nome="X", perfil="analista", senha="novo-1234567"), "nome de utilizador"),
    (dict(nome_utilizador="teste.administrador", nome="X", perfil="analista", senha=SENHA_TESTE), "Já existe"),
])
def test_criacao_invalida_explica_o_erro(cliente, dados, erro):
    r = cliente.post("/utilizadores", data=dados)
    assert r.status_code == 400 and erro in r.get_data(as_text=True)


def test_desativar_ou_mudar_perfil_termina_a_sessao(cliente):
    app = cliente.application
    ana = criar_cliente(app, "analista", "ana")
    assert ana.get("/").status_code == 200
    cliente.post(f"/utilizadores/{ana.utilizador.id}", data={"nome": "Ana", "perfil": "supervisor", "ativo": "1"})
    assert ana.get("/").status_code == 302
    assert ("teste.administrador", "utilizador_alterado", "ana") in _acoes(app)

    rui = criar_cliente(app, "analista", "rui")
    cliente.post(f"/utilizadores/{rui.utilizador.id}", data={"nome": "Rui", "perfil": "analista"})  # sem 'ativo'
    assert rui.get("/").status_code == 302 and not contas.obter_por_nome(_sessoes(app), "rui").ativo


def test_sistema_nunca_fica_sem_administrador(cliente):
    eu = cliente.utilizador.id
    r = cliente.post(f"/utilizadores/{eu}", data={"nome": "Eu", "perfil": "analista", "ativo": "1"})
    assert r.status_code == 400 and "pelo menos um administrador" in r.get_data(as_text=True)
    r = cliente.post(f"/utilizadores/{eu}", data={"nome": "Eu", "perfil": "administrador"})
    assert r.status_code == 400 and "própria conta" in r.get_data(as_text=True)


def test_redefinir_senha_obriga_a_mudar(cliente):
    app = cliente.application
    ana = criar_cliente(app, "analista", "ana")
    r = cliente.post(f"/utilizadores/{ana.utilizador.id}/senha", data={"senha": "provisoria-321"})
    assert r.status_code == 200 and "Senha redefinida" in r.get_data(as_text=True)
    assert ana.get("/").status_code == 302  # a sessão antiga terminou
    nova = criar_cliente(app, None)
    _entrar(nova, "ana", "provisoria-321")
    assert nova.get("/").headers["Location"] == "/conta/senha"
    assert ("teste.administrador", "senha_redefinida", "ana") in _acoes(app)


@pytest.mark.parametrize("dados,erro", [
    (dict(senha_atual="errada", senha_nova="nova-senha-boa", confirmacao="nova-senha-boa"), "senha atual"),
    (dict(senha_atual=SENHA_TESTE, senha_nova="nova-senha-boa", confirmacao="outra-senha-x"), "confirmação"),
    (dict(senha_atual=SENHA_TESTE, senha_nova="curta", confirmacao="curta"), "pelo menos 10"),
    (dict(senha_atual=SENHA_TESTE, senha_nova=SENHA_TESTE, confirmacao=SENHA_TESTE), "diferente"),
])
def test_mudar_senha_valida_os_dados(cliente, dados, erro):
    r = cliente.post("/conta/senha", data=dados)
    assert r.status_code == 400 and erro in r.get_data(as_text=True)


def test_mudar_senha_termina_as_outras_sessoes(app):
    a = criar_cliente(app, "analista", "ana")
    b = criar_cliente(app, "analista", "ana")
    a.post("/conta/senha", data={"senha_atual": SENHA_TESTE, "senha_nova": "nova-senha-boa", "confirmacao": "nova-senha-boa"})
    assert a.get("/").status_code == 200 and b.get("/").status_code == 302


# ------------------------------------------------------------ auditoria
def test_acoes_ficam_registadas_com_quem_e_sobre_o_que(app):
    ana = criar_cliente(app, "analista", "ana")
    ana.post("/", data=FORM)
    ana.post("/", data={"exemplo": "valor_alto"})
    chave = re.search(r"/analisar/([\w-]+)\.csv", _enviar(ana, "maio.csv").get_data(as_text=True)).group(1)
    ana.get(f"/analisar/{chave}")
    ana.get(f"/analisar/{chave}.csv")
    ana.get("/perfil?cliente=C0001")
    registos = _acoes(app, utilizador="ana")
    for esperado in [("ana", "testar_transacao", "C0001"), ("ana", "analisar_ficheiro", chave),
                     ("ana", "consultar_analise", chave), ("ana", "descarregar_analise", chave),
                     ("ana", "consultar_perfil", "C0001")]:
        assert esperado in registos
    eventos, _ = auditoria.listar(_sessoes(app), auditoria.Filtro(acao="analisar_ficheiro"))
    assert "maio.csv: 2 transações" in eventos[0].detalhe and eventos[0].ip == "127.0.0.1"
    assert sum(a == "testar_transacao" for _, a, _ in registos) == 2


def test_pagina_de_auditoria_filtra(app):
    ana = criar_cliente(app, "analista", "ana")
    ana.get("/perfil?cliente=C0001")
    ana.get("/perfil?cliente=C0002")
    supervisor = criar_cliente(app, "supervisor")
    html = supervisor.get("/auditoria?utilizador=ana&acao=consultar_perfil&alvo=C0002").get_data(as_text=True)
    assert "<strong>1</strong> registo" in html and "C0002" in html
    html = supervisor.get("/auditoria?desde=2000-01-01&ate=2000-01-02").get_data(as_text=True)
    assert "Nenhum registo" in html
    assert supervisor.get("/auditoria?pagina=abc&desde=lixo&acao=inventada").status_code == 200


def test_auditoria_pagina_os_resultados(app):
    for i in range(auditoria.POR_PAGINA + 5):
        auditoria.registar(_sessoes(app), "consultar_perfil", nome_utilizador="ana", alvo=f"C{i:04d}")
    supervisor = criar_cliente(app, "supervisor")
    html = supervisor.get("/auditoria?utilizador=ana").get_data(as_text=True)
    assert "página 1 de 2" in html and "pagina=2" in html
    assert html.count('class="evento-consultar_perfil"') == auditoria.POR_PAGINA
    html2 = supervisor.get("/auditoria?utilizador=ana&pagina=2").get_data(as_text=True)
    assert html2.count('class="evento-consultar_perfil"') == 5


def test_acao_desconhecida_nao_e_gravada(app):
    with pytest.raises(ValueError):
        auditoria.registar(_sessoes(app), "apagar_tudo")


# ------------------------------------------------------------ integridade dos modelos
def test_modelos_gravados_levam_manifesto_assinado(tmp_path, artefatos_pequenos):
    gravar(tmp_path, artefatos_pequenos, CHAVE_TESTE)
    manifesto = json.loads((tmp_path / "integridade.json").read_text(encoding="utf-8"))
    assert manifesto["algoritmo"] == "HMAC-SHA256" and set(manifesto["ficheiros"]) >= {"detector.joblib"}
    assert verificar_integridade(tmp_path, CHAVE_TESTE) is None and carregar(tmp_path, CHAVE_TESTE) is not None


@pytest.mark.parametrize("estragar,motivo", [
    (lambda p: (p / "historico.joblib").write_bytes(b"outro conteudo"), "historico.joblib foi alterado"),
    (lambda p: (p / "integridade.json").unlink(), "falta o integridade.json"),
    (lambda p: (p / "integridade.json").write_text("{", encoding="utf-8"), "danificado"),
])
def test_modelos_alterados_sao_recusados_antes_de_ler(tmp_path, artefatos_pequenos, estragar, motivo):
    gravar(tmp_path, artefatos_pequenos, CHAVE_TESTE)
    estragar(tmp_path)
    assert motivo in verificar_integridade(tmp_path, CHAVE_TESTE)
    assert carregar(tmp_path, CHAVE_TESTE) is None


def test_manifesto_refeito_sem_a_chave_e_recusado(tmp_path, artefatos_pequenos):
    """Quem troca um ficheiro e recalcula o SHA-256 no manifesto não conhece a chave: a assinatura falha."""
    import hashlib
    gravar(tmp_path, artefatos_pequenos, CHAVE_TESTE)
    (tmp_path / "detector.joblib").write_bytes(b"pickle malicioso")
    manifesto = json.loads((tmp_path / "integridade.json").read_text(encoding="utf-8"))
    manifesto["ficheiros"]["detector.joblib"] = hashlib.sha256(b"pickle malicioso").hexdigest()
    (tmp_path / "integridade.json").write_text(json.dumps(manifesto), encoding="utf-8")
    assert "assinatura não confere" in verificar_integridade(tmp_path, CHAVE_TESTE)
    assert "assinatura não confere" in verificar_integridade(tmp_path, "outra-chave-" + "1" * 40)


def test_app_com_modelos_alterados_nao_os_usa(tmp_path, artefatos_pequenos, ambiente_de_teste):
    gravar(tmp_path, artefatos_pequenos, CHAVE_TESTE)
    (tmp_path / "detector.joblib").write_bytes(b"lixo")
    app = criar_app(tmp_path)
    assert app.config["ARTEFATOS"] is None
    assert criar_cliente(app).get("/").status_code == 503
    assert criar_cliente(app).get("/utilizadores").status_code == 200  # a gestão de contas continua a funcionar
    log = (ambiente_de_teste / "logs" / "detetor.log").read_text(encoding="utf-8")
    assert "recusados: o ficheiro detector.joblib foi alterado" in log


# ------------------------------------------------------------ configuração e gerir.py
def test_chave_secreta_curta_e_recusada(monkeypatch):
    from detector.configuracao import ler_configuracao
    monkeypatch.setenv("CHAVE_SECRETA", "curta")
    with pytest.raises(ValueError, match="32 caracteres"):
        ler_configuracao(ficheiro_env=None)


def test_chave_local_fica_igual_entre_arranques(tmp_path, monkeypatch):
    from detector import configuracao
    monkeypatch.setattr(configuracao, "RAIZ", tmp_path)
    monkeypatch.delenv("CHAVE_SECRETA")
    primeira = configuracao.ler_configuracao(ficheiro_env=None).chave_secreta
    assert (tmp_path / "instancia" / "chave_local.txt").exists()
    assert configuracao.ler_configuracao(ficheiro_env=None).chave_secreta == primeira


def test_gerir_cria_administrador(app, capsys):
    import gerir
    gerir.criar_admin("Chefe", "Chefe da Equipa", "uma-senha-forte")
    u = contas.obter_por_nome(_sessoes(app), "chefe")
    assert u.perfil == "administrador" and not u.trocar_senha and "criado" in capsys.readouterr().out
    assert ("gerir.py", "utilizador_criado", "chefe") in _acoes(app)
    with pytest.raises(ValueError, match="Já existe"):
        gerir.criar_admin("chefe", "Outro", "uma-senha-forte")


def test_migracao_0002_desfaz_e_refaz(ambiente_de_teste):
    from alembic import command
    from detector.banco import configuracao_alembic, migrar
    url = "sqlite:///" + (ambiente_de_teste / "idas.db").as_posix()
    migrar(url)
    command.downgrade(configuracao_alembic(url), "0001")
    migrar(url)


def test_criar_admin_interativo_pede_de_novo_quando_a_senha_falha(app, monkeypatch, capsys):
    import gerir
    respostas = iter(["Chefe", "Chefe da Equipa"])
    senhas = iter(["curta", "curta", "uma-senha-forte", "outra-senha-x", "uma-senha-forte", "uma-senha-forte"])
    monkeypatch.setattr("builtins.input", lambda _: next(respostas))
    monkeypatch.setattr(gerir.getpass, "getpass", lambda _: next(senhas))
    gerir.criar_admin_interativo()
    saida = capsys.readouterr().out
    assert "pelo menos 10" in saida and "não são iguais" in saida and "criado" in saida
    assert contas.obter_por_nome(_sessoes(app), "chefe").perfil == "administrador"


def test_preparar_sem_teclado_nao_falha(app, monkeypatch, capsys):
    import gerir
    monkeypatch.setattr(gerir, "criar_env", lambda: False)
    monkeypatch.setattr(gerir, "treinar", lambda forcar=False: None)
    monkeypatch.setattr(gerir.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(EOFError))
    gerir.main(["preparar"])
    assert "Nenhuma conta criada" in capsys.readouterr().out
