"""Gerador de transações em fluxo contínuo para o simulador (simulador.py).

Usa os mesmos clientes que o gerador do treino (mesma semente e número de clientes): cada cliente continua
com a sua cidade, horário de pico, valor típico e categorias, por isso o modelo reconhece os seus hábitos.
Por cima das compras normais injeta os 6 tipos de fraude que o gerador conhece.

Cenários de mudança (opção --cenario), para mostrar a monitorização a detetar a queda de desempenho e o
re-treino a corrigi-la:
- novo_tipo: aparece a "compra fracionada", um tipo de fraude que o modelo nunca viu (várias compras de valor
  médio, nas categorias e na cidade habituais do cliente, espaçadas de 15 a 40 minutos);
- habitos: os clientes passam a comprar seis horas mais tarde e metade das compras numa categoria nova;
- valores: inflação, todos os valores (legítimos e fraudes) passam a ser 2,5 vezes maiores.

O tempo é simulado: começa onde o histórico acabou e avança `velocidade` vezes mais depressa do que o relógio
(60 = um minuto simulado por segundo real; um dia inteiro em 24 minutos). O ritmo das compras vem da taxa
diária de cada cliente e do seu horário, como no gerador: de madrugada há poucas, à hora de pico muitas.
"""
from __future__ import annotations

import heapq
import itertools
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from . import contas_bancarias, gerador
from .gerador import (CATEGORIA_TRANSFERENCIA, CATEGORIAS, CIDADES, NOMES_TIPO_FRAUDE, PAIS_HABITUAL, PAISES_ESTRANHOS,
                      PESO_CATEGORIAS, TIPOS_FRAUDE)

DESVIO_HORA = 2.5  # como gerador._hora
FRACAO_NOTURNA = 0.02  # compras legítimas entre a meia-noite e as 5h
TIPO_NOVO = "fracionada"
TIPOS_SIMULACAO = (*TIPOS_FRAUDE, TIPO_NOVO)  # o treino só conhece TIPOS_FRAUDE
NOMES_TIPO_SIMULACAO = NOMES_TIPO_FRAUDE | {TIPO_NOVO: "Compra fracionada (nova)"}
TRANSACOES_POR_EVENTO = {"rajada": 5.5, TIPO_NOVO: 4.0}  # média; os outros tipos têm uma transação
CENARIOS = {
    "nenhum": "Sem mudanças: os clientes e as fraudes continuam como no treino.",
    "novo_tipo": "Novo tipo de fraude: compras fracionadas que o modelo nunca viu (metade das fraudes).",
    "habitos": "Mudança de hábitos: compras seis horas mais tarde e metade numa categoria nova.",
    "valores": "Inflação: todos os valores 2,5 vezes maiores.",
}
INFLACAO = 2.5
ATRASO_HABITOS = 6  # horas


def clientes_do_treino(n_clientes: int = 300, dias: int = gerador.DIAS, semente: int = 42) -> list[dict]:
    """Os perfis exatos usados no treino: gerar_transacoes cria-os primeiro, com o mesmo gerador aleatório."""
    return gerador._clientes(np.random.default_rng(semente), n_clientes, dias)


def _distribuicao_horaria(pico: float) -> np.ndarray:
    """Probabilidade de cada hora do dia (0..23) para um cliente: normal à volta do pico e 2% de madrugada."""
    horas = np.arange(24) + 0.5
    diurna = np.exp(-((horas - pico) ** 2) / (2 * DESVIO_HORA ** 2))
    diurna[horas < 6] = 0
    diurna = diurna / diurna.sum() * (1 - FRACAO_NOTURNA)
    noturna = np.where(horas < 5, FRACAO_NOTURNA / 5, 0)
    return diurna + noturna


