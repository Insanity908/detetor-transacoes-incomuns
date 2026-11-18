"""Gravar e ler análises na base de dados, convertendo de e para os DataFrames do serviço."""
from __future__ import annotations

import json
import secrets
from datetime import datetime

import pandas as pd
from sqlalchemy import func, insert, select
from sqlalchemy.orm import sessionmaker

from .banco import Analise, TransacaoAvaliada

COLUNAS = ("id_transacao", "cliente", "data_hora", "valor", "cidade", "pais", "categoria",
           "risco", "anomalia", "nivel", "motivos", "historico_curto")
ORIGENS = ("ficheiro", "formulario", "exemplo")


def guardar_analise(sessoes: sessionmaker, resultado: pd.DataFrame, origem: str, modelo: str,
                    nome_ficheiro: str | None = None, utilizador_id: int | None = None) -> str:
    """Grava o resultado pela ordem em que está e devolve a chave usada nos endereços da interface."""
    if origem not in ORIGENS:
        raise ValueError(f"Origem desconhecida: {origem}")
    chave = secrets.token_urlsafe(16)
    linhas = [
        {
            "ordem": i,
            "id_transacao": str(r["id_transacao"]),
            "cliente": str(r["cliente"]),
            "data_hora": pd.Timestamp(r["data_hora"]).to_pydatetime(),
            "valor": float(r["valor"]),
            "cidade": str(r["cidade"]),
            "pais": str(r["pais"]),
            "categoria": str(r["categoria"]),
            "risco": float(r["risco"]),
            "anomalia": float(r["anomalia"]),
            "nivel": str(r["nivel"]),
            "motivos": json.dumps(list(r["motivos"]), ensure_ascii=False),
            "historico_curto": bool(r.get("historico_curto", False)),
        }
        for i, r in enumerate(resultado.to_dict("records"))
    ]
    with sessoes.begin() as sessao:
        analise = Analise(chave=chave, origem=origem, nome_ficheiro=(nome_ficheiro or None) and nome_ficheiro[:255],
                          criada_em=datetime.now().replace(microsecond=0), total=len(linhas),
                          alertas=sum(l["nivel"] != "Normal" for l in linhas), modelo=modelo,
                          utilizador_id=utilizador_id)
        sessao.add(analise)
        sessao.flush()
        if linhas:
            sessao.execute(insert(TransacaoAvaliada), [l | {"analise_id": analise.id} for l in linhas])
    return chave


def obter_analise(sessoes: sessionmaker, chave: str) -> Analise | None:
    with sessoes() as sessao:
        return sessao.scalar(select(Analise).where(Analise.chave == chave))


def obter_transacoes(sessoes: sessionmaker, analise: Analise, so_alertas: bool = False,
                     limite: int | None = None) -> pd.DataFrame:
    """As transações da análise, pela ordem gravada, no mesmo formato que o serviço devolve."""
    consulta = (select(*(getattr(TransacaoAvaliada, c) for c in COLUNAS))
                .where(TransacaoAvaliada.analise_id == analise.id).order_by(TransacaoAvaliada.ordem))
    if so_alertas:
        consulta = consulta.where(TransacaoAvaliada.nivel != "Normal")
    if limite is not None:
        consulta = consulta.limit(limite)
    with sessoes() as sessao:
        linhas = [dict(zip(COLUNAS, r)) for r in sessao.execute(consulta)]
    for linha in linhas:
        linha["motivos"] = json.loads(linha["motivos"])
    df = pd.DataFrame(linhas, columns=list(COLUNAS))
    df["data_hora"] = pd.to_datetime(df["data_hora"])
    return df


def listar_analises(sessoes: sessionmaker, origem: str | None = None, limite: int = 10,
                    utilizador_id: int | None = None) -> list[Analise]:
    """As análises mais recentes; com utilizador_id, só as feitas por esse utilizador."""
    consulta = select(Analise).order_by(Analise.criada_em.desc(), Analise.id.desc()).limit(limite)
    if origem is not None:
        consulta = consulta.where(Analise.origem == origem)
    if utilizador_id is not None:
        consulta = consulta.where(Analise.utilizador_id == utilizador_id)
    with sessoes() as sessao:
        return list(sessao.scalars(consulta))


def contar_analises(sessoes: sessionmaker) -> int:
    with sessoes() as sessao:
        return sessao.scalar(select(func.count(Analise.id))) or 0
