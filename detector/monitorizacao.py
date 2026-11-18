"""Monitorização do desempenho da versão publicada nas transações recentes que já têm rótulo.

Compara o que o analista vê (alerta = nível diferente de Normal) com a verdade (decisão do analista ou, sem ela,
o que o gerador do simulador marcou), nas últimas transações da API avaliadas por esta versão. Avisa quando o
F1 ou o recall caem claramente abaixo do que a versão teve no seu período de teste, ou quando os falsos alarmes
nas compras legítimas pelo menos duplicam (estes medem-se mesmo com poucas fraudes, por exemplo numa inflação de
valores, em que quase todas as compras parecem caras de mais).
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .banco import Alerta, Analise, TransacaoAvaliada
from .limiares import metricas_alerta

JANELA = 1000  # últimas transações com rótulo
MIN_FRAUDES = 10  # com menos, a medida é demasiado incerta para avisar
QUEDA_F1 = 0.10
QUEDA_RECALL = 0.15
MIN_LEGITIMAS = 300
FATOR_FALSOS = 2.0  # falsos alarmes por mil legítimas pelo menos 2x os do teste (e +10 por mil)
DIAS_TABELA = 14
CACHE_SEGUNDOS = 30


def medir(sessoes: sessionmaker, versao: int | None, base: dict | None) -> dict:
    if versao is None:
        return {"versao": None, "atual": None, "base": base, "aviso": False, "por_dia": [], "motivos": []}
    consulta = (select(TransacaoAvaliada.data_hora, TransacaoAvaliada.nivel, TransacaoAvaliada.fraude_simulada,
                       Alerta.estado)
                .join(Analise, Analise.id == TransacaoAvaliada.analise_id)
                .outerjoin(Alerta, Alerta.transacao_id == TransacaoAvaliada.id)
                .where(Analise.origem == "api", Analise.versao_modelo == versao,
                       (TransacaoAvaliada.fraude_simulada.is_not(None)) | Alerta.estado.in_(("fraude", "falso_alarme")))
                .order_by(TransacaoAvaliada.data_hora.desc(), TransacaoAvaliada.id.desc()).limit(JANELA))
    with sessoes() as sessao:
        linhas = sessao.execute(consulta).all()
    y = np.array([(e == "fraude") if e in ("fraude", "falso_alarme") else bool(s) for _, _, s, e in linhas], dtype=bool)
    nivel = np.array([n for _, n, _, _ in linhas])
    atual = metricas_alerta(y, nivel) if len(linhas) else None
    if atual is not None:
        atual["desde"], atual["ate"] = linhas[-1][0], linhas[0][0]

    dias = defaultdict(lambda: [0, 0, 0, 0])  # transações, fraudes, apanhadas, alertas
    for (quando, n, _, _), fraude in zip(linhas, y):
        d = dias[quando.date()]
        d[0] += 1
        d[1] += fraude
        d[2] += fraude and n != "Normal"
        d[3] += n != "Normal"
    por_dia = [{"dia": dia, "transacoes": t, "fraudes": f, "apanhadas": a, "alertas": al,
                "recall": a / f if f else None, "precisao": a / al if al else None}
               for dia, (t, f, a, al) in sorted(dias.items(), reverse=True)[:DIAS_TABELA]]

    motivos = []
    falsos_base = (base or {}).get("falsos_por_mil")
    if base and falsos_base is None and base.get("transacoes"):  # versões registadas antes desta medida
        legitimas = base["transacoes"] - base["fraudes"]
        falsos_base = 1000 * (base["alertas"] - base["apanhadas"]) / legitimas if legitimas else None
    if (atual and falsos_base is not None and atual["transacoes"] - atual["fraudes"] >= MIN_LEGITIMAS
            and atual["falsos_por_mil"] >= max(FATOR_FALSOS * falsos_base, falsos_base + 10)):
        motivos.append("falsos")
    if base is not None and falsos_base is not None:
        base = base | {"falsos_por_mil": falsos_base}
    if atual and base and atual["fraudes"] >= MIN_FRAUDES:
        if base.get("f1") is not None and atual["f1"] is not None and base["f1"] - atual["f1"] >= QUEDA_F1:
            motivos.append("f1")
        if base.get("recall") is not None and atual["recall"] is not None and base["recall"] - atual["recall"] >= QUEDA_RECALL:
            motivos.append("recall")
    return {"versao": versao, "atual": atual, "base": base, "aviso": bool(motivos), "motivos": motivos,
            "por_dia": por_dia, "suficiente": bool(atual and atual["fraudes"] >= MIN_FRAUDES)}


class Monitor:
    """Guarda a última medida durante uns segundos: o aviso aparece em várias páginas."""

    def __init__(self, sessoes: sessionmaker, relogio=time.monotonic):
        self.sessoes, self.relogio = sessoes, relogio
        self._cache: tuple | None = None
        self._trinco = threading.Lock()

    def estado(self, versao: int | None, base: dict | None) -> dict:
        with self._trinco:
            if self._cache and self._cache[0] == versao and self.relogio() - self._cache[1] < CACHE_SEGUNDOS:
                return self._cache[2]
        resultado = medir(self.sessoes, versao, base)
        with self._trinco:
            self._cache = (versao, self.relogio(), resultado)
        return resultado

    def esquecer(self) -> None:
        with self._trinco:
            self._cache = None
