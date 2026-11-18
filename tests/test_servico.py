import pandas as pd
import pytest

from detector.artefatos import carregar, gravar
from detector.entrada import ler_csv, validar
from detector.explicacao import formatar_decimal, formatar_mt, motivos
from detector.perfis import calcular_features
from detector.servico import avaliar_transacoes

PERFIL = {"valor_mediana": 1450.0, "compras_por_dia": 0.8, "paises": {"MZ": 50}}
SEM_DESVIO = dict(valor_vs_habitual=1.0, hora_incomum=0.0, pais_novo=0, cidade_nova=0, categoria_nova=0,
                  compras_ultima_hora=0, historico_curto=0, hora=14)
TRANSACAO = dict(pais="MZ", cidade="MAPUTO", categoria="MERCEARIA")
CABECALHO = "cliente,data_hora,valor,cidade,pais,categoria\n"


def _linha(**k):
    base = dict(id_transacao="N1", cliente="C0001", data_hora=pd.Timestamp("2026-05-01 14:00"), valor=100.0,
                cidade="MAPUTO", pais="MZ", categoria="MERCEARIA")
    return base | k


# ------------------------------------------------------------------ motivos
def test_formatos_em_portugues():
    assert formatar_mt(1450) == "1.450 MT" and formatar_mt(1450.5) == "1.450,50 MT" and formatar_mt(80) == "80 MT"
    assert formatar_decimal(8.24) == "8,2" and formatar_decimal(1234.5, 0) == "1.234"


def test_sem_desvio_nao_ha_motivo():
    assert motivos(TRANSACAO, SEM_DESVIO, PERFIL) == []


def test_motivo_de_valor_alto():
    m = motivos(TRANSACAO, SEM_DESVIO | {"valor_vs_habitual": 8.2}, PERFIL)
    assert m == ["Valor 8,2 vezes o habitual deste cliente (habitual: 1.450 MT)"]


def test_motivo_de_primeira_compra_no_estrangeiro():
    m = motivos(TRANSACAO | {"pais": "AE"}, SEM_DESVIO | {"pais_novo": 1}, PERFIL)
    assert m == ["Primeira compra deste cliente fora de Moçambique (AE)"]


def test_motivo_de_pais_novo_para_quem_ja_viajou():
    perfil = PERFIL | {"paises": {"MZ": 40, "ZA": 2}}
    assert motivos(TRANSACAO | {"pais": "NG"}, SEM_DESVIO | {"pais_novo": 1}, perfil) == [
        "Primeira compra deste cliente em NG"]


def test_motivos_de_cidade_categoria_hora_e_frequencia():
    feats = SEM_DESVIO | {"cidade_nova": 1, "categoria_nova": 1, "hora_incomum": 0.95, "hora": 3,
                          "compras_ultima_hora": 3}
    m = motivos(TRANSACAO | {"cidade": "BEIRA", "categoria": "RECARGA_TELEMOVEL"}, feats, PERFIL)
    assert m == ["Primeira compra deste cliente em Beira",
                 "Primeira compra deste cliente na categoria recarga de telemóvel",
                 "Compra às 3h; este cliente quase nunca compra a esta hora",
                 "4 compras na última hora; o normal deste cliente é 0,8 por dia"]


def test_historico_curto_e_sem_perfil():
    m = motivos(TRANSACAO, SEM_DESVIO | {"historico_curto": 1, "compras_ultima_hora": 4}, None)
    assert m == ["5 compras na última hora", "Cliente com pouco histórico: a avaliação é menos fiável"]


# ------------------------------------------------------------------ entrada
def test_csv_simples():
    df = validar(ler_csv((CABECALHO + "C0001,2026-05-01 14:30,1450.5,Maputo,MZ,Mercearia\n").encode()))
    assert df.valor.iloc[0] == 1450.5 and df.id_transacao.iloc[0] == "L1"


def test_csv_do_excel_com_ponto_e_virgula_e_virgula_decimal():
    bruto = "cliente;data_hora;valor;cidade;pais;categoria\nC0001;01/05/2026 14:30;1.450,50;Maputo;MZ;Mercearia\n"
    df = validar(ler_csv(bruto.encode("utf-8-sig")))
    assert df.valor.iloc[0] == 1450.5 and df.data_hora.iloc[0] == pd.Timestamp("2026-05-01 14:30")


def test_id_da_transacao_e_mantido_quando_existe():
    bruto = "id_transacao," + CABECALHO + "X9,C1,2026-05-01T14:30,5,a,MZ,b\n"
    assert validar(ler_csv(bruto.encode())).id_transacao.tolist() == ["X9"]


def test_colunas_em_falta_sao_listadas():
    with pytest.raises(ValueError, match="cidade"):
        ler_csv(b"cliente,data_hora,valor,pais,categoria\nC1,2026-05-01 14:30,5,MZ,b\n")


