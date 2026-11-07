"""Três classificadores supervisionados e um Isolation Forest, com limiares escolhidos na validação."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .avaliacao import melhor_limiar, metricas
from .perfis import FEATURES

ORDEM_SIMPLICIDADE = ("regressao_logistica", "random_forest", "gradient_boosting")
MARGEM_EMPATE = 0.02
FRACAO_ANOMALIAS = 0.02
NIVEIS = ("Alto risco", "Atenção", "Normal")
NOMES = {
    "regressao_logistica": "Regressão logística",
    "random_forest": "Random Forest",
    "gradient_boosting": "Gradient Boosting (HistGradientBoosting)",
    "isolation_forest": "Isolation Forest",
}


def criar_classificadores() -> dict[str, Pipeline]:
    modelos = {
        "regressao_logistica": LogisticRegression(max_iter=1000, random_state=42),
        "random_forest": RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1),
        "gradient_boosting": HistGradientBoostingClassifier(random_state=42),
    }
    return {nome: Pipeline([("escala", StandardScaler()), ("modelo", m)]) for nome, m in modelos.items()}


def escolher_modelo(f1_validacao: dict[str, float]) -> str:
    """O mais simples entre os que ficam até MARGEM_EMPATE abaixo do maior F1 na validação."""
    melhor = max(f1_validacao.values())
    return next(n for n in ORDEM_SIMPLICIDADE if n in f1_validacao and f1_validacao[n] >= melhor - MARGEM_EMPATE)


def para_escala(valor: np.ndarray, limiar: float) -> np.ndarray:
    """De 0 a 100, com o limiar de decisão sempre em 50."""
    v = np.asarray(valor, dtype=float)
    abaixo = 50 * v / max(limiar, 1e-9)
    acima = 50 + 50 * (v - limiar) / max(1 - limiar, 1e-9)
    return np.clip(np.where(v < limiar, abaixo, acima), 0, 100)


@dataclass
class Detector:
    classificadores: dict[str, Pipeline]
    limiares: dict[str, float]
    escolhido: str
    isolation: IsolationForest
    limiar_anomalia: float

    def pontuacoes(self, X: pd.DataFrame) -> pd.DataFrame:
        dados = X[FEATURES]
        return pd.DataFrame({
            "prob": self.classificadores[self.escolhido].predict_proba(dados)[:, 1],
            "anomalia_bruta": -self.isolation.score_samples(dados),
        }, index=X.index)

    def avaliar(self, X: pd.DataFrame) -> pd.DataFrame:
        p = self.pontuacoes(X)
        limiar = self.limiares[self.escolhido]
        alerta_c = p["prob"].to_numpy() >= limiar
        alerta_a = p["anomalia_bruta"].to_numpy() >= self.limiar_anomalia
        return pd.DataFrame({
            "prob": p["prob"],
            "risco": para_escala(p["prob"], limiar).round(1),
            "anomalia": para_escala(p["anomalia_bruta"], self.limiar_anomalia).round(1),
            "alerta_classificador": alerta_c,
            "alerta_anomalia": alerta_a,
            "nivel": np.where(alerta_c, NIVEIS[0], np.where(alerta_a, NIVEIS[1], NIVEIS[2])),
        }, index=X.index)


def treinar(X_treino: pd.DataFrame, y_treino: np.ndarray, X_val: pd.DataFrame, y_val: np.ndarray) -> tuple[Detector, dict]:
    """Treina com o treino e decide limiares e modelo com a validação. O teste não entra aqui."""
    classificadores, limiares, validacao = criar_classificadores(), {}, {}
    for nome, modelo in classificadores.items():
        modelo.fit(X_treino[FEATURES], y_treino)
        prob = modelo.predict_proba(X_val[FEATURES])[:, 1]
        limiares[nome] = melhor_limiar(y_val, prob)
        validacao[nome] = metricas(y_val, prob, limiares[nome])
    escolhido = escolher_modelo({n: m["f1"] for n, m in validacao.items()})
    isolation = IsolationForest(random_state=42).fit(X_treino[FEATURES])  # sem os rótulos
    anomalia = -isolation.score_samples(X_val[FEATURES])
    limiar_anomalia = float(np.quantile(anomalia, 1 - FRACAO_ANOMALIAS))
    validacao["isolation_forest"] = metricas(y_val, anomalia, limiar_anomalia)
    return Detector(classificadores, limiares, escolhido, isolation, limiar_anomalia), {"validacao": validacao}
