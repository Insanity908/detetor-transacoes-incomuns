"""Registo das versões dos modelos: qual está publicada, as candidatas do re-treino e as anteriores.

A pasta PASTA_MODELOS, criada pelo treino inicial (treinar.py), é a versão 1. Cada re-treino grava a sua
em PASTA_MODELOS/versoes/vN, com os ficheiros assinados como no treino inicial. Publicar uma versão (nova
ou anterior) troca o modelo em uso sem reiniciar o servidor; nada é apagado, por isso é sempre possível voltar.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import sessionmaker

from .artefatos import Artefatos, carregar
from .banco import VersaoModelo
from .limiares import metricas_alerta

NOMES_ESTADO = {"a_treinar": "A treinar", "candidata": "Candidata", "publicada": "Publicada",
                "anterior": "Anterior", "falhou": "Falhou"}


def _agora() -> datetime:
    return datetime.now().replace(microsecond=0)


def metricas_do_teste(artefatos: Artefatos) -> dict:
    """O desempenho da versão no seu período de teste, medido como o analista o vê (nível diferente de Normal)."""
    teste = artefatos.teste_avaliado
    resultado = {"classificador": artefatos.detector.escolhido}
    if teste is not None and len(teste) and "fraude" in teste.columns:
        resultado["teste_alertas"] = metricas_alerta(teste["fraude"].to_numpy(), teste["nivel"].to_numpy())
    return resultado


def ler_metricas(versao: VersaoModelo | None) -> dict:
    if versao is None or not versao.metricas:
        return {}
    try:
        return json.loads(versao.metricas)
    except ValueError:
        return {}


def listar(sessoes: sessionmaker) -> list[VersaoModelo]:
    with sessoes() as sessao:
        return list(sessao.scalars(select(VersaoModelo).order_by(VersaoModelo.numero.desc())))


def obter(sessoes: sessionmaker, numero: int) -> VersaoModelo | None:
    with sessoes() as sessao:
        return sessao.scalar(select(VersaoModelo).where(VersaoModelo.numero == numero))


def publicada(sessoes: sessionmaker) -> VersaoModelo | None:
    with sessoes() as sessao:
        return sessao.scalar(select(VersaoModelo).where(VersaoModelo.estado == "publicada"))


def registar_inicial(sessoes: sessionmaker, artefatos: Artefatos | None) -> VersaoModelo | None:
    """Sem nenhuma versão registada, a pasta do treino inicial passa a ser a versão 1 (publicada)."""
    with sessoes.begin() as sessao:
        if artefatos is None or sessao.scalar(select(func.count(VersaoModelo.id))):
            return None
        versao = VersaoModelo(numero=1, estado="publicada", origem="inicial", pasta=".",
                              classificador=artefatos.detector.escolhido, criada_em=_agora(), criada_por="treinar.py",
                              publicada_em=_agora(), publicada_por="sistema",
                              metricas=json.dumps(metricas_do_teste(artefatos), ensure_ascii=False))
        sessao.add(versao)
    return versao


def interromper_pendentes(sessoes: sessionmaker) -> int:
    """No arranque: um re-treino que estava a correr quando o servidor parou não vai acabar."""
    with sessoes.begin() as sessao:
        return sessao.execute(update(VersaoModelo).where(VersaoModelo.estado == "a_treinar").values(
            estado="falhou", erro="O servidor parou durante o treino. Peça um novo re-treino.")).rowcount


def carregar_versao(pasta_modelos: Path, versao: VersaoModelo) -> Artefatos | None:
    artefatos = carregar(Path(pasta_modelos) / versao.pasta)
    if artefatos is not None:
        artefatos.versao = versao.numero
    return artefatos


def criar_em_treino(sessoes: sessionmaker, por: str) -> VersaoModelo:
    with sessoes.begin() as sessao:
        if sessao.scalar(select(VersaoModelo.id).where(VersaoModelo.estado == "a_treinar")):
            raise ValueError("Já há um re-treino a decorrer. Espere que termine.")
        numero = (sessao.scalar(select(func.max(VersaoModelo.numero))) or 0) + 1
        versao = VersaoModelo(numero=numero, estado="a_treinar", origem="retreino", pasta=f"versoes/v{numero}",
                              criada_em=_agora(), criada_por=por[:50])
        sessao.add(versao)
    return versao


def concluir(sessoes: sessionmaker, numero: int, classificador: str, metricas: dict) -> None:
    with sessoes.begin() as sessao:
        sessao.execute(update(VersaoModelo).where(VersaoModelo.numero == numero).values(
            estado="candidata", classificador=classificador, metricas=json.dumps(metricas, ensure_ascii=False)))


def falhar(sessoes: sessionmaker, numero: int, erro: str) -> None:
    with sessoes.begin() as sessao:
        sessao.execute(update(VersaoModelo).where(VersaoModelo.numero == numero).values(
            estado="falhou", erro=erro[:2000]))


def publicar(sessoes: sessionmaker, numero: int, por: str) -> VersaoModelo:
    """Marca a versão como publicada e a anterior como 'anterior'. Quem chama carrega os ficheiros primeiro."""
    with sessoes.begin() as sessao:
        alvo = sessao.scalar(select(VersaoModelo).where(VersaoModelo.numero == numero))
        if alvo is None or alvo.estado not in ("candidata", "anterior"):
            raise ValueError("Só se publica uma versão candidata ou anterior.")
        sessao.execute(update(VersaoModelo).where(VersaoModelo.estado == "publicada").values(estado="anterior"))
        alvo.estado, alvo.publicada_em, alvo.publicada_por = "publicada", _agora(), por[:50]
        sessao.flush()
        sessao.expunge(alvo)
    return alvo