@dataclass
class Simulador:
    clientes: list[dict]
    inicio: datetime
    fraude_pct: float = 2.0  # percentagem das transações que são fraude
    tipos: tuple[str, ...] = TIPOS_FRAUDE
    semente: int | None = None
    cenario: str = "nenhum"
    prefixo: str = field(default_factory=lambda: "SIM-" + secrets.token_hex(3).upper())
    rng: np.random.Generator = field(init=False)
    _fila: list = field(init=False, default_factory=list)
    _contador: itertools.count = field(init=False, default_factory=itertools.count)
    _ids: itertools.count = field(init=False, default_factory=lambda: itertools.count(1))

    def __post_init__(self):
        if not 0 <= self.fraude_pct <= 50:
            raise ValueError("A percentagem de fraude tem de estar entre 0 e 50.")
        desconhecidos = set(self.tipos) - set(TIPOS_SIMULACAO)
        if desconhecidos or not self.tipos:
            raise ValueError(f"Tipos de fraude válidos: {', '.join(TIPOS_SIMULACAO)}")
        if self.cenario not in CENARIOS:
            raise ValueError(f"Cenários válidos: {', '.join(CENARIOS)}")
        if self.cenario == "novo_tipo" and TIPO_NOVO not in self.tipos:  # metade das fraudes passa a ser do tipo novo
            self.tipos = (*self.tipos, *([TIPO_NOVO] * len(self.tipos)))
        self.rng = np.random.default_rng(self.semente)
        # Gerador próprio para os salários: não altera a sequência das compras e fraudes para uma dada semente.
        self.rng_salarios = np.random.default_rng(None if self.semente is None else [self.semente, 25])
        self.taxas = np.array([c["taxa"] for c in self.clientes])  # compras por dia
        atraso = ATRASO_HABITOS if self.cenario == "habitos" else 0
        self.horarias = np.array([np.roll(_distribuicao_horaria(c["pico"]), atraso) for c in self.clientes])
        self.fator_valor = INFLACAO if self.cenario == "valores" else 1.0
        legitimas_por_dia = self.taxas.sum()
        media_por_evento = np.mean([TRANSACOES_POR_EVENTO.get(t, 1.0) for t in self.tipos])
        p = self.fraude_pct / 100
        # fraudes / (fraudes + legítimas) = p, contando as transações de cada evento
        self.eventos_fraude_por_dia = legitimas_por_dia * p / (1 - p) / media_por_evento if p < 1 else 0.0
        self.agora = self.inicio
        self.proporcoes = np.array(PESO_CATEGORIAS) / np.sum(PESO_CATEGORIAS)

    # ------------------------------------------------------------ avançar o tempo
    def avancar(self, ate: datetime) -> tuple[list[dict], list[dict]]:
        """Gera os eventos entre o instante atual e `ate`.

        Devolve `(transações, depósitos)`: as transações que já devem ser enviadas e os salários cujo instante
        (dia 25, 08:00 simuladas) foi atravessado."""
        depositos = self._salarios(self.agora, ate)
        while self.agora < ate:
            fim_da_hora = (self.agora.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            passo_fim = min(ate, fim_da_hora)  # a taxa muda de hora para hora
            horas = (passo_fim - self.agora).total_seconds() / 3600
            hora = self.agora.hour
            contagens = self.rng.poisson(self.taxas * self.horarias[:, hora] * horas)  # compras por cliente
            for i in np.flatnonzero(contagens):
                for _ in range(int(contagens[i])):
                    self._agendar([self._legitima(self.clientes[i], self._instante(self.agora, passo_fim))])
            for _ in range(int(self.rng.poisson(self.eventos_fraude_por_dia * horas / 24))):
                cliente = self.clientes[int(self.rng.integers(len(self.clientes)))]
                tipo = str(self.rng.choice(self.tipos))
                self._agendar(self._fraude(cliente, tipo, self._instante(self.agora, passo_fim)))
            self.agora = passo_fim
        prontas = []
        while self._fila and self._fila[0][0] <= ate:
            prontas.append(heapq.heappop(self._fila)[2])
        return prontas, depositos

    def _salarios(self, de: datetime, ate: datetime) -> list[dict]:
        """Um depósito de salário por cliente por cada dia 25, 08:00, em `(de, ate]`."""
        depositos = []
        ano, mes = de.year, de.month
        while True:
            instante = datetime(ano, mes, 25, 8)
            if instante > ate:
                break
            if instante > de:
                for c in self.clientes:
                    valor = round(float(c["tipico"] * c["taxa"] * 30 * self.rng_salarios.uniform(1.1, 1.4)), 2)
                    depositos.append({
                        "id_deposito": f"{self.prefixo}-SAL-{instante:%Y%m}-{c['cliente']}",
                        "cliente": c["cliente"], "valor": valor, "data_hora": instante.isoformat(),
                        "descricao": "Salário"})
            ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
        return depositos

    def pendentes(self) -> int:
        return len(self._fila)

    def _instante(self, de: datetime, ate: datetime) -> datetime:
        return de + timedelta(seconds=float(self.rng.uniform(0, (ate - de).total_seconds())))

    def _agendar(self, transacoes: list[tuple[datetime, dict]]) -> None:
        for envio, t in transacoes:
            heapq.heappush(self._fila, (envio, next(self._contador), t))

    # ------------------------------------------------------------ transações
    def _cidade(self, c: dict) -> str:
        return c["nova_cidade"] or c["cidade"]  # o simulador continua depois do treino: a mudança já aconteceu

    def _transacao(self, c: dict, quando: datetime, valor: float, cidade: str, pais: str, categoria: str,
                   tipo: str = "") -> dict:
        return {
            "id_transacao": f"{self.prefixo}-{next(self._ids):07d}",
            "cliente": c["cliente"],
            "data_hora": quando.replace(microsecond=0).isoformat(),
            "valor": round(max(float(valor) * self.fator_valor, 1.0), 2),
            "cidade": cidade,
            "pais": pais,
            "categoria": categoria,
            "simulacao": {"fraude": bool(tipo), "tipo": tipo},
        }

    def _legitima(self, c: dict, quando: datetime) -> tuple[datetime, dict]:
        valor = c["tipico"] * self.rng.lognormal(0, 0.5)
        if self.rng.random() < 0.01:
            valor = c["tipico"] * self.rng.uniform(3, 8)
        pais = "ZA" if self.rng.random() < 0.01 else PAIS_HABITUAL
        categoria = str(self.rng.choice(c["categorias"], p=c["peso_categorias"]))
        if self.cenario == "habitos" and self.rng.random() < 0.5:
            categoria = str(self.rng.choice([x for x in CATEGORIAS if x not in c["categorias"]]))
        return quando, self._transacao(c, quando, valor, self._cidade(c), pais, categoria)

    def _fraude(self, c: dict, tipo: str, quando: datetime) -> list[tuple[datetime, dict]]:
        cidade = self._cidade(c)
        categoria = lambda: str(self.rng.choice(CATEGORIAS, p=self.proporcoes))
        if tipo == "rajada":
            n = int(self.rng.integers(4, 8))
            momentos = [quando + timedelta(minutes=float(m)) for m in np.cumsum(self.rng.uniform(0.5, 3, n))]
            return [(m, self._transacao(c, m, max(c["tipico"] * self.rng.uniform(0.05, 0.3), 20), cidade,
                                        PAIS_HABITUAL, categoria(), tipo)) for m in momentos]
        if tipo == TIPO_NOVO:
            n = int(self.rng.integers(3, 6))
            momentos = [quando + timedelta(minutes=float(m)) for m in np.cumsum(self.rng.uniform(15, 40, n))]
            return [(m, self._transacao(c, m, c["tipico"] * self.rng.uniform(1.5, 2.5), cidade, PAIS_HABITUAL,
                                        str(self.rng.choice(c["categorias"], p=c["peso_categorias"])), tipo))
                    for m in momentos]
        if tipo == "valor_alto":
            return [(quando, self._transacao(c, quando, c["tipico"] * self.rng.uniform(3, 15), cidade, PAIS_HABITUAL,
                                             categoria(), tipo))]
        if tipo == "pais_estranho":
            return [(quando, self._transacao(c, quando, c["tipico"] * self.rng.lognormal(0, 0.6), cidade,
                                             str(self.rng.choice(PAISES_ESTRANHOS)), categoria(), tipo))]
        if tipo == "horario_estranho":
            # Uma compra de madrugada (1h-5h) do dia simulado, comunicada agora.
            dia = quando if quando.hour >= 5 else quando - timedelta(days=1)
            madrugada = dia.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=float(self.rng.uniform(1, 5)))
            return [(quando, self._transacao(c, madrugada, c["tipico"] * self.rng.lognormal(0.3, 0.6), cidade,
                                             PAIS_HABITUAL, categoria(), tipo))]
        if tipo == "conta_esvaziada":  # uma transferência enorme, de uma cidade que não é a do cliente
            outra = str(self.rng.choice([x for x in CIDADES if x != cidade]))
            return [(quando, self._transacao(c, quando, c["tipico"] * self.rng.uniform(15, 40), outra, PAIS_HABITUAL,
                                             CATEGORIA_TRANSFERENCIA, tipo))]
        # conta_tomada: cidade nova, categoria nova e valor médio-alto
        outra = str(self.rng.choice([x for x in CIDADES if x != cidade]))
        novas = [x for x in CATEGORIAS if x not in c["categorias"]]
        p = np.array([PESO_CATEGORIAS[CATEGORIAS.index(x)] for x in novas])
        nova = str(self.rng.choice(novas, p=p / p.sum()))
        return [(quando, self._transacao(c, quando, c["tipico"] * self.rng.uniform(2, 5), outra, PAIS_HABITUAL, nova, tipo))]


