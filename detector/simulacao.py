"""Gerador de transações em fluxo contínuo para o simulador (simulador.py).

Usa os mesmos clientes que o gerador do treino (mesma semente e número de clientes): cada cliente continua
com a sua cidade, horário de pico, valor típico e categorias, por isso o modelo reconhece os seus hábitos.
Por cima das compras normais injeta os 5 tipos de fraude que o gerador conhece.

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

from . import gerador
from .gerador import CATEGORIAS, CIDADES, PAIS_HABITUAL, PAISES_ESTRANHOS, PESO_CATEGORIAS, TIPOS_FRAUDE

DESVIO_HORA = 2.5  # como gerador._hora
FRACAO_NOTURNA = 0.02  # compras legítimas entre a meia-noite e as 5h
TRANSACOES_POR_EVENTO = {"rajada": 5.5}  # média; os outros tipos têm uma transação


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
    prefixo: str = field(default_factory=lambda: "SIM-" + secrets.token_hex(3).upper())
    rng: np.random.Generator = field(init=False)
    _fila: list = field(init=False, default_factory=list)
    _contador: itertools.count = field(init=False, default_factory=itertools.count)
    _ids: itertools.count = field(init=False, default_factory=lambda: itertools.count(1))

    def __post_init__(self):
        if not 0 <= self.fraude_pct <= 50:
            raise ValueError("A percentagem de fraude tem de estar entre 0 e 50.")
        desconhecidos = set(self.tipos) - set(TIPOS_FRAUDE)
        if desconhecidos or not self.tipos:
            raise ValueError(f"Tipos de fraude válidos: {', '.join(TIPOS_FRAUDE)}")
        self.rng = np.random.default_rng(self.semente)
        self.taxas = np.array([c["taxa"] for c in self.clientes])  # compras por dia
        self.horarias = np.array([_distribuicao_horaria(c["pico"]) for c in self.clientes])  # cliente x hora
        legitimas_por_dia = self.taxas.sum()
        media_por_evento = np.mean([TRANSACOES_POR_EVENTO.get(t, 1.0) for t in self.tipos])
        p = self.fraude_pct / 100
        # fraudes / (fraudes + legítimas) = p, contando as transações de cada evento
        self.eventos_fraude_por_dia = legitimas_por_dia * p / (1 - p) / media_por_evento if p < 1 else 0.0
        self.agora = self.inicio
        self.proporcoes = np.array(PESO_CATEGORIAS) / np.sum(PESO_CATEGORIAS)

    # ------------------------------------------------------------ avançar o tempo
    def avancar(self, ate: datetime) -> list[dict]:
        """Gera os eventos entre o instante atual e `ate` e devolve as transações que já devem ser enviadas."""
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
        return prontas

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
            "valor": round(max(float(valor), 1.0), 2),
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
        return quando, self._transacao(c, quando, valor, self._cidade(c), pais, categoria)

    def _fraude(self, c: dict, tipo: str, quando: datetime) -> list[tuple[datetime, dict]]:
        cidade = self._cidade(c)
        categoria = lambda: str(self.rng.choice(CATEGORIAS, p=self.proporcoes))
        if tipo == "rajada":
            n = int(self.rng.integers(4, 8))
            momentos = [quando + timedelta(minutes=float(m)) for m in np.cumsum(self.rng.uniform(0.5, 3, n))]
            return [(m, self._transacao(c, m, max(c["tipico"] * self.rng.uniform(0.05, 0.3), 20), cidade,
                                        PAIS_HABITUAL, categoria(), tipo)) for m in momentos]
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
    rejeitadas: int = 0
    por_tipo: dict = field(default_factory=lambda: {t: [0, 0] for t in TIPOS_FRAUDE})  # [enviadas, com alerta]

    def registar(self, enviada: dict, resultado: dict) -> None:
        if resultado.get("duplicada"):
            self.duplicadas += 1
            return
        self.enviadas += 1
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
