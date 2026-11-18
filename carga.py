"""Teste de carga da API: vários clientes em paralelo enviam transações do simulador o mais depressa possível.

Execução, com o sistema ligado e uma chave com limite alto (python gerir.py criar-chave Carga --limite 60000):

    python carga.py --chave dtf_... --clientes 8 --lote 20 --duracao 60

Mostra transações por segundo, o tempo de resposta (mediana, p95, p99) e os erros por código.
Atenção: grava as transações e os alertas na base de dados do sistema. Use uma instalação de teste.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from detector.simulacao import Simulador, clientes_do_treino


@dataclass
class Resultado:
    tempos: list[float] = field(default_factory=list)  # segundos por pedido bem-sucedido
    estados: Counter = field(default_factory=Counter)
    transacoes: int = 0
    duracao: float = 0.0

    def resumo(self) -> dict:
        ordenados = sorted(self.tempos)
        p = lambda q: ordenados[min(len(ordenados) - 1, int(q * len(ordenados)))] * 1000 if ordenados else None
        return {
            "pedidos": sum(self.estados.values()),
            "pedidos_ok": self.estados.get(200, 0),
            "transacoes": self.transacoes,
            "transacoes_por_segundo": self.transacoes / self.duracao if self.duracao else 0.0,
            "mediana_ms": statistics.median(ordenados) * 1000 if ordenados else None,
            "p95_ms": p(0.95),
            "p99_ms": p(0.99),
            "maximo_ms": ordenados[-1] * 1000 if ordenados else None,
            "estados": dict(self.estados),
        }


def enviar_urllib(url: str, chave: str, corpo: dict) -> int:
    pedido = urllib.request.Request(url.rstrip("/") + "/api/v1/transacoes", data=json.dumps(corpo).encode("utf-8"),
                                    method="POST", headers={"Authorization": f"Bearer {chave}",
                                                            "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(pedido, timeout=120) as resposta:
            resposta.read()
            return resposta.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return 0  # sem ligação


def gerar_transacoes(n: int, inicio: datetime, fraude_pct: float = 2.0, semente: int = 1) -> list[dict]:
    sim = Simulador(clientes_do_treino(), inicio, fraude_pct=fraude_pct, semente=semente)
    transacoes, fim = [], inicio
    while len(transacoes) < n:
        fim += timedelta(hours=6)
        transacoes.extend(sim.avancar(fim)[0])
    return transacoes[:n]


def correr(enviar, transacoes: list[dict], clientes: int, lote: int, duracao: float,
           relogio=time.perf_counter) -> Resultado:
    """`enviar(corpo) -> código HTTP`. Cada cliente tira o próximo lote da lista até acabar o tempo ou as transações."""
    resultado = Resultado()
    trinco = threading.Lock()
    proximo = [0]
    fim = relogio() + duracao

    def trabalhar():
        while relogio() < fim:
            with trinco:
                i = proximo[0]
                proximo[0] += lote
            if i >= len(transacoes):
                return
            parte = transacoes[i:i + lote]
            inicio = relogio()
            estado = enviar({"transacoes": parte})
            demorou = relogio() - inicio
            with trinco:
                resultado.estados[estado] += 1
                if estado == 200:
                    resultado.tempos.append(demorou)
                    resultado.transacoes += len(parte)

    comeco = relogio()
    threads = [threading.Thread(target=trabalhar, name=f"carga-{n}") for n in range(clientes)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    resultado.duracao = relogio() - comeco
    return resultado


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Teste de carga da API do detetor de fraudes.")
    p.add_argument("--url", default=os.environ.get("DETETOR_URL", "http://127.0.0.1:5000"))
    p.add_argument("--chave", default=os.environ.get("DETETOR_CHAVE_API"))
    p.add_argument("--clientes", type=int, default=8, help="pedidos em paralelo")
    p.add_argument("--lote", type=int, default=20, help="transações por pedido (1 a 500)")
    p.add_argument("--duracao", type=float, default=60, help="segundos")
    p.add_argument("--inicio", default="2026-05-01T00:00:00", help="data simulada das transações geradas")
    args = p.parse_args(argv)
    if not args.chave:
        print("Falta a chave: use --chave ou DETETOR_CHAVE_API.", file=sys.stderr)
        return 2
    if not 1 <= args.lote <= 500 or args.clientes < 1:
        print("--lote tem de estar entre 1 e 500 e --clientes ser pelo menos 1.", file=sys.stderr)
        return 2
    n = int(args.clientes * args.duracao * 200) + 1000  # mais do que o servidor consegue avaliar no tempo dado
    print(f"A gerar {n} transações...", flush=True)
    transacoes = gerar_transacoes(n, datetime.fromisoformat(args.inicio))
    print(f"A enviar para {args.url} com {args.clientes} clientes em paralelo, lotes de {args.lote}, "
          f"durante {args.duracao:g} s...", flush=True)
    r = correr(lambda corpo: enviar_urllib(args.url, args.chave, corpo), transacoes, args.clientes, args.lote,
               args.duracao).resumo()
    fmt = lambda v: "—" if v is None else f"{v:.0f}"
    print(f"Pedidos: {r['pedidos']} ({r['pedidos_ok']} com sucesso) · transações avaliadas: {r['transacoes']} "
          f"({r['transacoes_por_segundo']:.1f} por segundo)")
    print(f"Tempo por pedido: mediana {fmt(r['mediana_ms'])} ms · p95 {fmt(r['p95_ms'])} ms · "
          f"p99 {fmt(r['p99_ms'])} ms · máximo {fmt(r['maximo_ms'])} ms")
    erros = {k: v for k, v in r["estados"].items() if k != 200}
    if erros:
        print("Erros:", ", ".join(f"{'sem ligação' if k == 0 else k}: {v}" for k, v in sorted(erros.items())))
    else:
        print("Erros: nenhum")
    return 0 if not erros else 1


if __name__ == "__main__":
    sys.exit(main())
