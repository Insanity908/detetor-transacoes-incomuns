"""Avalia transações novas com o histórico guardado: features, modelos e motivos."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .artefatos import Artefatos
from .explicacao import motivos
from .perfis import calcular_features, normalizar, perfil_cliente


def avaliar_transacoes(artefatos: Artefatos, transacoes: pd.DataFrame) -> pd.DataFrame:
    """Recebe transações já validadas e devolve-as, pela mesma ordem, com risco, nível e motivos."""
    historico = artefatos.historico
    enviadas = normalizar(transacoes)
    historico = normalizar(historico)
    historico = historico[historico["cliente"].isin(set(enviadas["cliente"]))]  # só o passado de quem foi enviado
    if "id_transacao" in historico.columns:  # a mesma transação já guardada não pode ser o próprio passado
        chave = lambda d: list(zip(d["id_transacao"].astype(str), d["cliente"], d["data_hora"]))
        repetidas = set(chave(enviadas))
        manter = np.array([c not in repetidas for c in chave(historico)], dtype=bool)  # lista vazia seria colunas
        historico = historico[manter]
    features = calcular_features(transacoes, historico=historico)
    return montar_resultado(transacoes, features, artefatos.detector.avaliar(features), historico)


def montar_resultado(transacoes: pd.DataFrame, features: pd.DataFrame, avaliacao: pd.DataFrame,
                     historico: pd.DataFrame) -> pd.DataFrame:
    """Junta transações, avaliação e motivos.

    O perfil de cada cliente usa só as compras legítimas anteriores à primeira transação enviada dele,
    para o "habitual" dos motivos não incluir compras do futuro.
    """
    normalizadas = normalizar(transacoes)
    hist_normalizado = normalizar(historico)
    por_cliente = {c: g for c, g in hist_normalizado.groupby("cliente")}
    inicio = normalizadas.groupby("cliente")["data_hora"].min()
    perfis = {c: perfil_cliente(por_cliente[c][por_cliente[c]["data_hora"] < inicio[c]], c)
              if c in por_cliente else None for c in normalizadas["cliente"].unique()}
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
