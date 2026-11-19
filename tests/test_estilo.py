"""Estilo C: tokens e componentes no CSS, faixa e pílulas nas bases, sem scripts inline nem recursos externos."""
import re
from pathlib import Path

import pytest

from tests.conftest import criar_cliente

CSS = (Path(__file__).resolve().parent.parent / "static" / "estilo.css").read_text(encoding="utf-8")


def test_css_tem_as_cores_e_os_componentes():
    for cor in ("#17232e", "#1f5f8b", "#b3261e", "#8a5a00", "#2e7d4f"):
        assert cor in CSS
    for classe in (".faixa", ".nav-pilulas", ".conteudo-sobreposto", ".pagina", ".cartao", ".kpis", ".kpi",
                   ".lista-item", ".lista-icone", ".icone-alto", ".tabela-cartao", "prefers-reduced-motion"):
        assert classe in CSS, classe


@pytest.fixture()
def app(pasta_pequena):
    from app import criar_app
    return criar_app(pasta_pequena)


@pytest.mark.parametrize("perfil,caminho", [(None, "/entrar"), ("administrador", "/alertas"),
                                            ("administrador", "/painel"), ("administrador", "/balcao/clientes"),
                                            (None, "/cliente/entrar")])
def test_paginas_seguem_o_estilo_sem_scripts_inline_nem_externos(app, perfil, caminho):
    r = criar_cliente(app, perfil).get(caminho)
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and 'class="faixa' in html and 'class="pagina"' in html
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "script inline"
    assert not re.search(r'(?:src|href)="https?://', html), "recurso externo"
    assert "default-src 'self'" in r.headers.get("Content-Security-Policy", "")


def test_alertas_em_lista_com_icones(app):
    html = criar_cliente(app, "administrador").get("/alertas").get_data(as_text=True)
    assert 'class="lista fila"' in html and "nav-pilulas" in html
