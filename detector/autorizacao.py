"""A única porta entre o banco e o detetor: cada saída de dinheiro é avaliada antes de mexer no saldo.

Quem chama (balcão, área do cliente, API, simulador) entrega pedidos de débito; aqui decide-se, por ordem de
momento: validar sem modelo, avaliar com o modelo, verificar o saldo, e então autorizar, reter ou recusar.
A auditoria fica para quem chama (rota), porque aqui não há pedido HTTP.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from . import repositorio
from .alertas import criar_para_analise
from .banco import CANAIS_MOVIMENTO, Alerta, ContaBancaria, Movimento, TransacaoAvaliada
from .contas_bancarias import debitar
from .servico import avaliar_transacoes

registo = logging.getLogger("detetor")

DIAS_HISTORICO = 90
TIPOS_SAIDA = ("compra", "transferencia_enviada", "levantamento")
MOTIVO_SALDO = "Saldo insuficiente."
MOTIVO_INVALIDO = "Pedido inválido."
MOTIVO_RISCO = "Risco muito alto: recusado automaticamente."


@dataclass(frozen=True)
class PedidoDebito:
    conta_id: int
    tipo: str  # "compra" | "transferencia_enviada" | "levantamento"
    valor: int  # cêntimos
    momento: datetime
    cidade: str
    pais: str
    categoria: str
    canal: str
    por: str
    id_transacao: str | None = None  # gerado ("BNC-" + token) se None
    contraparte_id: int | None = None
    descricao: str | None = None
    fraude_simulada: bool | None = None
    tipo_fraude_simulada: str | None = None


@dataclass(frozen=True)
class Decisao:
    movimento_id: int  # 0 quando não há conta onde registar o pedido
    estado: str  # "autorizado" | "recusado" | "retido"
    motivo: str | None
    nivel: str | None  # None quando recusado antes do modelo
    risco: float | None
    alerta_id: int | None


def _invalido(s: Session, p: PedidoDebito) -> str | None:
    """O motivo da recusa antes do modelo, ou None se o pedido pode ser avaliado."""
    if p.tipo not in TIPOS_SAIDA or p.canal not in CANAIS_MOVIMENTO or p.valor <= 0:
        return MOTIVO_INVALIDO
    conta = s.get(ContaBancaria, p.conta_id)
    if conta is None:
        return "Conta não encontrada."
    if conta.estado == "bloqueada":
        return "A conta está bloqueada."
    if p.tipo == "transferencia_enviada" and p.contraparte_id is not None:  # sem contraparte: saída para fora
        if p.contraparte_id == p.conta_id:
            return "A conta de destino não pode ser a própria conta."
        destino = s.get(ContaBancaria, p.contraparte_id)
        if destino is None:
            return "Conta de destino não encontrada."
        if destino.estado == "bloqueada":
            return "A conta de destino está bloqueada."
    return None


def _recusar(s: Session, p: PedidoDebito, id_transacao: str, motivo: str, agora: datetime) -> Movimento | None:
    """Regista o pedido como recusado, sem mexer em saldos; None se a conta nem existe."""
    if s.get(ContaBancaria, p.conta_id) is None:
        return None
    destino = s.get(ContaBancaria, p.contraparte_id) if p.contraparte_id is not None else None
    movimento = Movimento(conta_id=p.conta_id, tipo=p.tipo, valor=p.valor, estado="recusado", motivo=motivo,
                          contraparte_id=destino.id if destino else None, canal=p.canal, momento=p.momento,
                          registado_em=agora, id_transacao=id_transacao, descricao=p.descricao, criado_por=p.por)
    s.add(movimento)
    s.flush()
    return movimento


def _disponivel(s: Session, conta_id: int) -> int:
    """O saldo disponível lido da base de dados neste momento (não da cache da sessão)."""
    conta = s.execute(select(ContaBancaria).where(ContaBancaria.id == conta_id)
                      .execution_options(populate_existing=True)).scalar_one()
    return conta.saldo - conta.saldo_retido


def autorizar(sessoes: sessionmaker, artefatos, pedidos: list[PedidoDebito], cortes, origem: str,
              chave_api_id: int | None = None) -> list[Decisao]:
    """Decide cada pedido (autoriza, retém ou recusa), grava tudo numa só transação e devolve as decisões
    pela mesma ordem dos pedidos."""
    ids = [p.id_transacao or "BNC-" + secrets.token_hex(8).upper() for p in pedidos]
    decisoes: list[Decisao | None] = [None] * len(pedidos)

    # 1. Validação sem modelo.
    avaliar: list[int] = []
    clientes: dict[int, str] = {}
    invalidos: dict[int, str] = {}
    with sessoes() as s:
        for i, p in enumerate(pedidos):
            motivo = _invalido(s, p)
            if motivo is not None:
                invalidos[i] = motivo
            else:
                avaliar.append(i)
                clientes[i] = s.get(ContaBancaria, p.conta_id).cliente.codigo

    # 2. Avaliação de todos os restantes de uma vez, pelo mesmo caminho da API.
    resultado = None
    if avaliar:
        df = pd.DataFrame([{"id_transacao": ids[i], "cliente": clientes[i],
                            "data_hora": pd.Timestamp(pedidos[i].momento), "valor": pedidos[i].valor / 100,
                            "cidade": pedidos[i].cidade, "pais": pedidos[i].pais,
                            "categoria": pedidos[i].categoria, "fraude_simulada": pedidos[i].fraude_simulada,
                            "tipo_fraude_simulada": pedidos[i].tipo_fraude_simulada} for i in avaliar])
        desde = df["data_hora"].min().to_pydatetime() - timedelta(days=DIAS_HISTORICO)
        historico = repositorio.historico_api(sessoes, set(df["cliente"]), desde)
        resultado = avaliar_transacoes(artefatos, df, historico_extra=historico, cortes=cortes).reset_index(drop=True)

    # 3. Saldo, gravação e débitos numa só transação, por ordem de (momento, índice).
    agora = datetime.now().replace(microsecond=0)
    with sessoes.begin() as s:
        for i, motivo in invalidos.items():
            # Um pedido malformado (valor, tipo ou canal) não se regista: violaria as restrições da tabela.
            m = None if motivo == MOTIVO_INVALIDO else _recusar(s, pedidos[i], ids[i], motivo, agora)
            decisoes[i] = Decisao(m.id if m else 0, "recusado", motivo, None, None, None)

        # Saldo disponível corrente (lido na transação) para decidir quem passa; o débito atómico é a salvaguarda.
        disponivel: dict[int, int] = {}

        def livre(conta_id: int) -> int:
            if conta_id not in disponivel:
                disponivel[conta_id] = _disponivel(s, conta_id)
            return disponivel[conta_id]

        passam: list[int] = []  # posições em `avaliar` que passaram o saldo, pela ordem de processamento
        for k in sorted(range(len(avaliar)), key=lambda k: (pedidos[avaliar[k]].momento, avaliar[k])):
            i, p, linha = avaliar[k], pedidos[avaliar[k]], resultado.iloc[k]
            if livre(p.conta_id) < p.valor:
                m = _recusar(s, p, ids[i], MOTIVO_SALDO, agora)  # a avaliação não é gravada
                decisoes[i] = Decisao(m.id, "recusado", MOTIVO_SALDO, None, None, None)
                continue
            passam.append(k)
            if float(linha["risco"]) >= cortes.recusa:
                continue  # recusado pelo risco: não gasta saldo
            disponivel[p.conta_id] -= p.valor
            if p.tipo == "transferencia_enviada" and p.contraparte_id is not None and linha["nivel"] != "Alto risco":
                disponivel[p.contraparte_id] = livre(p.contraparte_id) + p.valor

        if passam:
            analise = repositorio.gravar_analise(s, resultado.iloc[passam].reset_index(drop=True), origem,
                                                 artefatos.detector.escolhido, chave_api_id=chave_api_id,
                                                 versao_modelo=getattr(artefatos, "versao", None))
            transacoes = dict(s.execute(select(TransacaoAvaliada.ordem, TransacaoAvaliada.id)
                                        .where(TransacaoAvaliada.analise_id == analise.id)).all())
            estados: dict[int, str] = {}
            for ordem, k in enumerate(passam):
                i, p, linha = avaliar[k], pedidos[avaliar[k]], resultado.iloc[k]
                nivel, risco = str(linha["nivel"]), float(linha["risco"])
                comum = dict(tipo=p.tipo, valor=p.valor, canal=p.canal, por=p.por, momento=p.momento,
                             contraparte_id=p.contraparte_id, transacao_id=transacoes[ordem],
                             id_transacao=ids[i], descricao=p.descricao)
                conta = s.get(ContaBancaria, p.conta_id)
                if risco >= cortes.recusa:
                    m = debitar(s, conta, estado="recusado", motivo=MOTIVO_RISCO, **comum)
                else:
                    m = debitar(s, conta, estado="retido" if nivel == "Alto risco" else "autorizado", **comum)
                if m.estado == "recusado":
                    # Recusa pelo risco, ou corrida perdida no saldo (o débito é atómico): o alerta nasce fechado.
                    estados[ordem] = "recusado_auto"
                decisoes[i] = Decisao(m.id, m.estado, m.motivo, nivel, risco, None)
            criar_para_analise(s, analise.id, analise.criada_em, estados)
            alertas = dict(s.execute(select(Alerta.transacao_id, Alerta.id)
                                     .where(Alerta.transacao_id.in_(list(transacoes.values())))).all())
            for ordem, k in enumerate(passam):
                d = decisoes[avaliar[k]]
                decisoes[avaliar[k]] = Decisao(d.movimento_id, d.estado, d.motivo, d.nivel, d.risco,
                                               alertas.get(transacoes[ordem]))

    total = {e: sum(d.estado == e for d in decisoes) for e in ("autorizado", "retido", "recusado")}
    registo.info("Banco (%s): %d pedido(s) de débito: %d autorizado(s), %d retido(s), %d recusado(s)",
                 origem, len(pedidos), total["autorizado"], total["retido"], total["recusado"])
    return decisoes
