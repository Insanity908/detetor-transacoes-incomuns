"""Arranque num alojamento na nuvem (Render): sem teclado, com o administrador definido no ambiente."""
from pathlib import Path

import pytest

from detector import contas

RAIZ = Path(__file__).resolve().parent.parent


def _preparar(monkeypatch, pasta_pequena):
    import gerir
    sessoes = __import__("app").criar_app(pasta_pequena).config["SESSOES"]
    monkeypatch.setattr(gerir, "banco", lambda: None)
    monkeypatch.setattr(gerir, "treinar", lambda *a, **k: None)
    monkeypatch.setattr(gerir, "criar_sinteticos", lambda *a, **k: None)
    monkeypatch.setattr(gerir, "_sessoes", lambda: sessoes)
    return gerir, sessoes


def test_nuvem_cria_o_administrador_do_ambiente_uma_vez(pasta_pequena, monkeypatch, capsys):
    gerir, sessoes = _preparar(monkeypatch, pasta_pequena)
    monkeypatch.setenv("ADMIN_UTILIZADOR", "chefe")
    monkeypatch.setenv("ADMIN_SENHA", "senha-da-demo")
    assert gerir.main(["nuvem"]) == 0
    assert gerir.main(["nuvem"]) == 0  # cada arranque repete: não cria outro nem falha
    u = contas.obter_por_nome(sessoes, "chefe")
    assert u.perfil == "administrador" and contas.contar(sessoes) == 1
    assert contas.confirmar_senha(u, "senha-da-demo")
    assert "Administrador 'chefe' criado" in capsys.readouterr().out


def test_nuvem_sem_senha_no_ambiente_avisa_e_nao_cria(pasta_pequena, monkeypatch, capsys):
    gerir, sessoes = _preparar(monkeypatch, pasta_pequena)
    monkeypatch.delenv("ADMIN_SENHA", raising=False)
    assert gerir.main(["nuvem"]) == 0
    assert contas.contar(sessoes) == 0
    assert "ADMIN_SENHA" in capsys.readouterr().out


def test_nuvem_prepara_banco_modelos_e_clientes(pasta_pequena, monkeypatch):
    gerir, _ = _preparar(monkeypatch, pasta_pequena)
    passos = []
    monkeypatch.setattr(gerir, "banco", lambda: passos.append("banco"))
    monkeypatch.setattr(gerir, "treinar", lambda *a, **k: passos.append("treinar"))
    monkeypatch.setattr(gerir, "criar_sinteticos", lambda *a, **k: passos.append("clientes"))
    monkeypatch.setenv("ADMIN_SENHA", "x")
    assert gerir.main(["nuvem"]) == 0
    assert passos == ["banco", "treinar", "clientes"]


def test_render_yaml_existe_e_usa_o_comando_nuvem():
    texto = (RAIZ / "render.yaml").read_text(encoding="utf-8")
    for esperado in ("gerir.py treinar", "gerir.py nuvem", "servidor.py", "HOST", "0.0.0.0",
                     "CHAVE_SECRETA", "generateValue", "ADMIN_SENHA", "/saude", "DEMO_DADOS", "DEMO_SENHA"):
        assert esperado in texto, esperado


# ---------------------------------------------------------------- dados da demonstração a cada arranque
def _demo(monkeypatch, pasta_pequena, **env):
    gerir, sessoes = _preparar(monkeypatch, pasta_pequena)
    monkeypatch.setenv("ADMIN_SENHA", "senha-admin")
    monkeypatch.setenv("DEMO_DADOS", "1")
    for chave, valor in env.items():
        monkeypatch.setenv(chave, valor)
    return gerir, sessoes


def test_nuvem_com_demo_cria_equipa_e_cliente_prontos_a_usar(pasta_pequena, monkeypatch, capsys):
    from detector import acesso_cliente, clientes
    gerir, sessoes = _demo(monkeypatch, pasta_pequena, DEMO_SENHA="senha-demo")
    assert gerir.main(["nuvem"]) == 0
    for nome in ("analista", "supervisor", "balcao"):
        u = contas.obter_por_nome(sessoes, nome)
        assert u.perfil == nome and not u.trocar_senha
        assert contas.confirmar_senha(u, "senha-demo")
    demo = [c for c in clientes.pesquisar(sessoes, "Demonstração") if not c.sintetico]
    assert len(demo) == 1 and demo[0].conta.saldo == 2_000_000
    assert acesso_cliente.entrar(sessoes, demo[0].codigo, "senha-demo").id == demo[0].id
    assert not acesso_cliente.obter_acesso(sessoes, demo[0].id).trocar_senha
    saida = capsys.readouterr().out
    assert demo[0].codigo in saida and "/cliente/entrar" in saida


def test_nuvem_com_demo_e_idempotente(pasta_pequena, monkeypatch):
    from detector import clientes
    gerir, sessoes = _demo(monkeypatch, pasta_pequena)
    assert gerir.main(["nuvem"]) == 0
    assert gerir.main(["nuvem"]) == 0
    assert contas.contar(sessoes) == 4  # admin + analista + supervisor + balcao
    demo = [c for c in clientes.pesquisar(sessoes, "Demonstração") if not c.sintetico]
    assert len(demo) == 1 and demo[0].conta.saldo == 2_000_000  # o depósito não se repete


def test_demo_usa_a_senha_do_admin_se_nao_houver_senha_propria(pasta_pequena, monkeypatch):
    gerir, sessoes = _demo(monkeypatch, pasta_pequena)
    monkeypatch.delenv("DEMO_SENHA", raising=False)
    assert gerir.main(["nuvem"]) == 0
    assert contas.confirmar_senha(contas.obter_por_nome(sessoes, "analista"), "senha-admin")


def test_sem_demo_dados_so_cria_o_admin(pasta_pequena, monkeypatch):
    gerir, sessoes = _preparar(monkeypatch, pasta_pequena)
    monkeypatch.setenv("ADMIN_SENHA", "x")
    monkeypatch.delenv("DEMO_DADOS", raising=False)
    assert gerir.main(["nuvem"]) == 0
    assert contas.contar(sessoes) == 1


# ---------------------------------------------------------------- caminho até à área do cliente
def test_pagina_de_entrada_da_equipa_liga_a_area_do_cliente(pasta_pequena):
    from tests.conftest import criar_cliente
    from app import criar_app
    cli = criar_cliente(criar_app(pasta_pequena), None)
    html = cli.get("/entrar").get_data(as_text=True)
    assert 'href="/cliente/entrar"' in html and "Área do cliente" in html


def test_criar_acesso_no_balcao_diz_onde_o_cliente_entra(pasta_pequena):
    from tests.conftest import criar_cliente
    from app import criar_app
    from detector import clientes
    app = criar_app(pasta_pequena)
    sessoes = app.config["SESSOES"]
    balcao = criar_cliente(app, "balcao")
    c = clientes.criar(sessoes, clientes.validar({
        "nome": "Ana Teste", "data_nascimento": "1990-01-01", "tipo_documento": "NUIT",
        "numero_documento": "123456789", "telefone": "841234567", "email": "", "cidade": "MAPUTO",
        "morada": "Rua 1"}), por="teste")
    r = balcao.post(f"/balcao/clientes/{c.id}/acesso", data={"senha": "provisoria"}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "/cliente/entrar" in html and c.codigo in html


@pytest.mark.parametrize("ficheiro", [".env"])
def test_segredos_locais_nao_estao_no_git(ficheiro):
    import subprocess
    r = subprocess.run(["git", "ls-files", ficheiro], cwd=RAIZ, capture_output=True, text=True)
    assert r.stdout.strip() == ""
