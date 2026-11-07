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
