import json

import pytest

from detector.artefatos import carregar
from detector.modelos import ORDEM_SIMPLICIDADE, escolher_modelo
from treinar import executar


@pytest.fixture(scope="module")
def resultado(tmp_path_factory):
    raiz = tmp_path_factory.mktemp("treino")
    return executar(raiz / "modelos", raiz / "data", raiz / "RELATORIO.md", n_clientes=120), raiz


def test_grava_tudo_o_que_a_interface_precisa(resultado):
    _, raiz = resultado
    artefatos = carregar(raiz / "modelos")
    assert artefatos is not None and (raiz / "data" / "transacoes.csv").exists()
    assert {p.name for p in (raiz / "modelos" / "graficos").iterdir()} == {
        "matriz_confusao.png", "importancia.png", "comparacao.png"}
    assert {"risco", "nivel", "motivos", "fraude"} <= set(artefatos.teste_avaliado.columns)


def test_resultados_tem_todas_as_chaves(resultado):
    res, raiz = resultado
    gravado = json.loads((raiz / "modelos" / "resultados.json").read_text(encoding="utf-8"))
    for chave in ("gerado_em", "tempo_segundos", "dados", "periodos", "modelos", "escolhido", "motivo_escolha",
                  "matriz_confusao", "recall_por_tipo", "ganho_isolation", "importancia", "divisao_aleatoria",
                  "aviso_gerador_facil", "atalhos"):
        assert chave in gravado, chave
    assert set(gravado["modelos"]) == {*ORDEM_SIMPLICIDADE, "isolation_forest"}
    assert set(gravado["periodos"]) == {"aquecimento", "treino", "validacao", "teste"}
    assert set(gravado["atalhos"]) == {"valor", "hora", "pais", "categoria", "cidade"}


def test_teste_nao_escolhe_nada(resultado):
    res, _ = resultado
    f1_val = {n: res["modelos"][n]["validacao"]["f1"] for n in ORDEM_SIMPLICIDADE}
    assert res["escolhido"] == escolher_modelo(f1_val)


def test_matriz_de_confusao_bate_com_as_metricas(resultado):
    res, _ = resultado
    (vn, fp), (fn, vp) = res["matriz_confusao"]
    teste = res["modelos"][res["escolhido"]]["teste"]
    assert vp + fp == teste["alertas"] and vp / (vp + fn) == pytest.approx(teste["recall"])


def test_nenhuma_coluna_bruta_e_um_atalho(resultado):
    res, _ = resultado
    pr_modelo = res["modelos"][res["escolhido"]]["teste"]["pr_auc"]
    assert all(v < pr_modelo / 2 for v in res["atalhos"].values()), (pr_modelo, res["atalhos"])


def test_relatorio_tem_as_secoes(resultado):
    _, raiz = resultado
    texto = (raiz / "RELATORIO.md").read_text(encoding="utf-8")
    for titulo in ("Dados", "Períodos", "Modelos", "Modelo escolhido", "Matriz de confusão",
                   "Recall por tipo de fraude", "Isolation Forest", "Importância das features",
                   "Divisão aleatória contra divisão por tempo", "Limitações"):
        assert f"## {titulo}" in texto, titulo
    assert "acurácia" in texto.lower() and "modelos/graficos/matriz_confusao.png" in texto


def test_relatorio_formata_numeros_e_nomes(resultado):
    res, raiz = resultado
    texto = (raiz / "RELATORIO.md").read_text(encoding="utf-8")
    assert "120 clientes, com" in texto and "fraudes apanhadas, " in texto
    assert "| Validação |" in texto and "| Valor alto |" in texto
    assert "| validacao |" not in texto and "| valor_alto |" not in texto


def test_relatorio_descreve_o_resultado_real_das_divisoes(resultado):
    res, raiz = resultado
    texto = (raiz / "RELATORIO.md").read_text(encoding="utf-8")
    d = res["divisao_aleatoria"]
    frase = ("a divisão aleatória deu um resultado mais otimista" if d["f1_aleatoria"] > d["f1_tempo"]
             else "a divisão aleatória não inflou o resultado")
    assert frase in texto