@dataclass
class Estatisticas:
    """O que o simulador sabe (a verdade do gerador) comparado com o que o sistema respondeu."""
    enviadas: int = 0
    fraudes: int = 0
    fraudes_com_alerta: int = 0
    legitimas_com_alerta: int = 0
    duplicadas: int = 0
    recusadas_saldo: int = 0  # débitos recusados por falta de saldo
    retidas: int = 0  # movimentos retidos pelo detetor
    rejeitadas: int = 0
    por_tipo: dict = field(default_factory=lambda: {t: [0, 0] for t in TIPOS_SIMULACAO})  # [enviadas, com alerta]

    def registar(self, enviada: dict, resultado: dict) -> None:
        if resultado.get("duplicada"):
            self.duplicadas += 1
            return
        self.enviadas += 1
        movimento = resultado.get("movimento") or {}
        if movimento.get("estado") == "recusado" and movimento.get("motivo") == contas_bancarias.MOTIVO_SALDO:
            self.recusadas_saldo += 1
        elif movimento.get("estado") == "retido":
            self.retidas += 1
        alerta = bool(resultado.get("alerta"))
        if enviada["simulacao"]["fraude"]:
            self.fraudes += 1
            self.fraudes_com_alerta += alerta
            self.por_tipo[enviada["simulacao"]["tipo"]][0] += 1
            self.por_tipo[enviada["simulacao"]["tipo"]][1] += alerta
        else:
            self.legitimas_com_alerta += alerta

    @property
    def detecao(self) -> float | None:
        return self.fraudes_com_alerta / self.fraudes if self.fraudes else None

    @property
    def falsos_alertas(self) -> float | None:
        legitimas = self.enviadas - self.fraudes
        return self.legitimas_com_alerta / legitimas if legitimas else None