@pytest.mark.parametrize("linhas,trecho", [
    ("C1,2026-05-01 14:30,5,a,MZ,b\nC1,2026-05-01 14:30,-5,a,MZ,b\n", "Linha 3"),
    ("C1,ontem,5,a,MZ,b\n", "Linha 2"),
    ("C1,2026-05-01 14:30,abc,a,MZ,b\n", "Linha 2"),
    ("C1,2026-05-01 14:30,5,,MZ,b\n", "Linha 2"),
])
def test_erros_indicam_a_linha(linhas, trecho):
    with pytest.raises(ValueError, match=trecho):
        validar(ler_csv((CABECALHO + linhas).encode()))


def test_ficheiro_grande_demais_e_recusado():
    with pytest.raises(ValueError, match="5 MB"):
        ler_csv(b"x" * (5 * 1024 * 1024 + 1))


def test_ficheiro_vazio_e_recusado():
    with pytest.raises(ValueError):
        ler_csv(CABECALHO.encode())


# ------------------------------------------------------------------ artefatos
def test_artefatos_incompletos_devolvem_none(tmp_path):
    assert carregar(tmp_path) is None


def test_ida_e_volta_dos_artefatos(tmp_path, artefatos_pequenos):
    gravar(tmp_path, artefatos_pequenos)
    lido = carregar(tmp_path)
    assert lido.detector.escolhido == artefatos_pequenos.detector.escolhido and lido.pasta == tmp_path
    assert len(lido.historico) == len(artefatos_pequenos.historico)


def test_ficheiro_corrompido_devolve_none(tmp_path, artefatos_pequenos):
    gravar(tmp_path, artefatos_pequenos)
    (tmp_path / "detector.joblib").write_bytes(b"lixo")
    assert carregar(tmp_path) is None


# ------------------------------------------------------------------ serviço
def test_resultado_tem_as_colunas_e_a_ordem(artefatos_pequenos):
    novas = pd.DataFrame([_linha(id_transacao="A", valor=50), _linha(id_transacao="B", valor=90000)])
    r = avaliar_transacoes(artefatos_pequenos, novas)
    assert r.id_transacao.tolist() == ["A", "B"]
    assert {"risco", "anomalia", "nivel", "motivos", "historico_curto"} <= set(r.columns)
    assert any("vezes o habitual" in m for m in r.motivos.iloc[1])


def test_data_anterior_ao_fim_do_historico_usa_so_o_passado(artefatos_pequenos):
    hist = artefatos_pequenos.historico
    cliente = hist.cliente.value_counts().index[0]
    meio = hist[hist.cliente == cliente].data_hora.iloc[20] + pd.Timedelta(minutes=1)
    nova = pd.DataFrame([_linha(cliente=cliente, data_hora=meio)])
    esperado = calcular_features(nova, historico=hist[hist.data_hora < meio])
    pd.testing.assert_frame_equal(esperado, calcular_features(nova, historico=hist))
    assert len(avaliar_transacoes(artefatos_pequenos, nova)) == 1


def test_transacao_ja_no_historico_nao_conta_a_si_mesma(artefatos_pequenos):
    hist = artefatos_pequenos.historico
    linha = hist.iloc[[500]].drop(columns=["fraude", "tipo_fraude"])
    sem_ela = calcular_features(linha, historico=hist.drop(index=linha.index))
    r = avaliar_transacoes(artefatos_pequenos, linha)
    com_ela = calcular_features(linha, historico=hist)
    assert com_ela.minutos_desde_anterior.iloc[0] == 0  # sem o cuidado, ela seria o próprio passado
    assert r.risco.iloc[0] == artefatos_pequenos.detector.avaliar(sem_ela).risco.iloc[0]


def test_cliente_novo_em_rajada_conta_a_propria_sequencia(artefatos_pequenos):
    inicio = pd.Timestamp("2026-05-01 10:00")
    rajada = pd.DataFrame([_linha(id_transacao=f"R{i}", cliente="NOVO", valor=60,
                                  data_hora=inicio + pd.Timedelta(minutes=2 * i)) for i in range(5)])
    r = avaliar_transacoes(artefatos_pequenos, rajada)
    assert r.historico_curto.all() and "5 compras na última hora" in r.motivos.iloc[-1]


def test_valor_enorme_e_cidade_nunca_vista_nao_quebram(artefatos_pequenos):
    nova = pd.DataFrame([_linha(valor=1e9, cidade="LICHINGA", categoria="JOIAS")])
    r = avaliar_transacoes(artefatos_pequenos, nova)
    assert 0 <= r.risco.iloc[0] <= 100 and 0 <= r.anomalia.iloc[0] <= 100


def test_usa_so_o_historico_dos_clientes_enviados(artefatos_pequenos, monkeypatch):
    import detector.servico as servico
    vistos = {}
    original = servico.calcular_features

    def espiao(df, historico=None, **opcoes):
        vistos["clientes"] = set(historico["cliente"].astype(str).str.strip())
        return original(df, historico=historico, **opcoes)

    monkeypatch.setattr(servico, "calcular_features", espiao)
    avaliar_transacoes(artefatos_pequenos, pd.DataFrame([_linha(cliente=" C0001 ")]))
    assert vistos["clientes"] == {"C0001"}  # sem isto, cada pedido recalcula o histórico de todos os clientes


