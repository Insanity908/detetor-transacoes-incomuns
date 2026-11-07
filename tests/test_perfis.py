import pandas as pd
import pytest

from detector.gerador import gerar_transacoes
from detector.perfis import FEATURES, MINUTOS_MAX, calcular_features, perfil_cliente


def _linha(i, quando, valor=100.0, cidade="MAPUTO", pais="MZ", categoria="MERCEARIA", cliente="C1", fraude=0):
    return dict(id_transacao=f"T{i}", cliente=cliente, data_hora=pd.Timestamp(quando), valor=valor,
                cidade=cidade, pais=pais, categoria=categoria, fraude=fraude)


def _cinco_normais(hora=14):
    return [_linha(i, f"2026-01-0{i + 1} {hora}:00") for i in range(5)]


def test_colunas_na_ordem_do_plano():
    f = calcular_features(pd.DataFrame(_cinco_normais()))
    assert list(f.columns) == FEATURES


def test_valor_vs_habitual_e_novidades():
    df = pd.DataFrame(_cinco_normais() + [_linha(9, "2026-01-07 14:00", 800, "BEIRA", "ZA", "VIAGEM")])
    f = calcular_features(df).iloc[-1]
    assert f.valor_vs_habitual == 8.0 and f.pais_novo == f.cidade_nova == f.categoria_nova == 1
    assert f.historico_curto == 0 and f.valor == 800 and f.hora == 14


def test_historico_curto_fica_neutro():
    df = pd.DataFrame(_cinco_normais()[:2] + [_linha(9, "2026-01-07 03:00", 800, "BEIRA", "ZA", "VIAGEM")])
    f = calcular_features(df).iloc[-1]
    assert f.historico_curto == 1 and f.valor_vs_habitual == 1 and f.hora_incomum == 0
    assert f.pais_novo == f.cidade_nova == f.categoria_nova == 0


def test_frequencia_recente_e_tempo_desde_a_anterior():
    df = pd.DataFrame([_linha(i, f"2026-01-01 10:{10 * i:02d}") for i in range(4)])
    f = calcular_features(df)
    assert f.compras_ultima_hora.tolist() == [0, 1, 2, 3] and f.compras_24h.tolist() == [0, 1, 2, 3]
    assert f.minutos_desde_anterior.tolist() == [MINUTOS_MAX, 10, 10, 10]


def test_janela_de_uma_hora_exclui_compras_mais_antigas():
    df = pd.DataFrame([_linha(0, "2026-01-01 08:00"), _linha(1, "2026-01-01 10:00")])
    assert calcular_features(df).compras_ultima_hora.tolist() == [0, 0]


def test_minutos_desde_anterior_tem_limite():
    df = pd.DataFrame([_linha(0, "2026-01-01 08:00"), _linha(1, "2026-02-01 08:00")])
    assert calcular_features(df).minutos_desde_anterior.iloc[-1] == MINUTOS_MAX


def test_hora_incomum():
    df = pd.DataFrame(_cinco_normais() + [_linha(8, "2026-01-08 03:00"), _linha(9, "2026-01-09 15:00")])
    f = calcular_features(df)
    assert f.hora_incomum.iloc[-2] == 1.0  # nenhuma compra anterior perto das 3h
    assert f.hora_incomum.iloc[-1] == pytest.approx(1 / 6)  # só a compra das 3h fica longe das 15h


def test_hora_incomum_e_circular():
    df = pd.DataFrame([_linha(i, f"2026-01-0{i + 1} 23:00") for i in range(5)] + [_linha(9, "2026-01-07 01:00")])
    assert calcular_features(df).hora_incomum.iloc[-1] == 0.0  # 23h e 1h estão a 2 horas


def test_clientes_nao_se_misturam():
    df = pd.DataFrame(_cinco_normais() + [_linha(9, "2026-01-07 14:00", 800, cliente="C2")])
    f = calcular_features(df).iloc[-1]
    assert f.historico_curto == 1 and f.compras_24h == 0


def test_futuro_nao_muda_o_passado():
    df = gerar_transacoes(60, 60)
    alterado = df.copy()
    alterado.loc[alterado.index[-50:], "valor"] *= 100
    alterado.loc[alterado.index[-50:], "pais"] = "NG"
    pd.testing.assert_frame_equal(calcular_features(df).iloc[:-50], calcular_features(alterado).iloc[:-50])


def test_historico_externo_conta_como_passado():
    df = gerar_transacoes(40, 30)
    historico, novas = df.iloc[:-20], df.iloc[-20:]
    pd.testing.assert_frame_equal(calcular_features(df).iloc[-20:], calcular_features(novas, historico=historico))


def test_indice_original_e_preservado():
    df = pd.DataFrame(_cinco_normais()).iloc[::-1]
    df.index = [10, 20, 30, 40, 50]
    f = calcular_features(df)
    assert f.index.tolist() == [10, 20, 30, 40, 50] and f.loc[50, "compras_24h"] == 0


def test_grafia_nao_muda_o_resultado():
    a = pd.DataFrame(_cinco_normais() + [_linha(9, "2026-01-07 14:00", 800)])
    b = pd.DataFrame(_cinco_normais() + [_linha(9, "2026-01-07 14:00", 800, " maputo ", " mz", "mercearia ",
                                                cliente=" C1 ")])
    assert calcular_features(a).iloc[-1].equals(calcular_features(b).iloc[-1])


def test_perfil_do_cliente():
    hist = pd.DataFrame(_cinco_normais() + [_linha(9, "2026-01-07 03:00", 9999, fraude=1)])
    p = perfil_cliente(hist, "C1")
    assert p["n_compras"] == 5 and p["valor_mediana"] == 100 and len(p["horas"]) == 24 and p["horas"][14] == 5
    assert p["paises"] == {"MZ": 5} and p["categorias"] == {"MERCEARIA": 5}
    assert perfil_cliente(hist, "NAO_EXISTE") is None


@pytest.mark.parametrize("cliente", [" C1", "C1 "])
def test_perfil_aceita_espacos(cliente):
    assert perfil_cliente(pd.DataFrame(_cinco_normais()), cliente)["n_compras"] == 5
