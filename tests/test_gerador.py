import pandas as pd
import pytest

from detector.gerador import CATEGORIAS, CIDADES, TIPOS_FRAUDE, gerar_transacoes


@pytest.fixture(scope="module")
def completo():
    return gerar_transacoes()


def test_mesma_semente_gera_os_mesmos_dados():
    pd.testing.assert_frame_equal(gerar_transacoes(80, 60), gerar_transacoes(80, 60))


def test_sementes_diferentes_geram_dados_diferentes():
    assert not gerar_transacoes(80, 60, semente=1).equals(gerar_transacoes(80, 60, semente=2))


def test_colunas_e_valores():
    df = gerar_transacoes(80, 60)
    assert list(df.columns) == ["id_transacao", "cliente", "data_hora", "valor", "cidade", "pais",
                                "categoria", "fraude", "tipo_fraude"]
    assert (df.valor > 0).all() and df.data_hora.is_monotonic_increasing and df.id_transacao.is_unique
    assert set(df.cidade) <= set(CIDADES)
    assert (df.loc[df.fraude == 0, "tipo_fraude"] == "").all()
    assert df.id_transacao.iloc[0] == "T000001" and df.cliente.str.fullmatch(r"C\d{4}").all()
    assert df.data_hora.min() >= pd.Timestamp("2026-01-01") and df.data_hora.max() < pd.Timestamp("2026-03-02")


def test_taxa_e_tipos_de_fraude(completo):
    assert 0.005 <= completo.fraude.mean() <= 0.02
    assert set(completo.loc[completo.fraude == 1, "tipo_fraude"]) == set(TIPOS_FRAUDE)


def test_ruido_legitimo_existe(completo):
    legit = completo[completo.fraude == 0]
    assert (legit.pais == "ZA").any() and (legit.data_hora.dt.hour < 5).any()


def test_categorias_das_fraudes_seguem_as_legitimas(completo):
    compras = completo[completo.categoria.isin(CATEGORIAS)]
    p_legit = compras[compras.fraude == 0].categoria.value_counts(normalize=True)
    fraudes = compras[(compras.fraude == 1) & (compras.tipo_fraude != "conta_tomada")]
    p_fraude = fraudes.categoria.value_counts(normalize=True)
    assert (p_fraude.reindex(p_legit.index, fill_value=0) - p_legit).abs().max() < 0.10


def test_rajada_sao_compras_proximas_do_mesmo_cliente(completo):
    rajadas = completo[completo.tipo_fraude == "rajada"]
    assert rajadas.groupby("cliente").size().min() >= 4


def test_parte_dos_clientes_adere_depois_do_aquecimento(completo):
    primeira = completo.groupby("cliente").data_hora.min()
    tardios = (primeira >= pd.Timestamp("2026-01-31")).mean()
    assert 0.15 <= tardios <= 0.35
    assert (primeira < pd.Timestamp("2026-01-05")).mean() >= 0.6  # os restantes começam logo


def test_clientes_tardios_tem_compras_e_nada_antes_da_adesao(completo):
    primeira = completo.groupby("cliente").data_hora.min()
    tardios = primeira[primeira >= pd.Timestamp("2026-01-31")].index
    assert completo[completo.cliente.isin(tardios)].groupby("cliente").size().min() >= 5


def test_transferencias_levantamentos_e_conta_esvaziada():
    from detector.gerador import CATEGORIA_LEVANTAMENTO, CATEGORIA_TRANSFERENCIA, CATEGORIAS
    df = gerar_transacoes(40, 60)
    assert {CATEGORIA_TRANSFERENCIA, CATEGORIA_LEVANTAMENTO} <= set(df.categoria)
    assert not {CATEGORIA_TRANSFERENCIA, CATEGORIA_LEVANTAMENTO} & set(CATEGORIAS)
    assert (df.tipo_fraude == "conta_esvaziada").any()
    esvaziada = df[df.tipo_fraude == "conta_esvaziada"]
    assert (esvaziada.categoria == CATEGORIA_TRANSFERENCIA).all()
    tomada = df[df.tipo_fraude == "conta_tomada"]
    assert not tomada.categoria.isin([CATEGORIA_TRANSFERENCIA, CATEGORIA_LEVANTAMENTO]).any()
    assert 0.005 <= df.fraude.mean() <= 0.02


def test_simulador_gera_conta_esvaziada():
    from datetime import datetime
    from detector.simulacao import Simulador, clientes_do_treino
    sim = Simulador(clientes_do_treino(20), datetime(2026, 6, 1, 12), semente=1)
    (_, t), = sim._fraude(sim.clientes[0], "conta_esvaziada", datetime(2026, 6, 1, 12))
    assert t["categoria"] == "TRANSFERENCIA" and t["simulacao"] == {"fraude": True, "tipo": "conta_esvaziada"}
