"""Histórico anterior de clientes novos (por exemplo, o extrato de outro banco).

Só serve ao detetor: dá-lhe os hábitos do cliente desde o primeiro dia. Não mexe no saldo, não aparece no extrato do
cliente, não é avaliado nem cria alertas, e não entra na monitorização nem no re-treino.
"""
from __future__ import annotations

import zlib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker

from . import entrada, gerador
from .banco import Cliente, HistoricoImportado

MAX_LINHAS = 2000
DIAS_MAXIMOS = 90  # o detetor só olha para os últimos 90 dias do cliente (autorizacao.DIAS_HISTORICO)
DIAS_EXEMPLO = 60
CATEGORIAS = (*gerador.CATEGORIAS, gerador.CATEGORIA_TRANSFERENCIA, gerador.CATEGORIA_LEVANTAMENTO)
COLUNAS_DETETOR = ["id_transacao", "cliente", "data_hora", "valor", "cidade", "pais", "categoria", "fraude"]


@dataclass(frozen=True)
class ResultadoImportacao:
    inseridas: int
    repetidas: int


@dataclass(frozen=True)
class ResumoHistorico:
    total: int
    primeira: datetime
    ultima: datetime
    categorias: list[str]


def _agora() -> datetime:
    return datetime.now().replace(microsecond=0)


def _cliente(sessoes: sessionmaker, cliente_id: int) -> Cliente:
    with sessoes() as s:
        c = s.get(Cliente, cliente_id)
        if c is None:
            raise ValueError("Cliente não encontrado.")
        s.expunge(c)
        return c


def _gravar(sessoes: sessionmaker, cliente_id: int, linhas: list[dict], origem: str, por: str) -> ResultadoImportacao:
    """Grava as linhas novas; as que já existem (no lote ou na tabela) contam como repetidas."""
    chave = lambda d: (d["data_hora"], d["valor"], d["cidade"], d["pais"], d["categoria"])
    agora = _agora()
    with sessoes.begin() as s:
        existentes = {tuple(r) for r in s.execute(
            select(HistoricoImportado.data_hora, HistoricoImportado.valor, HistoricoImportado.cidade,
                   HistoricoImportado.pais, HistoricoImportado.categoria)
            .where(HistoricoImportado.cliente_id == cliente_id))}
        novas = 0
        for d in linhas:
            k = chave(d)
            if k in existentes:
                continue
            existentes.add(k)
            s.add(HistoricoImportado(cliente_id=cliente_id, origem=origem, importado_em=agora,
                                     importado_por=por[:50], **d))
            novas += 1
    return ResultadoImportacao(inseridas=novas, repetidas=len(linhas) - novas)


def importar(sessoes: sessionmaker, cliente_id: int, conteudo: bytes, por: str,
             agora: datetime | None = None) -> ResultadoImportacao:
    """Lê um CSV (data_hora, valor, cidade, pais, categoria; cliente opcional) e grava-o. Erros recusam tudo."""
    c = _cliente(sessoes, cliente_id)
    agora = agora or _agora()
    df = entrada.ler_csv(conteudo, cliente=c.codigo)
    if len(df) > MAX_LINHAS:
        raise ValueError(f"O ficheiro tem {len(df)} linhas; o máximo é {MAX_LINHAS}.")
    validado = entrada.validar(df)
    inicio = agora - timedelta(days=DIAS_MAXIMOS)
    linhas = []
    for i, r in enumerate(validado.to_dict("records")):
        n = i + 2
        if str(r["cliente"]).strip().upper() != c.codigo.upper():
            raise ValueError(f"Linha {n}: o cliente '{r['cliente']}' não é o desta ficha ({c.codigo}).")
        quando = pd.Timestamp(r["data_hora"]).to_pydatetime().replace(microsecond=0)
        if not inicio <= quando < agora:
            raise ValueError(f"Linha {n}: a data tem de estar nos últimos {DIAS_MAXIMOS} dias e não pode ser futura.")
        categoria = str(r["categoria"]).strip().upper()
        if categoria not in CATEGORIAS:
            raise ValueError(f"Linha {n}: categoria desconhecida ('{r['categoria']}').")
        linhas.append({"data_hora": quando, "valor": int(round(float(r["valor"]) * 100)),
                       "cidade": str(r["cidade"]).strip().upper(), "pais": str(r["pais"]).strip().upper(),
                       "categoria": categoria})
    return _gravar(sessoes, cliente_id, linhas, "ficheiro", por)


