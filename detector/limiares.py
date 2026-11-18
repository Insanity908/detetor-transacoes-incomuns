"""Limiares de alerta ajustáveis pelo supervisor, na escala de 0 a 100 mostrada na interface.

Por omissão, 50 nas duas escalas: é o limiar que o treino escolheu (o que maximiza o F1 na validação).
Subir o corte dá menos alertas (menos falsos alarmes, mas mais fraudes passam); descer dá o contrário.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy.orm import sessionmaker

from .banco import Ajuste

CHAVE = "limiares"
MINIMO, MAXIMO = 1, 99
CORTES_COMPARADOS = (30, 40, 50, 60, 70)


@dataclass(frozen=True)
class Cortes:
    risco: float = 50.0  # risco >= corte: "Alto risco"
    anomalia: float = 50.0  # anomalia >= corte (com histórico): "Atenção"
    recusa: float = 95.0  # risco >= corte: o banco recusa a operação sem esperar por análise

    def __post_init__(self):
        for nome, valor in asdict(self).items():
            if not MINIMO <= float(valor) <= MAXIMO:
                raise ValueError(f"O limite de {nome} tem de estar entre {MINIMO} e {MAXIMO}.")
        if float(self.recusa) < float(self.risco):
            raise ValueError("O limite de recusa tem de ser maior ou igual ao de alto risco.")

    @property
    def padrao(self) -> bool:
        return self == Cortes()


def ler(sessoes: sessionmaker) -> tuple[Cortes, Ajuste | None]:
    with sessoes() as sessao:
        ajuste = sessao.get(Ajuste, CHAVE)
    if ajuste is None:
        return Cortes(), None
    try:
        dados = json.loads(ajuste.valor)
        if "recusa" not in dados:  # gravado antes do corte de recusa: 95, ou o risco se este for mais alto
            dados["recusa"] = max(95.0, float(dados.get("risco", 50)))
        return Cortes(**dados), ajuste
    except (ValueError, TypeError):
        return Cortes(), ajuste


def gravar(sessoes: sessionmaker, cortes: Cortes, por: str) -> None:
    with sessoes.begin() as sessao:
        sessao.merge(Ajuste(chave=CHAVE, valor=json.dumps(asdict(cortes)),
                            alterado_em=datetime.now().replace(microsecond=0), alterado_por=por[:50]))


def efeito(teste_avaliado: pd.DataFrame, cortes: list[Cortes]) -> list[dict]:
    """O que cada combinação de cortes teria dado no teste da versão publicada (que tem os rótulos)."""
    if teste_avaliado is None or not len(teste_avaliado) or "fraude" not in teste_avaliado.columns:
        return []
    y = teste_avaliado["fraude"].to_numpy() == 1
    risco = teste_avaliado["risco"].to_numpy(dtype=float)
    anomalia = teste_avaliado["anomalia"].to_numpy(dtype=float)
    com_historico = ~teste_avaliado["historico_curto"].to_numpy(dtype=bool)
    linhas = []
    for c in cortes:
        alerta = (risco >= c.risco) | ((anomalia >= c.anomalia) & com_historico)
        apanhadas = int((alerta & y).sum())
        linhas.append({"cortes": c, "alertas": int(alerta.sum()), "fraudes": int(y.sum()), "apanhadas": apanhadas,
                       "falsos": int((alerta & ~y).sum()),
                       "recall": apanhadas / y.sum() if y.sum() else None,
                       "precisao": apanhadas / alerta.sum() if alerta.sum() else None,
                       "alertas_por_mil": 1000 * alerta.mean()})
    return linhas


def metricas_alerta(y: np.ndarray, nivel: np.ndarray) -> dict:
    """Recall, precisão e F1 do que o analista vê: alerta é qualquer nível diferente de Normal."""
    y = np.asarray(y).astype(bool)
    alerta = np.asarray(nivel) != "Normal"
    apanhadas = int((alerta & y).sum())
    recall = apanhadas / y.sum() if y.sum() else None
    precisao = apanhadas / alerta.sum() if alerta.sum() else None
    f1 = (2 * recall * precisao / (recall + precisao)) if recall and precisao else (0.0 if y.sum() else None)
    legitimas = int((~y).sum())
    falsos = int((alerta & ~y).sum())
    return {"transacoes": int(len(y)), "fraudes": int(y.sum()), "alertas": int(alerta.sum()), "apanhadas": apanhadas,
            "recall": recall, "precisao": precisao, "f1": f1, "falsos": falsos,
            "falsos_por_mil": 1000 * falsos / legitimas if legitimas else None}
