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
                     "CHAVE_SECRETA", "generateValue", "ADMIN_SENHA", "/saude"):
        assert esperado in texto, esperado


@pytest.mark.parametrize("ficheiro", [".env"])
def test_segredos_locais_nao_estao_no_git(ficheiro):
    import subprocess
    r = subprocess.run(["git", "ls-files", ficheiro], cwd=RAIZ, capture_output=True, text=True)
    assert r.stdout.strip() == ""