def gerar_exemplo(sessoes: sessionmaker, cliente_id: int, por: str, agora: datetime | None = None) -> int:
    """Gera ~60 dias de compras legítimas coerentes com a ficha (cidade, ~1 200 MT, 3–4 categorias, horas diurnas)."""
    c = _cliente(sessoes, cliente_id)
    agora = agora or _agora()
    rng = np.random.default_rng(zlib.crc32(c.codigo.encode("utf-8")))
    k = int(rng.integers(3, 5))
    perfil = {
        "cliente": c.codigo, "adesao": 0, "cidade": c.cidade, "nova_cidade": None, "dia_mudanca": None,
        "tipico": float(np.clip(rng.lognormal(np.log(1200), 0.4), 400, 3000)), "pico": float(rng.uniform(9, 19)),
        "categorias": list(rng.choice(gerador.CATEGORIAS, size=k, replace=False, p=gerador.PESO_CATEGORIAS)),
        "peso_categorias": rng.dirichlet(np.ones(k) * 2), "taxa": float(rng.uniform(0.8, 2.5)),
    }
    t0 = pd.Timestamp(agora.date()) - pd.Timedelta(days=DIAS_EXEMPLO)
    df = gerador._legitimas(rng, perfil, DIAS_EXEMPLO, t0)
    df = df[(df["data_hora"] < pd.Timestamp(agora)) & (df["pais"] == gerador.PAIS_HABITUAL)]
    linhas = [{"data_hora": pd.Timestamp(r["data_hora"]).floor("s").to_pydatetime(),
               "valor": max(int(round(float(r["valor"]) * 100)), 100), "cidade": str(r["cidade"]),
               "pais": str(r["pais"]), "categoria": str(r["categoria"])} for r in df.to_dict("records")]
    return _gravar(sessoes, cliente_id, linhas, "exemplo", por).inseridas


def resumo(sessoes: sessionmaker, cliente_id: int) -> ResumoHistorico | None:
    with sessoes() as s:
        total, primeira, ultima = s.execute(
            select(func.count(), func.min(HistoricoImportado.data_hora), func.max(HistoricoImportado.data_hora))
            .where(HistoricoImportado.cliente_id == cliente_id)).one()
        if not total:
            return None
        cats = Counter(s.scalars(select(HistoricoImportado.categoria)
                                 .where(HistoricoImportado.cliente_id == cliente_id)))
    return ResumoHistorico(total=total, primeira=primeira, ultima=ultima,
                           categorias=[c for c, _ in cats.most_common(3)])


def apagar(sessoes: sessionmaker, cliente_id: int) -> int:
    with sessoes.begin() as s:
        return s.execute(delete(HistoricoImportado).where(HistoricoImportado.cliente_id == cliente_id)).rowcount


def para_detetor(sessoes: sessionmaker, codigos: set[str], desde: datetime) -> pd.DataFrame:
    """As linhas importadas destes clientes desde `desde`, no formato do histórico do treino."""
    if not codigos:
        return pd.DataFrame(columns=COLUNAS_DETETOR)
    with sessoes() as s:
        linhas = s.execute(
            select(HistoricoImportado, Cliente.codigo).join(Cliente, Cliente.id == HistoricoImportado.cliente_id)
            .where(Cliente.codigo.in_(sorted(codigos)), HistoricoImportado.data_hora >= desde)
            .order_by(HistoricoImportado.data_hora)).all()
    df = pd.DataFrame([{"id_transacao": f"HIST-{h.id}", "cliente": codigo, "data_hora": h.data_hora,
                        "valor": h.valor / 100, "cidade": h.cidade, "pais": h.pais, "categoria": h.categoria,
                        "fraude": 0} for h, codigo in linhas], columns=COLUNAS_DETETOR)
    df["data_hora"] = pd.to_datetime(df["data_hora"])
    return df
