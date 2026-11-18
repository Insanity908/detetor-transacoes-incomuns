"""Perfil Balcão: só abre a sua área; os perfis do detetor não a abrem."""
import pytest

from app import criar_app
from tests.conftest import criar_cliente


@pytest.fixture()
def app_balcao(pasta_pequena):
    return criar_app(pasta_pequena)


@pytest.fixture()
def cliente_balcao(app_balcao):
    return criar_cliente(app_balcao, "balcao")


@pytest.mark.parametrize("caminho", ["/alertas", "/analisar", "/perfil", "/painel", "/modelos", "/utilizadores"])
def test_balcao_nao_abre_paginas_do_detetor(cliente_balcao, caminho):
    assert cliente_balcao.get(caminho).status_code == 403


def test_balcao_nao_usa_a_pagina_de_testar_por_post(cliente_balcao):
    assert cliente_balcao.post("/").status_code == 403


def test_balcao_na_raiz_vai_para_clientes(cliente_balcao):
    r = cliente_balcao.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/balcao/clientes")


@pytest.mark.xfail(strict=True, reason="a rota /balcao/clientes chega na Tarefa 5")
def test_analista_nao_abre_o_balcao(app_balcao):
    analista = criar_cliente(app_balcao, "analista")
    assert analista.get("/balcao/clientes").status_code == 403


def test_balcao_pode_mudar_a_senha(cliente_balcao):
    assert cliente_balcao.get("/conta/senha").status_code == 200


def test_acesso_negado_do_balcao_fica_auditado(app_balcao, cliente_balcao):
    cliente_balcao.get("/alertas")
    from sqlalchemy import select
    from detector.banco import EventoAuditoria
    with app_balcao.config["SESSOES"]() as s:
        assert s.scalars(select(EventoAuditoria).where(EventoAuditoria.acao == "acesso_negado")).first()


def test_administrador_cria_utilizador_balcao(cliente):
    r = cliente.post("/utilizadores", data={"nome_utilizador": "caixa1", "nome": "Caixa Um", "perfil": "balcao",
                                            "senha": "Senha-Forte-123"})
    assert r.status_code == 200
    assert "Balcão" in r.get_data(as_text=True)


def test_areas_dos_perfis(app_balcao):
    from detector.banco import Utilizador
    def com(perfil):
        return Utilizador(perfil=perfil)
    assert com("balcao").tem_area("balcao") and not com("balcao").tem_area("alertas")
    assert com("analista").tem_area("alertas") and not com("analista").tem_area("balcao")
    assert com("supervisor").tem_area("supervisao") and not com("supervisor").tem_area("administracao")
    assert all(com("administrador").tem_area(a) for a in ("balcao", "alertas", "supervisao", "administracao"))
    assert not com("balcao").pode("analista") and com("balcao").pode("balcao")
    assert com("analista").pode("analista") and not com("analista").pode("supervisor")
