import numpy as np
import pandas as pd
import pytest

from detector.avaliacao import dividir_periodos, melhor_limiar, metricas
from detector.gerador import gerar_transacoes
from detector.modelos import ORDEM_SIMPLICIDADE, Detector, escolher_modelo, para_escala, treinar
from detector.perfis import FEATURES, calcular_features


@pytest.fixture(scope="module")
def conjuntos():
    df = gerar_transacoes(80, 120)
    X = calcular_features(df)
    p = dividir_periodos(df["data_hora"])
    y = df["fraude"].to_numpy()
    return X[p["treino"]], y[p["treino"]], X[p["validacao"]], y[p["validacao"]]


@pytest.fixture(scope="module")
def treinado(conjuntos):
    return treinar(*conjuntos)


def test_periodos_de_120_dias():
    datas = pd.Series(pd.date_range("2026-01-01", periods=120 * 24, freq="h"))
    p = dividir_periodos(datas)
    dias = lambda m: sorted({d.day_of_year for d in datas[m]})
    assert dias(p["aquecimento"]) == list(range(1, 31))
    assert dias(p["treino"])[0] == 31 and dias(p["treino"])[-1] == 88
    assert dias(p["validacao"]) == list(range(89, 103)) and dias(p["teste"]) == list(range(103, 121))
    assert not (p["treino"] & p["teste"]).any() and sum(m.sum() for m in p.values()) == len(datas)


@pytest.mark.parametrize("f1,esperado", [
    ({"regressao_logistica": .70, "random_forest": .71, "gradient_boosting": .80}, "gradient_boosting"),
    ({"regressao_logistica": .79, "random_forest": .80, "gradient_boosting": .81}, "regressao_logistica"),
    ({"regressao_logistica": .50, "random_forest": .795, "gradient_boosting": .81}, "random_forest"),
])
def test_regra_de_escolha(f1, esperado):
    assert escolher_modelo(f1) == esperado


def test_escala_poe_o_limiar_em_50():
    assert para_escala(np.array([0, .2, .6, 1, 3, -1]), .2).tolist() == [0, 50, 75, 100, 100, 0]


def test_limiar_maximiza_f1():
    assert melhor_limiar(np.array([0, 0, 1, 1]), np.array([.1, .4, .35, .8])) == .35


def test_limiar_sem_fraude_e_1():
    assert melhor_limiar(np.zeros(4), np.array([.1, .4, .35, .8])) == 1.0


def test_metricas():
    m = metricas(np.array([0, 0, 1, 1]), np.array([.1, .9, .2, .8]), .5)
    assert m["precisao"] == .5 and m["recall"] == .5 and m["f1"] == .5 and m["acuracia"] == .5 and m["alertas"] == 2
    vazio = metricas(np.zeros(3), np.array([.1, .2, .9]), .5)
    assert vazio["recall"] is None and vazio["pr_auc"] is None


def test_treino_pequeno_de_ponta_a_ponta(treinado, conjuntos):
    det, res = treinado
    assert det.escolhido in ORDEM_SIMPLICIDADE
    assert set(res["validacao"]) == {*ORDEM_SIMPLICIDADE, "isolation_forest"}
    avaliado = det.avaliar(conjuntos[2])
    assert list(avaliado.index) == list(conjuntos[2].index)
    assert set(avaliado.nivel) <= {"Alto risco", "Atenção", "Normal"}
    assert avaliado.risco.between(0, 100).all() and avaliado.anomalia.between(0, 100).all()


def test_isolation_marca_cerca_de_2_por_cento_da_validacao(treinado, conjuntos):
    det, _ = treinado
    assert 0.01 <= det.avaliar(conjuntos[2]).alerta_anomalia.mean() <= 0.03


def test_isolation_forest_nao_usa_rotulos(conjuntos):
    X_tr, y_tr, X_val, y_val = conjuntos
    a, _ = treinar(X_tr, y_tr, X_val, y_val)
    b, _ = treinar(X_tr, 1 - y_tr, X_val, y_val)
    np.testing.assert_allclose(a.pontuacoes(X_val).anomalia_bruta, b.pontuacoes(X_val).anomalia_bruta)


def test_niveis_seguem_a_tabela(treinado):
    det, _ = treinado

    class Falso(Detector):
        def pontuacoes(self, X):
            return pd.DataFrame({"prob": [1.0, .0, .0], "anomalia_bruta": [0.0, 1.0, 0.0]}, index=X.index)

    falso = Falso(det.classificadores, det.limiares, det.escolhido, det.isolation, det.limiar_anomalia)
    X = pd.DataFrame(np.zeros((3, len(FEATURES))), columns=FEATURES)
    assert falso.avaliar(X).nivel.tolist() == ["Alto risco", "Atenção", "Normal"]


def test_anomalia_nao_alerta_cliente_sem_historico(treinado):
    det, _ = treinado

    class Falso(Detector):
        def pontuacoes(self, X):
            return pd.DataFrame({"prob": [.0, .0], "anomalia_bruta": [1.0, 1.0]}, index=X.index)

    falso = Falso(det.classificadores, det.limiares, det.escolhido, det.isolation, det.limiar_anomalia)
    X = pd.DataFrame(np.zeros((2, len(FEATURES))), columns=FEATURES)
    X["historico_curto"] = [1, 0]  # sem histórico, não há "habitual" com que comparar
    r = falso.avaliar(X)
    assert r.nivel.tolist() == ["Normal", "Atenção"] and r.alerta_anomalia.tolist() == [False, True]
