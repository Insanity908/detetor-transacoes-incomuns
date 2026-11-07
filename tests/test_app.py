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


# ------------------------------------------------------------ analisar ficheiro
import io  # noqa: E402
import re  # noqa: E402

CABECALHO = "cliente,data_hora,valor,cidade,pais,categoria\n"
CSV_VALIDO = (CABECALHO + "C0001,2026-05-01 14:30,150,Maputo,MZ,MERCEARIA\n"
              "C0001,2026-05-01 15:00,90000,Maputo,MZ,MERCEARIA\nC0002,2026-05-02 03:10,500,Tete,AE,VIAGEM\n")


def _enviar(cliente, conteudo, nome="t.csv"):
    return cliente.post("/analisar", data={"ficheiro": (io.BytesIO(conteudo), nome)},
                        content_type="multipart/form-data")


def test_pagina_de_envio(cliente):
    html = cliente.get("/analisar").get_data(as_text=True)
    assert 'type="file"' in html and "5 MB" in html


def test_analisar_csv_ordena_por_risco(cliente):
    r = _enviar(cliente, CSV_VALIDO.encode())
    html = r.get_data(as_text=True)
    riscos = [int(x) for x in re.findall(r'data-risco="(\d+)"', html)]
    assert r.status_code == 200 and len(riscos) == 3 and riscos == sorted(riscos, reverse=True)


def test_sem_ficheiro_mostra_erro(cliente):
    r = cliente.post("/analisar", data={}, content_type="multipart/form-data")
    assert r.status_code == 400 and "Escolha um ficheiro" in r.get_data(as_text=True)


def test_csv_sem_colunas_mostra_quais_faltam(cliente):
    r = _enviar(cliente, b"cliente,data_hora,valor,pais,categoria\nC1,2026-05-01 14:30,5,MZ,b\n")
    assert r.status_code == 400 and "cidade" in r.get_data(as_text=True)


def test_so_alertas_filtra_a_tabela(cliente):
    html = _enviar(cliente, CSV_VALIDO.encode()).get_data(as_text=True)
    chave = re.search(r"/analisar/([\w-]+)\?alertas=1", html).group(1)
    filtrado = cliente.get(f"/analisar/{chave}?alertas=1").get_data(as_text=True)
    alertas = int(re.search(r"<strong>(\d+)</strong> com alerta", filtrado).group(1))
    assert filtrado.count("data-risco=") == alertas and "Normal</span>" not in filtrado
    assert "Mostrar todas" in filtrado


def test_download_neutraliza_formulas(cliente):
    conteudo = (CABECALHO + "C0001,2026-05-01 14:30,150,=HYPERLINK(1),MZ,MERCEARIA\n").encode()
    html = _enviar(cliente, conteudo).get_data(as_text=True)
    chave = re.search(r"/analisar/([\w-]+)\.csv", html).group(1)
    r = cliente.get(f"/analisar/{chave}.csv")
    texto = r.data.decode("utf-8-sig")
    assert r.mimetype == "text/csv" and "'=HYPERLINK(1)" in texto and texto.splitlines()[0].count(";") >= 9


def test_chave_de_download_desconhecida_da_404(cliente):
    assert cliente.get("/analisar/naoexiste.csv").status_code == 404
    assert cliente.get("/analisar/naoexiste").status_code == 404


def test_ficheiro_grande_demais_e_recusado(cliente):
    r = _enviar(cliente, b"x" * (6 * 1024 * 1024))
    assert r.status_code == 413 and "5 MB" in r.get_data(as_text=True)


# ------------------------------------------------------------ perfil
def test_perfil_mostra_o_cliente(cliente):
    html = cliente.get("/perfil?cliente=C0001").get_data(as_text=True)
    assert "C0001" in html and "MT" in html and 'class="barra-hora"' in html
    assert cliente.get("/perfil?cliente=NAO").status_code == 404
    assert "Escolha um cliente" in cliente.get("/perfil").get_data(as_text=True)


def test_perfil_lista_as_transacoes_marcadas(cliente_treinado, pasta_treinada):
    from detector.artefatos import carregar
    teste = carregar(pasta_treinada).teste_avaliado
    marcado = teste[teste.nivel != "Normal"].cliente.iloc[0]
    html = cliente_treinado.get(f"/perfil?cliente={marcado}").get_data(as_text=True)
    assert "Transações marcadas no período de teste" in html and "Rótulo do gerador" in html


# ------------------------------------------------------------ modelos
def test_modelos_mostra_a_escolha_e_os_graficos(cliente_treinado):
    html = cliente_treinado.get("/modelos").get_data(as_text=True)
    assert "acurácia" in html.lower() and "graficos/matriz_confusao.png" in html and "Modelo escolhido" in html
    assert cliente_treinado.get("/graficos/matriz_confusao.png").status_code == 200
    assert cliente_treinado.get("/graficos/resultados.json").status_code == 404
    assert cliente_treinado.get("/graficos/../resultados.json").status_code == 404


def test_modelos_sem_resultados_pede_o_treino(cliente):
    r = cliente.get("/modelos")
    assert r.status_code == 200 and "python treinar.py" in r.get_data(as_text=True)


def test_filtro_tira_as_linhas_normais(cliente, artefatos_pequenos):
    t = criar_exemplos(artefatos_pequenos.historico)["normal"][0]
    normal = f"{t['cliente']},{t['data_hora']:%Y-%m-%d %H:%M},{t['valor']},{t['cidade']},{t['pais']},{t['categoria']}\n"
    html = _enviar(cliente, (CSV_VALIDO + normal).encode()).get_data(as_text=True)
    assert "Normal</span>" in html
    chave = re.search(r"/analisar/([\w-]+)\?alertas=1", html).group(1)
    filtrado = cliente.get(f"/analisar/{chave}?alertas=1").get_data(as_text=True)
    assert filtrado.count("data-risco=") < html.count("data-risco=") and "Normal</span>" not in filtrado


def test_nota_nunca_arredonda_para_o_limite(cliente):
    nota = cliente.application.jinja_env.filters["nota"]
    assert nota(49.6) == "49" and nota(50.0) == "50" and nota(77.9) == "77"


def test_pagina_de_envio_avisa_a_espera(cliente):
    assert "pode demorar até um minuto" in cliente.get("/analisar").get_data(as_text=True)


# --------------------------------------------- pontos menores da revisão final
def test_neutraliza_tab_e_retorno_no_inicio():
    from app import _neutralizar
    assert _neutralizar("\tx") == "'\tx" and _neutralizar("\rx") == "'\rx" and _neutralizar("ok") == "ok"


def test_perfil_mostra_o_nome_do_tipo_de_fraude(cliente_treinado, pasta_treinada):
    from detector.artefatos import carregar
    teste = carregar(pasta_treinada).teste_avaliado
    alvo = teste[(teste.nivel != "Normal") & teste.tipo_fraude.str.contains("_")]
    marcado = alvo.cliente.iloc[0]
    html = cliente_treinado.get(f"/perfil?cliente={marcado}").get_data(as_text=True)
    assert "fraude (" in html and not re.search(r"fraude \([a-z]+_[a-z]+\)", html)


def test_perfil_aceita_codigo_em_minusculas(cliente):
    assert cliente.get("/perfil?cliente=c0001").status_code == 200


def test_textos_em_portugues_de_mocambique(cliente):
    html = cliente.post("/", data={"exemplo": "valor_alto"}).get_data(as_text=True)
    assert "Porque parece estranha" in html
    pagina = _enviar(cliente, CSV_VALIDO.encode()).get_data(as_text=True)
    assert "Descarregar resultado" in pagina and "Baixar" not in pagina
