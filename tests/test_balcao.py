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


def test_balcao_pode_mudar_a_senha_por_post(app_balcao, cliente_balcao):
    r = cliente_balcao.post("/conta/senha", data={"senha_atual": "senha-de-teste-123",
                                                  "senha_nova": "Outra-Senha-Forte-456",
                                                  "confirmacao": "Outra-Senha-Forte-456"})
    assert r.status_code == 200 and "Senha alterada" in r.get_data(as_text=True)


# ------------------------------------------------------------------ ecrãs do balcão
DADOS = {"nome": "Ana Maria Sitoe", "data_nascimento": "1990-05-10", "tipo_documento": "NUIT",
         "numero_documento": "123456789", "telefone": "841234567", "email": "ana@exemplo.mz",
         "cidade": "MAPUTO", "morada": "Av. 24 de Julho 100"}


def _acoes(app):
    from sqlalchemy import select
    from detector.banco import EventoAuditoria
    with app.config["SESSOES"]() as s:
        return [(e.acao, e.alvo) for e in s.scalars(select(EventoAuditoria))]


def _novo(cliente_balcao, **extra):
    return cliente_balcao.post("/balcao/clientes/novo", data={**DADOS, **extra})


def test_criar_cliente_redireciona_para_a_ficha_e_audita(app_balcao, cliente_balcao):
    r = _novo(cliente_balcao)
    assert r.status_code == 302 and r.headers["Location"].endswith("/balcao/clientes/1")
    acoes = _acoes(app_balcao)
    assert ("cliente_criado", "C0001") in acoes and ("conta_aberta", "C0001") in acoes
    assert "Ana Maria Sitoe" in cliente_balcao.get("/balcao/clientes/1").get_data(as_text=True)


def test_formulario_invalido_da_400_com_mensagem(cliente_balcao):
    r = _novo(cliente_balcao, telefone="123")
    assert r.status_code == 400
    assert "O telefone deve ter 9 dígitos e começar por 8." in r.get_data(as_text=True)


def test_documento_repetido_da_400(cliente_balcao):
    _novo(cliente_balcao)
    r = _novo(cliente_balcao)
    assert r.status_code == 400 and "Já existe um cliente" in r.get_data(as_text=True)


def test_deposito_aparece_na_ficha(app_balcao, cliente_balcao):
    _novo(cliente_balcao)
    r = cliente_balcao.post("/balcao/clientes/1/deposito", data={"valor": "1 500,00", "descricao": "Abertura"})
    assert r.status_code == 302
    pagina = cliente_balcao.get("/balcao/clientes/1").get_data(as_text=True)
    assert "1.500" in pagina and "Abertura" in pagina
    assert ("deposito", "C0001") in _acoes(app_balcao)


def test_deposito_invalido_da_400(cliente_balcao):
    _novo(cliente_balcao)
    for valor in ("abc", "-5", "0", ""):
        r = cliente_balcao.post("/balcao/clientes/1/deposito", data={"valor": valor})
        assert r.status_code == 400


def test_pesquisa_por_parte_do_nome(cliente_balcao):
    _novo(cliente_balcao)
    assert "Ana Maria" in cliente_balcao.get("/balcao/clientes?q=maria").get_data(as_text=True)
    assert "Ana Maria" not in cliente_balcao.get("/balcao/clientes?q=zzz").get_data(as_text=True)


def test_editar_cliente(app_balcao, cliente_balcao):
    _novo(cliente_balcao)
    r = cliente_balcao.post("/balcao/clientes/1/editar", data={"telefone": "851111111", "email": "",
                                                               "morada": "Rua Nova 1", "cidade": "BEIRA"})
    assert r.status_code == 302 and ("cliente_alterado", "C0001") in _acoes(app_balcao)
    assert cliente_balcao.post("/balcao/clientes/1/editar", data={"telefone": "1", "email": "",
                                                                  "morada": "x", "cidade": "BEIRA"}).status_code == 400


def test_bloquear_e_desbloquear(app_balcao, cliente_balcao):
    _novo(cliente_balcao)
    cliente_balcao.post("/balcao/clientes/1/bloquear", data={"bloquear": "1"})
    assert "Bloqueada" in cliente_balcao.get("/balcao/clientes/1").get_data(as_text=True)
    assert cliente_balcao.post("/balcao/clientes/1/deposito", data={"valor": "10"}).status_code == 400
    cliente_balcao.post("/balcao/clientes/1/bloquear", data={"bloquear": "0"})
    assert cliente_balcao.post("/balcao/clientes/1/deposito", data={"valor": "10"}).status_code == 302
    assert ("conta_bancaria_bloqueada", "C0001") in _acoes(app_balcao)
    cliente_balcao.post("/balcao/clientes/1/bloquear", data={"bloquear": "0"})
    assert ("conta_bancaria_desbloqueada", "C0001") in _acoes(app_balcao)


