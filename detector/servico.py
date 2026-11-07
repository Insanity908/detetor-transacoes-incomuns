"""Avalia transações novas com o histórico guardado: features, modelos e motivos."""
from __future__ import annotations

import pandas as pd

from .artefatos import Artefatos
from .explicacao import motivos
from .perfis import calcular_features, normalizar, perfil_cliente


def avaliar_transacoes(artefatos: Artefatos, transacoes: pd.DataFrame) -> pd.DataFrame:
    """Recebe transações já validadas e devolve-as, pela mesma ordem, com risco, nível e motivos."""
    historico = artefatos.historico
    if "id_transacao" in historico.columns:  # uma transação já guardada não pode ser o próprio passado
        historico = historico[~historico["id_transacao"].isin(transacoes["id_transacao"])]
    clientes = set(transacoes["cliente"].astype(str).str.strip())  # só o passado de quem foi enviado
    historico = historico[historico["cliente"].astype(str).str.strip().isin(clientes)]
    features = calcular_features(transacoes, historico=historico)
    return montar_resultado(transacoes, features, artefatos.detector.avaliar(features), historico)


def montar_resultado(transacoes: pd.DataFrame, features: pd.DataFrame, avaliacao: pd.DataFrame,
                     historico: pd.DataFrame) -> pd.DataFrame:
    """Junta transações, avaliação e motivos; os perfis vêm das compras legítimas do histórico."""
    normalizadas = normalizar(transacoes)
    hist_normalizado = normalizar(historico)
    por_cliente = {c: g for c, g in hist_normalizado.groupby("cliente")}
    perfis = {c: perfil_cliente(por_cliente[c], c) if c in por_cliente else None
              for c in normalizadas["cliente"].unique()}
    lista_motivos = [
        motivos(linha, features.loc[indice].to_dict(), perfis[linha["cliente"]])
        for indice, linha in zip(normalizadas.index, normalizadas.to_dict("records"))
    ]
    resultado = transacoes.copy()
    resultado["risco"] = avaliacao["risco"]
    resultado["anomalia"] = avaliacao["anomalia"]
    resultado["nivel"] = avaliacao["nivel"]
    resultado["motivos"] = pd.Series(lista_motivos, index=transacoes.index, dtype=object)
    resultado["historico_curto"] = features["historico_curto"].astype(bool)
    return resultado
