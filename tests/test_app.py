import pytest

from app import criar_app
from detector.exemplos import NOMES, criar_exemplos

FORM = dict(cliente="C0001", data_hora="2026-05-01T14:30", valor="150", cidade="Maputo", pais="MZ",
            categoria="MERCEARIA")


def test_sem_modelos_mostra_a_instrucao(tmp_path):
    r = criar_app(tmp_path).test_client().get("/")
    assert r.status_code == 503 and "python treinar.py" in r.get_data(as_text=True)


def test_pagina_inicial_tem_as_abas_e_o_aviso(cliente):
    html = cliente.get("/").get_data(as_text=True)
    for t in ("Testar transação", "Analisar ficheiro", "Perfil do cliente", "Modelos", "Dados sintéticos"):
        assert t in html


def test_exemplos_usam_um_cliente_com_historico(artefatos_pequenos):
    ex = criar_exemplos(artefatos_pequenos.historico)
    assert set(ex) == set(NOMES) and len(ex["rajada"]) == 5 and all(len(ex[n]) == 1 for n in NOMES if n != "rajada")
    fim = artefatos_pequenos.historico.data_hora.max()
    assert all(t["data_hora"] > fim for lista in ex.values() for t in lista)


@pytest.mark.parametrize("nome", NOMES)
def test_exemplos_respondem(cliente, nome):
    r = cliente.post("/", data={"exemplo": nome})
    assert r.status_code == 200 and "Risco" in r.get_data(as_text=True)


def test_exemplo_normal_e_valor_alto(cliente):
    assert "Normal" in cliente.post("/", data={"exemplo": "normal"}).get_data(as_text=True)
    assert "vezes o habitual" in cliente.post("/", data={"exemplo": "valor_alto"}).get_data(as_text=True)


def test_rajada_mostra_uma_linha_por_compra(cliente):
    html = cliente.post("/", data={"exemplo": "rajada"}).get_data(as_text=True)
    assert html.count('class="compra"') == 5 and "compras na última hora" in html


def test_formulario_valido(cliente):
    r = cliente.post("/", data=FORM)
    assert r.status_code == 200 and "Risco" in r.get_data(as_text=True)


@pytest.mark.parametrize("campo,valor,trecho", [("valor", "-5", "valor"), ("data_hora", "", "data_hora"),
                                                 ("cidade", "", "cidade")])
def test_formulario_invalido_mostra_o_erro(cliente, campo, valor, trecho):
    r = cliente.post("/", data=FORM | {campo: valor})
    assert r.status_code == 400 and trecho in r.get_data(as_text=True)


def test_cliente_novo_avisa_historico_curto(cliente):
    html = cliente.post("/", data=FORM | {"cliente": "CLIENTE-NOVO"}).get_data(as_text=True)
    assert "pouco histórico" in html


def test_texto_do_utilizador_e_escapado(cliente):
    html = cliente.post("/", data=FORM | {"cidade": "<script>x</script>"}).get_data(as_text=True)
    assert "<script>x</script>" not in html