def test_cliente_inexistente_da_404(cliente_balcao):
    assert cliente_balcao.get("/balcao/clientes/99").status_code == 404


def test_administrador_usa_o_balcao(cliente):
    assert cliente.get("/balcao/clientes").status_code == 200


def test_analista_nao_deposita(app_balcao):
    analista = criar_cliente(app_balcao, "analista")
    assert analista.post("/balcao/clientes/1/deposito", data={"valor": "5"}).status_code == 403


def test_balcao_funciona_sem_modelos(tmp_path):
    app = criar_app(tmp_path / "vazia")
    assert criar_cliente(app, "balcao").get("/balcao/clientes").status_code == 200


def test_senha_do_utilizador_criado_pre_seleciona_analista(cliente):
    assert 'value="analista" selected' in cliente.get("/utilizadores").get_data(as_text=True)


# ------------------------------------------------------------ levantamento ao balcão
def _forcar_nivel(monkeypatch, nivel, risco):
    from detector import autorizacao

    def falso(artefatos, df, historico_extra=None, cortes=None):
        r = df.copy().reset_index(drop=True)
        r["risco"], r["anomalia"], r["nivel"] = risco, 10.0, nivel
        r["motivos"] = [["teste"]] * len(r)
        r["historico_curto"] = False
        return r
    monkeypatch.setattr(autorizacao, "avaliar_transacoes", falso)


def _com_saldo(cliente_balcao, saldo="1 000,00"):
    _novo(cliente_balcao)
    cliente_balcao.post("/balcao/clientes/1/deposito", data={"valor": saldo})


def _levantar(cliente_balcao, valor="100,00", **extra):
    return cliente_balcao.post("/balcao/clientes/1/levantamento", data={"valor": valor, **extra},
                               follow_redirects=True)


def test_levantamento_autorizado(app_balcao, cliente_balcao, monkeypatch):
    _forcar_nivel(monkeypatch, "Normal", 5.0)
    _com_saldo(cliente_balcao)
    pagina = _levantar(cliente_balcao).get_data(as_text=True)
    assert "Levantamento autorizado. Entregue o dinheiro." in pagina
    assert "900,00 MT" in pagina and "Levantamento" in pagina
    assert ("levantamento", "C0001") in _acoes(app_balcao)


def test_levantamento_retido_manda_nao_entregar(app_balcao, cliente_balcao, monkeypatch):
    _forcar_nivel(monkeypatch, "Alto risco", 90.0)
    _com_saldo(cliente_balcao)
    pagina = _levantar(cliente_balcao).get_data(as_text=True)
    assert "Em verificação." in pagina and "Não entregue o dinheiro" in pagina
    assert "Retido" in pagina and ("levantamento", "C0001") in _acoes(app_balcao)


def test_levantamento_recusado_mostra_o_motivo(app_balcao, cliente_balcao, monkeypatch):
    _forcar_nivel(monkeypatch, "Normal", 5.0)
    _com_saldo(cliente_balcao, "50,00")
    pagina = _levantar(cliente_balcao, "100,00", cidade="BEIRA").get_data(as_text=True)
    assert "Saldo insuficiente." in pagina
    assert ("levantamento", "C0001") in _acoes(app_balcao)


def test_levantamento_invalido_da_400(cliente_balcao, monkeypatch):
    _forcar_nivel(monkeypatch, "Normal", 5.0)
    _com_saldo(cliente_balcao)
    for valor in ("abc", "-5", "0", ""):
        assert cliente_balcao.post("/balcao/clientes/1/levantamento", data={"valor": valor}).status_code == 400


def test_levantamento_sem_modelos_da_503(tmp_path):
    app = criar_app(tmp_path / "vazia")
    c = criar_cliente(app, "balcao")
    c.post("/balcao/clientes/novo", data=DADOS)
    r = c.post("/balcao/clientes/1/levantamento", data={"valor": "10"})
    assert r.status_code == 503
    assert "O detetor ainda não está pronto: não é possível fazer levantamentos." in r.get_data(as_text=True)


def test_levantamento_retido_aviso_destacado(app_balcao, cliente_balcao, monkeypatch):
    _forcar_nivel(monkeypatch, "Alto risco", 90.0)
    _com_saldo(cliente_balcao)
    pagina = _levantar(cliente_balcao).get_data(as_text=True)
    assert "<strong>Não entregue o dinheiro</strong>" in pagina
    assert "aviso-retido" in pagina


def test_formularios_de_dinheiro_desativam_ao_enviar(cliente_balcao):
    _novo(cliente_balcao)
    pagina = cliente_balcao.get("/balcao/clientes/1").get_data(as_text=True)
    assert pagina.count("data-envio-unico") >= 2
