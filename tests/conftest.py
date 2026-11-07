from pathlib import Path

import pandas as pd
import pytest

from detector.artefatos import Artefatos
from detector.avaliacao import dividir_periodos
from detector.gerador import gerar_transacoes
from detector.modelos import treinar
from detector.perfis import calcular_features


@pytest.fixture(scope="session")
def artefatos_pequenos():
    """Modelos treinados com 80 clientes, sem resultados nem gráficos: rápido para testar o serviço e a interface."""
    df = gerar_transacoes(80, 120)
    X = calcular_features(df)
    p = dividir_periodos(df["data_hora"])
    y = df["fraude"].to_numpy()
    detector, _ = treinar(X[p["treino"]], y[p["treino"]], X[p["validacao"]], y[p["validacao"]])
    return Artefatos(detector, df, pd.DataFrame(), {}, Path("."))


@pytest.fixture(scope="session")
def pasta_pequena(artefatos_pequenos, tmp_path_factory):
    from detector.artefatos import gravar
    pasta = tmp_path_factory.mktemp("modelos_pequenos")
    gravar(pasta, artefatos_pequenos)
    return pasta


@pytest.fixture()
def cliente(pasta_pequena):
    from app import criar_app
    return criar_app(pasta_pequena).test_client()


@pytest.fixture(scope="session")
def pasta_treinada(tmp_path_factory):
    """Treino completo com 120 clientes: tem resultados, gráficos e transações de teste avaliadas."""
    from treinar import executar
    raiz = tmp_path_factory.mktemp("treinada")
    executar(raiz / "modelos", raiz / "data", None, n_clientes=120)
    return raiz / "modelos"


@pytest.fixture()
def cliente_treinado(pasta_treinada):
    from app import criar_app
    return criar_app(pasta_treinada).test_client()
