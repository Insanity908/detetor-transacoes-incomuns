"""Histórico anterior importado para clientes novos: só para o detetor."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from detector import clientes, contas_bancarias, historico
from detector.autorizacao import PedidoDebito, autorizar
from detector.banco import Alerta, Movimento, TransacaoAvaliada
from tests.conftest import criar_cliente

AGORA = datetime(2026, 11, 19, 12, 0, 0)


@pytest.fixture()
def app(pasta_pequena):
    from app import criar_app
    return criar_app(pasta_pequena)


@pytest.fixture()
def sessoes(app):
    return app.config["SESSOES"]


def _novo(sessoes, nuit="123456789"):
    return clientes.criar(sessoes, clientes.validar({
        "nome": "Ana Teste", "data_nascimento": "1990-01-01", "tipo_documento": "NUIT", "numero_documento": nuit,
        "telefone": "841234567", "email": "", "cidade": "MAPUTO", "morada": "Rua 1"}), por="teste")


def _csv(linhas, sep=",", cabecalho="data_hora,valor,cidade,pais,categoria"):
    return ("\n".join([cabecalho.replace(",", sep), *linhas]) + "\n").encode("utf-8")


LINHAS = ["2026-11-01 10:00,1500,MAPUTO,MZ,MERCEARIA", "2026-11-05 12:30,800,MAPUTO,MZ,RESTAURANTE",
          "2026-11-10 18:00,2500.50,MATOLA,MZ,COMBUSTIVEL"]


def test_importar_csv_simples_e_resumo(sessoes):
    c = _novo(sessoes)
    r = historico.importar(sessoes, c.id, _csv(LINHAS), "teste", agora=AGORA)
    assert (r.inseridas, r.repetidas) == (3, 0)
    res = historico.resumo(sessoes, c.id)
    assert res.total == 3 and res.primeira == datetime(2026, 11, 1, 10) and res.ultima == datetime(2026, 11, 10, 18)


def test_importar_formato_excel_pt(sessoes):
    c = _novo(sessoes)
    r = historico.importar(sessoes, c.id, _csv(["01/11/2026 10:00;1.234,50;MAPUTO;MZ;MERCEARIA"], sep=";"),
                           "teste", agora=AGORA)
    assert r.inseridas == 1
    df = historico.para_detetor(sessoes, {c.codigo}, AGORA - timedelta(days=90))
    assert df["valor"].iloc[0] == pytest.approx(1234.50)


def test_repetidas_sao_ignoradas(sessoes):
    c = _novo(sessoes)
    historico.importar(sessoes, c.id, _csv(LINHAS), "teste", agora=AGORA)
    r = historico.importar(sessoes, c.id, _csv(LINHAS), "teste", agora=AGORA)
    assert (r.inseridas, r.repetidas) == (0, 3)


@pytest.mark.parametrize("linha", ["2026-11-25 10:00,100,MAPUTO,MZ,MERCEARIA",  # futura
                                   "2026-08-01 10:00,100,MAPUTO,MZ,MERCEARIA",  # mais de 90 dias
                                   "2026-11-01 10:00,100,MAPUTO,MZ,BRINQUEDOS"])  # categoria desconhecida
def test_ficheiro_com_erros_e_recusado_por_inteiro(sessoes, linha):
    c = _novo(sessoes)
    with pytest.raises(ValueError, match="Linha"):
        historico.importar(sessoes, c.id, _csv([LINHAS[0], linha]), "teste", agora=AGORA)
    assert historico.resumo(sessoes, c.id) is None


def test_coluna_cliente_de_outro_cliente_recusa(sessoes):
    c = _novo(sessoes)
    conteudo = _csv(["C9999,2026-11-01 10:00,100,MAPUTO,MZ,MERCEARIA"],
                    cabecalho="cliente,data_hora,valor,cidade,pais,categoria")
    with pytest.raises(ValueError, match="não é o desta ficha"):
        historico.importar(sessoes, c.id, conteudo, "teste", agora=AGORA)


def test_limite_de_linhas(sessoes):
    c = _novo(sessoes)
    linhas = [f"2026-11-01 10:{i % 60:02d}:{i // 60 % 60:02d},{100 + i},MAPUTO,MZ,MERCEARIA" for i in range(2001)]
    with pytest.raises(ValueError, match="máximo é 2000"):
        historico.importar(sessoes, c.id, _csv(linhas), "teste", agora=AGORA)


def test_gerar_exemplo_coerente_e_deterministico(sessoes, pasta_pequena, tmp_path, monkeypatch):
    c = _novo(sessoes)
    n = historico.gerar_exemplo(sessoes, c.id, "teste", agora=AGORA)
    assert n > 30
    df = historico.para_detetor(sessoes, {c.codigo}, AGORA - timedelta(days=90))
    assert (df["data_hora"] < AGORA).all() and (df["data_hora"] >= AGORA - timedelta(days=90)).all()
    assert set(df["pais"]) == {"MZ"} and set(df["cidade"]) == {"MAPUTO"}
    historico.apagar(sessoes, c.id)
    assert historico.gerar_exemplo(sessoes, c.id, "teste", agora=AGORA) == n  # mesma semente, mesmas linhas


def test_para_detetor_formato_e_apagar(sessoes):
    c = _novo(sessoes)
    historico.importar(sessoes, c.id, _csv(LINHAS), "teste", agora=AGORA)
    df = historico.para_detetor(sessoes, {c.codigo}, AGORA - timedelta(days=90))
    assert list(df.columns) == historico.COLUNAS_DETETOR and set(df["cliente"]) == {c.codigo}
    assert df["id_transacao"].str.startswith("HIST-").all() and (df["fraude"] == 0).all()
    assert historico.apagar(sessoes, c.id) == 3 and historico.resumo(sessoes, c.id) is None
    assert historico.para_detetor(sessoes, set(), AGORA).empty


# ---------------------------------------------------------------- detetor
def _pagar(app, c, valor=50_000):
    return autorizar(app.config["SESSOES"], app.config["ARTEFATOS"], [PedidoDebito(
        conta_id=c.conta.id, tipo="compra", valor=valor, momento=datetime.now().replace(microsecond=0),
        cidade="MAPUTO", pais="MZ", categoria="MERCEARIA", canal="cliente", por="teste")],
        app.config["CORTES"], "banco")[0]


def _curto(sessoes):
    with sessoes() as s:
        return s.scalars(select(TransacaoAvaliada.historico_curto).order_by(TransacaoAvaliada.id.desc())).first()


def _contagens(sessoes):
    with sessoes() as s:
        return tuple(s.scalar(select(func.count()).select_from(t)) for t in (TransacaoAvaliada, Alerta, Movimento))


def test_autorizar_usa_historico_importado(app, sessoes):
    c = _novo(sessoes)
    contas_bancarias.depositar(sessoes, c.conta.id, 1_000_000, "balcao", "teste")
    _pagar(app, c)
    assert _curto(sessoes) is True
    historico.gerar_exemplo(sessoes, c.id, "teste")
    _pagar(app, c)
    assert _curto(sessoes) is False


def test_historico_nao_contamina(app, sessoes):
    c = _novo(sessoes)
    contas_bancarias.depositar(sessoes, c.conta.id, 1_000_000, "balcao", "teste")
    antes, saldo = _contagens(sessoes), clientes.obter(sessoes, c.id).conta.saldo
    historico.gerar_exemplo(sessoes, c.id, "teste")
    assert _contagens(sessoes) == antes
    assert clientes.obter(sessoes, c.id).conta.saldo == saldo
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_apagar_repoe_cliente_novo(app, sessoes):
    c = _novo(sessoes)
    contas_bancarias.depositar(sessoes, c.conta.id, 1_000_000, "balcao", "teste")
    historico.gerar_exemplo(sessoes, c.id, "teste")
    historico.apagar(sessoes, c.id)
    _pagar(app, c)
    assert _curto(sessoes) is True


# ---------------------------------------------------------------- balcão
def test_balcao_importa_gera_e_apaga(app, sessoes):
    import io
    c = _novo(sessoes)
    b = criar_cliente(app, "balcao")
    hoje = datetime.now()
    linhas = [f"{(hoje - timedelta(days=d)):%Y-%m-%d} 10:00,1500,MAPUTO,MZ,MERCEARIA" for d in (3, 5, 7)]
    r = b.post(f"/balcao/clientes/{c.id}/historico", data={"ficheiro": (io.BytesIO(_csv(linhas)), "h.csv")},
               content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200 and "Histórico importado: 3 transações" in r.get_data(as_text=True)
    r = b.post(f"/balcao/clientes/{c.id}/historico", data={"ficheiro": (io.BytesIO(b"lixo"), "h.csv")},
               content_type="multipart/form-data")
    assert r.status_code == 400 and "Histórico não importado" in r.get_data(as_text=True)
    assert b.post(f"/balcao/clientes/{c.id}/historico/exemplo", follow_redirects=True).status_code == 200
    r = b.post(f"/balcao/clientes/{c.id}/historico/apagar", follow_redirects=True)
    assert "Sem histórico importado" in r.get_data(as_text=True)
    analista = criar_cliente(app, "analista")
    assert analista.post(f"/balcao/clientes/{c.id}/historico/exemplo").status_code == 403
