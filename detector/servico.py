"""Avalia transações novas com o histórico guardado: features, modelos e motivos."""
from __future__ import annotations

import threading

import numpy as np
import pandas as pd

from .artefatos import Artefatos
from .explicacao import motivos
from .perfis import calcular_features, normalizar, perfil_cliente


_cache = {"historico": None, "por_cliente": {}}
_trinco = threading.Lock()


def _historico_por_cliente(historico: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """O histórico do treino já normalizado e separado por cliente, calculado uma vez por conjunto de modelos.

    Normalizar as ~70 mil linhas a cada pedido custava dezenas de milissegundos por transação na API.
    """
    with _trinco:
        if _cache["historico"] is not historico:
            _cache["por_cliente"] = {c: g for c, g in normalizar(historico).groupby("cliente", sort=False)}
            _cache["historico"] = historico
        return _cache["por_cliente"]


def avaliar_transacoes(artefatos: Artefatos, transacoes: pd.DataFrame,
                       historico_extra: pd.DataFrame | None = None, cortes=None) -> pd.DataFrame:
    """Recebe transações já validadas e devolve-as, pela mesma ordem, com risco, nível e motivos.

    historico_extra junta ao histórico do treino as transações recebidas antes (pela API), para que uma rajada
    repartida por vários pedidos, ou os hábitos recentes do cliente, também contem.
    """
    enviadas = normalizar(transacoes)
    clientes = set(enviadas["cliente"])
    por_cliente = _historico_por_cliente(artefatos.historico)
    partes = [por_cliente[c] for c in sorted(clientes) if c in por_cliente]  # só o passado de quem foi enviado
    if historico_extra is not None and len(historico_extra):
        extra = normalizar(historico_extra)
        extra = extra[extra["cliente"].isin(clientes)]
        if partes and "id_transacao" in extra.columns:  # depois de um re-treino, o histórico já inclui parte da API
            ja = set(pd.concat(partes)["id_transacao"].astype(str)) if "id_transacao" in partes[0].columns else set()
            extra = extra[~extra["id_transacao"].astype(str).isin(ja)]
        partes.append(extra)
    historico = (pd.concat(partes, ignore_index=True) if partes
                 else normalizar(artefatos.historico.iloc[:0]))
    if "id_transacao" in historico.columns:  # a mesma transação já guardada não pode ser o próprio passado
        chave = lambda d: list(zip(d["id_transacao"].astype(str), d["cliente"], d["data_hora"]))
        repetidas = set(chave(enviadas))
        manter = np.array([c not in repetidas for c in chave(historico)], dtype=bool)  # lista vazia seria colunas
        historico = historico[manter]
    features = calcular_features(transacoes, historico=historico)
    return montar_resultado(transacoes, features, artefatos.detector.avaliar(features, cortes), historico)


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
