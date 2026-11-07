"""Divisão por tempo e métricas da classe fraude."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import (accuracy_score, average_precision_score, f1_score, precision_recall_curve,
                             precision_score, recall_score)

from .gerador import INICIO

DIAS_AQUECIMENTO = 30
FRACAO_TREINO, FRACAO_VALIDACAO = 0.64, 0.16


def dividir_periodos(datas: pd.Series, inicio: str = INICIO) -> dict[str, np.ndarray]:
    """Aquecimento (só histórico), treino, validação e teste, por dias e sem sobreposição."""
    dia = (pd.to_datetime(datas).dt.normalize() - pd.Timestamp(inicio)).dt.days.to_numpy() + 1
    n = int(dia.max()) - DIAS_AQUECIMENTO
    fim_treino = DIAS_AQUECIMENTO + round(FRACAO_TREINO * n)
    fim_validacao = fim_treino + round(FRACAO_VALIDACAO * n)
    return {
        "aquecimento": dia <= DIAS_AQUECIMENTO,
        "treino": (dia > DIAS_AQUECIMENTO) & (dia <= fim_treino),
        "validacao": (dia > fim_treino) & (dia <= fim_validacao),
        "teste": dia > fim_validacao,
    }


def melhor_limiar(y: np.ndarray, pontuacao: np.ndarray) -> float:
    """O limiar que maximiza o F1 da classe fraude; sem fraudes, 1,0."""
    y = np.asarray(y)
    if not y.sum():
        return 1.0
    precisao, recall, limiares = precision_recall_curve(y, pontuacao)
    with np.errstate(invalid="ignore", divide="ignore"):
        f1 = np.nan_to_num(2 * precisao[:-1] * recall[:-1] / (precisao[:-1] + recall[:-1]))
    return float(limiares[int(np.argmax(f1))])


def metricas(y: np.ndarray, pontuacao: np.ndarray, limiar: float) -> dict:
    y = np.asarray(y)
    alerta = np.asarray(pontuacao) >= limiar
    tem_fraude = bool(y.sum())
    return {
        "precisao": float(precision_score(y, alerta, zero_division=0)),
        "recall": float(recall_score(y, alerta, zero_division=0)) if tem_fraude else None,
        "f1": float(f1_score(y, alerta, zero_division=0)),
        "pr_auc": float(average_precision_score(y, pontuacao)) if tem_fraude else None,
        "acuracia": float(accuracy_score(y, alerta)),
        "alertas": int(alerta.sum()),
    }