# ------------------------------------------------- achados da revisão final
PONTO_VIRGULA = "cliente;data_hora;valor;cidade;pais;categoria\n"


@pytest.mark.parametrize("valor,esperado", [("1234.50", 1234.5), ("1.500", 1500.0), ("1.450,50", 1450.5),
                                            ("2.345.678", 2345678.0), ("80", 80.0)])
def test_ponto_e_virgula_com_ponto_decimal_nao_multiplica_o_valor(valor, esperado):
    df = validar(ler_csv((PONTO_VIRGULA + f"C1;01/05/2026 14:30;{valor};Maputo;MZ;b\n").encode()))
    assert df.valor.iloc[0] == esperado


def test_virgula_com_milhar_ambiguo_e_recusado():
    with pytest.raises(ValueError, match="Linha 2"):
        validar(ler_csv((CABECALHO + "C1,2026-05-01 14:30,1.500,a,MZ,b\n").encode()))


def test_csv_do_excel_em_cp1252_e_lido():
    bruto = PONTO_VIRGULA + "C1;01/05/2026 14:30;150,00;Maputo;MZ;Farmácia\n"
    df = validar(ler_csv(bruto.encode("cp1252")))
    assert df.categoria.iloc[0] == "Farmácia"


def test_linhas_vazias_do_excel_sao_ignoradas():
    bruto = PONTO_VIRGULA + "C1;01/05/2026 14:30;150,00;Maputo;MZ;b\n;;;;;\n;;;;;\n"
    assert len(validar(ler_csv(bruto.encode()))) == 1
    with pytest.raises(ValueError, match="não tem transações"):
        ler_csv((PONTO_VIRGULA + ";;;;;\n").encode())


def test_linha_vazia_no_meio_nao_muda_a_numeracao():
    bruto = PONTO_VIRGULA + "C1;01/05/2026 14:30;150;a;MZ;b\n;;;;;\nC1;ontem;150;a;MZ;b\n"
    with pytest.raises(ValueError, match="Linha 4"):
        validar(ler_csv(bruto.encode()))


# --------------------------------------------- pontos menores da revisão final
@pytest.mark.parametrize("valor", ["1 234,50", "1 234,50", "1 234,50"])
def test_milhares_separados_por_espaco(valor):
    df = validar(ler_csv((PONTO_VIRGULA + f"C1;01/05/2026 14:30;{valor};a;MZ;b\n").encode()))
    assert df.valor.iloc[0] == 1234.5


def test_codigo_do_cliente_nao_depende_de_maiusculas(artefatos_pequenos):
    r = avaliar_transacoes(artefatos_pequenos, pd.DataFrame([_linha(cliente="c0001")]))
    assert not r.historico_curto.iloc[0]


def test_id_igual_de_outra_compra_nao_apaga_o_historico(artefatos_pequenos, monkeypatch):
    import detector.servico as servico
    hist = artefatos_pequenos.historico
    alheia = hist[hist.cliente == "C0001"].iloc[10]
    vistos = {}
    original = servico.calcular_features

    def espiao(df, historico=None, **opcoes):
        vistos["ids"] = set(historico["id_transacao"])
        return original(df, historico=historico, **opcoes)

    monkeypatch.setattr(servico, "calcular_features", espiao)
    nova = _linha(id_transacao=alheia.id_transacao, data_hora=pd.Timestamp("2026-04-29 10:00"), valor=33.0)
    avaliar_transacoes(artefatos_pequenos, pd.DataFrame([nova]))
    assert alheia.id_transacao in vistos["ids"]  # mesmo ID, outra compra: o histórico fica


def test_habitual_do_motivo_usa_so_o_passado(artefatos_pequenos):
    from detector.perfis import perfil_cliente
    hist = artefatos_pequenos.historico
    compras = hist[(hist.cliente == "C0001") & (hist.fraude == 0)]
    quando = compras.data_hora.iloc[30] + pd.Timedelta(minutes=1)
    r = avaliar_transacoes(artefatos_pequenos, pd.DataFrame([_linha(data_hora=quando, valor=compras.valor.median() * 50)]))
    esperado = perfil_cliente(hist[hist.data_hora < quando], "C0001")["valor_mediana"]
    assert f"(habitual: {formatar_mt(esperado)})" in r.motivos.iloc[0][0]


def test_motivo_de_categoria_nova_em_transferencia_e_levantamento():
    f = SEM_DESVIO | {"categoria_nova": 1}
    assert motivos(TRANSACAO | {"categoria": "TRANSFERENCIA"}, f, PERFIL) == ["Primeira transferência deste cliente"]
    assert motivos(TRANSACAO | {"categoria": "LEVANTAMENTO"}, f, PERFIL) == ["Primeiro levantamento deste cliente"]
