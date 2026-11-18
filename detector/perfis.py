"""Comportamento habitual de cada cliente e features que medem o quanto uma transação foge dele.

Cada feature usa só as transações anteriores do mesmo cliente, por isso nada do futuro entra no cálculo.
"""
from __future__ import annotations

from bisect import insort

import numpy as np
import pandas as pd

FEATURES = ["valor_vs_habitual", "hora_incomum", "pais_novo", "cidade_nova", "categoria_nova",
            "compras_ultima_hora", "compras_24h", "minutos_desde_anterior", "historico_curto", "valor", "hora"]
MIN_HISTORICO = 5
MINUTOS_MAX = 10_080  # uma semana
DISTANCIA_HORA = 3
_HORA_NS = 3_600 * 10**9


def normalizar(df: pd.DataFrame) -> pd.DataFrame:
    """Compara sem depender de espaços ou maiúsculas: ` mz ` e `MZ` são o mesmo país."""
    out = df.copy()
    out["cliente"] = out["cliente"].astype(str).str.strip().str.upper()  # c0001 e C0001 são o mesmo cliente
    for coluna in ("cidade", "pais", "categoria"):
        out[coluna] = out[coluna].astype(str).str.strip().str.upper()
    out["data_hora"] = pd.to_datetime(out["data_hora"])
    return out


def _features_do_cliente(grupo: pd.DataFrame, calcular: np.ndarray | None = None) -> np.ndarray:
    """Features de cada linha, por ordem de tempo. Com `calcular`, só as linhas marcadas são calculadas:
    as outras (o histórico) apenas atualizam o estado do cliente, o que dá exatamente os mesmos valores."""
    tempos = grupo["data_hora"].to_numpy("datetime64[ns]").astype(np.int64)
    valores = grupo["valor"].to_numpy(float)
    horas = (grupo["data_hora"].dt.hour + grupo["data_hora"].dt.minute / 60).to_numpy()
    cidades, paises, categorias = (grupo[c].to_numpy() for c in ("cidade", "pais", "categoria"))
    vistos = {"cidade": set(), "pais": set(), "categoria": set()}
    ordenados: list[float] = []  # valores anteriores em ordem, para a mediana sem reordenar a cada passo
    saida = np.zeros((len(grupo), len(FEATURES)))
    for j in range(len(grupo)):
        if calcular is not None and not calcular[j]:
            insort(ordenados, valores[j])
            vistos["pais"].add(paises[j])
            vistos["cidade"].add(cidades[j])
            vistos["categoria"].add(categorias[j])
            continue
        t = tempos[j]
        curto = j < MIN_HISTORICO
        if curto:
            vs_habitual, hora_incomum, novos = 1.0, 0.0, (0, 0, 0)
        else:
            meio = j // 2
            mediana = ordenados[meio] if j % 2 else (ordenados[meio - 1] + ordenados[meio]) / 2
            vs_habitual = valores[j] / mediana
            distancia = np.abs(horas[:j] - horas[j])
            distancia = np.minimum(distancia, 24 - distancia)
            hora_incomum = float(np.mean(distancia > DISTANCIA_HORA))
            novos = (int(paises[j] not in vistos["pais"]), int(cidades[j] not in vistos["cidade"]),
                     int(categorias[j] not in vistos["categoria"]))
        anteriores = tempos[:j]
        ultima_hora = j - np.searchsorted(anteriores, t - _HORA_NS, "left")
        dia = j - np.searchsorted(anteriores, t - 24 * _HORA_NS, "left")
        minutos = min((t - tempos[j - 1]) / 60e9, MINUTOS_MAX) if j else MINUTOS_MAX
        saida[j] = (vs_habitual, hora_incomum, *novos, ultima_hora, dia, minutos, int(curto),
                    valores[j], int(horas[j]))
        insort(ordenados, valores[j])
        vistos["pais"].add(paises[j])
        vistos["cidade"].add(cidades[j])
        vistos["categoria"].add(categorias[j])
    return saida


def calcular_features(df: pd.DataFrame, historico: pd.DataFrame | None = None,
                      historico_normalizado: bool = False) -> pd.DataFrame:
    """Uma linha de features por linha de `df`, com o mesmo índice.

    Cada linha usa as transações anteriores do mesmo cliente, vindas de `historico` e das linhas
    anteriores de `df`. Em caso de empate de horário, o histórico vem primeiro e depois a ordem de `df`.
    """
    novas = normalizar(df).assign(_origem=1, _ordem=np.arange(len(df)), _indice=df.index)
    partes = [novas]
    if historico is not None and len(historico):
        base = historico if historico_normalizado else normalizar(historico)
        partes.insert(0, base.assign(_origem=0, _ordem=np.arange(len(historico)), _indice=-1))
    todas = pd.concat(partes, ignore_index=True)
    todas = todas.sort_values(["cliente", "data_hora", "_origem", "_ordem"], kind="stable")
    blocos = []
    for _, grupo in todas.groupby("cliente", sort=False):
        manter = grupo["_origem"].to_numpy() == 1
        valores = _features_do_cliente(grupo, manter)
        blocos.append(pd.DataFrame(valores[manter], columns=FEATURES, index=grupo["_indice"].to_numpy()[manter]))
    resultado = pd.concat(blocos) if blocos else pd.DataFrame(columns=FEATURES)
    resultado = resultado.loc[df.index]
    inteiras = ["pais_novo", "cidade_nova", "categoria_nova", "compras_ultima_hora", "compras_24h",
                "historico_curto", "hora"]
    return resultado.astype({c: int for c in inteiras})


def perfil_cliente(historico: pd.DataFrame, cliente: str, normalizado: bool = False) -> dict | None:
    """O comportamento normal do cliente, só com as compras legítimas."""
    dados = historico if normalizado else normalizar(historico)
    dados = dados[dados["cliente"] == str(cliente).strip().upper()]
    if "fraude" in dados.columns:
        dados = dados[dados["fraude"] == 0]
    if dados.empty:
        return None
    dias = (dados["data_hora"].max().normalize() - dados["data_hora"].min().normalize()).days + 1
    horas = dados["data_hora"].dt.hour.value_counts().reindex(range(24), fill_value=0)
    contagem = lambda coluna: {str(k): int(v) for k, v in dados[coluna].value_counts().items()}
    return {
        "n_compras": len(dados),
        "valor_mediana": float(dados["valor"].median()),
        "valor_p10": float(dados["valor"].quantile(0.1)),
        "valor_p90": float(dados["valor"].quantile(0.9)),
        "compras_por_dia": len(dados) / dias,
        "horas": [int(h) for h in horas],
        "cidades": contagem("cidade"),
        "paises": contagem("pais"),
        "categorias": contagem("categoria"),
    }
