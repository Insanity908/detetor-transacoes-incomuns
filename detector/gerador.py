"""Gerador de transações sintéticas de clientes em Moçambique, com fraudes rotuladas.

Cada cliente tem hábitos próprios (cidade, valor típico, horário, categorias, frequência). As fraudes
fogem desses hábitos. As categorias das fraudes seguem as proporções das compras legítimas, e os valores
dependem do valor típico de cada cliente. A exceção é o tipo "país estranho", que por definição acontece num
país fora de MZ e ZA: a coluna bruta do país denuncia-o, mas o modelo não a usa, só a feature `pais_novo`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

INICIO = "2026-01-01"
DIAS = 120
PAIS_HABITUAL = "MZ"
CIDADES = ("MAPUTO", "MATOLA", "BEIRA", "NAMPULA", "QUELIMANE", "TETE")
PESO_CIDADES = (0.35, 0.2, 0.15, 0.12, 0.1, 0.08)
CATEGORIAS = ("MERCEARIA", "COMBUSTIVEL", "RESTAURANTE", "FARMACIA", "RECARGA_TELEMOVEL", "TRANSPORTE",
              "VESTUARIO", "ELETRONICA", "VIAGEM")
PESO_CATEGORIAS = (0.2, 0.12, 0.12, 0.1, 0.16, 0.12, 0.08, 0.05, 0.05)
TIPOS_FRAUDE = ("valor_alto", "pais_estranho", "horario_estranho", "rajada", "conta_tomada")
NOMES_TIPO_FRAUDE = {"valor_alto": "Valor alto", "pais_estranho": "País estranho", "horario_estranho": "Horário estranho",
                     "rajada": "Rajada", "conta_tomada": "Conta tomada"}
PAISES_ESTRANHOS = ("NG", "AE", "CN", "BR", "IN", "ZW", "MW", "TZ")
FRACAO_CLIENTES_NOVOS = 0.25  # aderem depois do aquecimento, para o modelo ver clientes sem histórico
DIA_ADESAO_MIN = 30  # índice 0: dia 31
EVENTOS_POR_TRANSACAO = 0.0053  # com a rajada valendo várias compras, dá cerca de 1% de fraudes


def _hora(rng: np.random.Generator, pico: float, n: int) -> np.ndarray:
    return np.clip(rng.normal(pico, 2.5, n), 6, 23.9)


def _clientes(rng: np.random.Generator, n_clientes: int, dias: int) -> list[dict]:
    clientes = []
    for i in range(n_clientes):
        k = int(rng.integers(3, 5))
        categorias = rng.choice(CATEGORIAS, size=k, replace=False, p=PESO_CATEGORIAS)
        cidade = str(rng.choice(CIDADES, p=PESO_CIDADES))
        tardio = rng.random() < FRACAO_CLIENTES_NOVOS and dias - 10 > DIA_ADESAO_MIN
        adesao = int(rng.integers(DIA_ADESAO_MIN, dias - 10)) if tardio else 0
        muda = rng.random() < 0.05
        clientes.append({
            "cliente": f"C{i + 1:04d}",
            "adesao": adesao,
            "cidade": cidade,
            "nova_cidade": str(rng.choice([c for c in CIDADES if c != cidade])) if muda else None,
            "dia_mudanca": int(rng.integers(max(adesao, dias // 3), dias)) if muda else None,
            "tipico": float(np.clip(rng.lognormal(np.log(1200), 0.8), 300, 5000)),
            "pico": float(rng.uniform(8, 20)),
            "categorias": list(categorias),
            "peso_categorias": rng.dirichlet(np.ones(k) * 2),
            "taxa": float(rng.uniform(0.5, 4)),
        })
    return clientes


def _cidade_no_dia(c: dict, dia: np.ndarray) -> np.ndarray:
    cidade = np.full(len(dia), c["cidade"], dtype=object)
    if c["nova_cidade"] is not None:
        cidade[dia >= c["dia_mudanca"]] = c["nova_cidade"]
    return cidade


def _legitimas(rng: np.random.Generator, c: dict, dias: int, t0: pd.Timestamp) -> pd.DataFrame:
    n = max(int(rng.poisson(c["taxa"] * (dias - c["adesao"]))), 5)
    dia = rng.integers(c["adesao"], dias, n)
    hora = _hora(rng, c["pico"], n)
    noturna = rng.random(n) < 0.02
    hora[noturna] = rng.uniform(0, 5, noturna.sum())
    valor = c["tipico"] * rng.lognormal(0, 0.5, n)
    grande = rng.random(n) < 0.01
    valor[grande] = c["tipico"] * rng.uniform(3, 8, grande.sum())
    pais = np.where(rng.random(n) < 0.01, "ZA", PAIS_HABITUAL)
    return pd.DataFrame({
        "cliente": c["cliente"],
        "data_hora": t0 + pd.to_timedelta(dia, "D") + pd.to_timedelta(hora * 60, "min"),
        "valor": valor,
        "cidade": _cidade_no_dia(c, dia),
        "pais": pais,
        "categoria": rng.choice(c["categorias"], size=n, p=c["peso_categorias"]),
        "fraude": 0,
        "tipo_fraude": "",
    })


def _fraude(rng: np.random.Generator, c: dict, tipo: str, dias: int, t0: pd.Timestamp,
            categorias: np.ndarray, pesos: np.ndarray) -> pd.DataFrame:
    dia = int(rng.integers(c["adesao"], dias))
    inicio = t0 + pd.Timedelta(days=dia) + pd.Timedelta(minutes=float(_hora(rng, c["pico"], 1)[0]) * 60)
    cidade = _cidade_no_dia(c, np.array([dia]))[0]
    categoria = lambda n: rng.choice(categorias, size=n, p=pesos)  # mesmas proporções das legítimas
    linhas = {"cliente": c["cliente"], "fraude": 1, "tipo_fraude": tipo, "pais": PAIS_HABITUAL, "cidade": cidade}
    if tipo == "rajada":
        n = int(rng.integers(4, 8))
        quando = inicio + pd.to_timedelta(np.cumsum(rng.uniform(0.5, 3, n)), "min")
        valor = np.maximum(c["tipico"] * rng.uniform(0.05, 0.3, n), 20)
        return pd.DataFrame(linhas | {"data_hora": quando, "valor": valor, "categoria": categoria(n)})
    if tipo == "valor_alto":
        valor, cat = c["tipico"] * rng.uniform(3, 15), categoria(1)[0]
    elif tipo == "pais_estranho":
        valor, cat = c["tipico"] * rng.lognormal(0, 0.6), categoria(1)[0]
        linhas["pais"] = str(rng.choice(PAISES_ESTRANHOS))
    elif tipo == "horario_estranho":
        valor, cat = c["tipico"] * rng.lognormal(0.3, 0.6), categoria(1)[0]
        inicio = t0 + pd.Timedelta(days=dia) + pd.Timedelta(minutes=float(rng.uniform(1, 5)) * 60)
    else:  # conta_tomada: cidade nova, categoria nova e valor médio-alto
        valor = c["tipico"] * rng.uniform(2, 5)
        linhas["cidade"] = str(rng.choice([x for x in CIDADES if x != cidade]))
        novas = [x for x in CATEGORIAS if x not in c["categorias"]]
        p = np.array([PESO_CATEGORIAS[CATEGORIAS.index(x)] for x in novas])
        cat = str(rng.choice(novas, p=p / p.sum()))
    return pd.DataFrame([linhas | {"data_hora": inicio, "valor": valor, "categoria": cat}])


def gerar_transacoes(n_clientes: int = 300, dias: int = DIAS, semente: int = 42, inicio: str = INICIO) -> pd.DataFrame:
    rng = np.random.default_rng(semente)
    t0 = pd.Timestamp(inicio)
    clientes = _clientes(rng, n_clientes, dias)
    legitimas = pd.concat([_legitimas(rng, c, dias, t0) for c in clientes], ignore_index=True)
    proporcoes = legitimas["categoria"].value_counts(normalize=True)
    eventos = max(int(round(EVENTOS_POR_TRANSACAO * len(legitimas))), len(TIPOS_FRAUDE))
    tipos = [TIPOS_FRAUDE[i % len(TIPOS_FRAUDE)] for i in range(eventos)]  # todos os tipos aparecem
    escolhidos = rng.integers(0, n_clientes, eventos)
    fraudes = [_fraude(rng, clientes[i], t, dias, t0, proporcoes.index.to_numpy(), proporcoes.to_numpy())
               for i, t in zip(escolhidos, tipos, strict=True)]
    df = pd.concat([legitimas, *fraudes], ignore_index=True)
    df["data_hora"] = df["data_hora"].dt.floor("s")
    df = df[df["data_hora"] < t0 + pd.Timedelta(days=dias)]
    df["valor"] = df["valor"].round(2)
    df = df.sort_values(["data_hora", "cliente"], kind="stable").reset_index(drop=True)
    df.insert(0, "id_transacao", [f"T{i + 1:06d}" for i in range(len(df))])
    df["fraude"] = df["fraude"].astype(int)
    return df[["id_transacao", "cliente", "data_hora", "valor", "cidade", "pais", "categoria", "fraude",
               "tipo_fraude"]]
