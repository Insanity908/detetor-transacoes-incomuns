"""Testes de carga. Os rápidos correm sempre; o com o servidor real (waitress) só com `pytest -m carga`."""
import socket
import threading
from datetime import datetime

import pytest
from flask.testing import FlaskClient

import carga
from app import criar_app
from detector import chaves_api


def test_correr_conta_tempos_e_erros():
    respostas = iter([200, 200, 429, 200, 500] * 10)
    trinco = threading.Lock()

    def enviar(corpo):
        with trinco:
            return next(respostas)
    transacoes = [{"id_transacao": f"T{i}"} for i in range(50)]
    r = carga.correr(enviar, transacoes, clientes=3, lote=5, duracao=5).resumo()
    assert r["pedidos"] == 10 and r["pedidos_ok"] == 6 and r["transacoes"] == 30
    assert r["estados"] == {200: 6, 429: 2, 500: 2} and r["mediana_ms"] is not None


def test_transacoes_geradas_sao_unicas_e_validas():
    from detector.api import validar_pedido
    transacoes = carga.gerar_transacoes(300, datetime(2026, 5, 1))
    assert len(transacoes) == 300 and len({t["id_transacao"] for t in transacoes}) == 300
    validar_pedido({"transacoes": transacoes[:200]}, agora=datetime(2026, 6, 1))


def test_carga_em_paralelo_na_aplicacao(pasta_pequena):
    """Vários clientes em simultâneo: nenhuma transação se perde nem é gravada duas vezes."""
    app = criar_app(pasta_pequena)
    texto, _ = chaves_api.criar(app.config["SESSOES"], "Carga", "t", 60000)

    def enviar(corpo):
        r = FlaskClient(app).post("/api/v1/transacoes", json=corpo, headers={"Authorization": f"Bearer {texto}"})
        return r.status_code
    transacoes = carga.gerar_transacoes(400, datetime(2026, 5, 1))
    for t in transacoes:  # os clientes da fixture pequena são só 80
        t["cliente"] = f"C{int(t['cliente'][1:]) % 80 + 1:04d}"
    r = carga.correr(enviar, transacoes, clientes=4, lote=25, duracao=120).resumo()
    assert r["estados"] == {200: 16} and r["transacoes"] == 400
    from sqlalchemy import func, select
    from detector.banco import TransacaoAvaliada
    with app.config["SESSOES"]() as s:
        assert s.scalar(select(func.count(TransacaoAvaliada.id))) == 400


@pytest.mark.carga
def test_carga_no_servidor_waitress(pasta_pequena, capsys):
    """O servidor de produção aguenta vários clientes em paralelo sem erros (os números ficam no relatório)."""
    from waitress.server import create_server
    app = criar_app(pasta_pequena)
    texto, _ = chaves_api.criar(app.config["SESSOES"], "Carga", "t", 60000)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    servidor = create_server(app, host="127.0.0.1", port=porta, threads=4)
    thread = threading.Thread(target=servidor.run, daemon=True)
    thread.start()
    try:
        transacoes = carga.gerar_transacoes(3000, datetime(2026, 5, 1))
        for t in transacoes:
            t["cliente"] = f"C{int(t['cliente'][1:]) % 80 + 1:04d}"
        url = f"http://127.0.0.1:{porta}"
        r = carga.correr(lambda corpo: carga.enviar_urllib(url, texto, corpo), transacoes, clientes=8, lote=20,
                         duracao=20).resumo()
    finally:
        servidor.close()
    with capsys.disabled():
        print(f"\ncarga: {r}")
    assert set(r["estados"]) == {200} and r["transacoes"] > 0


def test_otimizacao_das_features_nao_muda_nada(artefatos_pequenos):
    """Calcular só as linhas novas (com o histórico à parte) dá o mesmo que calcular tudo junto."""
    import pandas as pd
    from detector.perfis import _features_do_cliente, calcular_features, normalizar
    historico = artefatos_pequenos.historico
    novas = historico.tail(400)
    passado = historico.iloc[:-400]
    separado = calcular_features(novas, historico=passado)
    junto = calcular_features(pd.concat([passado, novas]))
    pd.testing.assert_frame_equal(separado, junto.loc[novas.index])
    pd.testing.assert_frame_equal(separado, calcular_features(novas, historico=normalizar(passado),
                                                              historico_normalizado=True))
    grupo = normalizar(historico[historico["cliente"] == "C0001"]).sort_values("data_hora", kind="stable")
    mascara = (pd.Series(range(len(grupo))) % 3 == 0).to_numpy()
    assert (_features_do_cliente(grupo, mascara)[mascara] == _features_do_cliente(grupo)[mascara]).all()
