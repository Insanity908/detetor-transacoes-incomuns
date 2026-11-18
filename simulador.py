"""Simulador de transações: envia compras normais e fraudes à API do detetor, continuamente, como se viessem de um banco.

Execução, na raiz do projeto (com o sistema ligado e uma chave criada em API > Chaves da API, ou com
`python gerir.py criar-chave Simulador`):

    python simulador.py --chave dtf_...                 velocidade 60: um dia simulado em 24 minutos
    python simulador.py --velocidade 600 --fraude 5     mais depressa e com mais fraude
    python simulador.py --tipos rajada,valor_alto       só estes tipos de fraude
    python simulador.py --duracao 300                   pára ao fim de 5 minutos (0 = até Ctrl+C)
    python simulador.py --cenario novo_tipo             cenário de mudança: novo_tipo, habitos ou valores

A chave também pode vir da variável de ambiente DETETOR_CHAVE_API. Os alertas aparecem na fila (Alertas).
A cada intervalo, o simulador mostra quantas fraudes injetou e quantas o sistema apanhou.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from collections.abc import Callable

from detector.gerador import TIPOS_FRAUDE
from detector.simulacao import (CENARIOS, NOMES_TIPO_SIMULACAO, TIPOS_SIMULACAO, Estatisticas,
                                Simulador, clientes_do_treino)

MAX_LOTE = 500
TENTATIVAS = 5


class ErroFatal(RuntimeError):
    """Não adianta tentar de novo (chave inválida, dados recusados)."""

    def __init__(self, mensagem: str, estado: int | None = None):
        super().__init__(mensagem)
        self.estado = estado


class ClienteApi:
    """Fala com a API. `transporte(metodo, caminho, corpo) -> (estado, cabeçalhos, json)` pode ser trocado nos testes."""

    def __init__(self, url: str, chave: str, transporte: Callable | None = None, dormir: Callable = time.sleep):
        self.url, self.chave, self.dormir = url.rstrip("/"), chave, dormir
        self.transporte = transporte or self._urllib

    def _urllib(self, metodo: str, caminho: str, corpo: dict | None):
        dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
        pedido = urllib.request.Request(self.url + caminho, data=dados, method=metodo, headers={
            "Authorization": f"Bearer {self.chave}", "Content-Type": "application/json", "User-Agent": "simulador"})
        try:
            with urllib.request.urlopen(pedido, timeout=60) as resposta:
                return resposta.status, dict(resposta.headers), json.loads(resposta.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                conteudo = json.loads(exc.read() or b"{}")
            except ValueError:
                conteudo = {}
            return exc.code, dict(exc.headers), conteudo

    def pedir(self, metodo: str, caminho: str, corpo: dict | None = None) -> dict:
        espera = 1.0
        for tentativa in range(1, TENTATIVAS + 1):
            try:
                estado, cabecalhos, resposta = self.transporte(metodo, caminho, corpo)
            except OSError as exc:  # servidor desligado ou rede em baixo
                estado, cabecalhos, resposta = None, {}, {"mensagem": str(exc)}
            if estado == 200:
                return resposta
            if estado in (401, 403):
                raise ErroFatal(f"Chave recusada: {resposta.get('mensagem', estado)}")
            if estado in (400, 415, 422):
                raise ErroFatal(f"Pedido recusado ({estado}): {json.dumps(resposta, ensure_ascii=False)[:500]}", estado)
            if estado == 429:
                esperar = float(cabecalhos.get("Retry-After", 1))
                print(f"  limite de pedidos atingido; à espera {esperar:.0f} s", flush=True)
                self.dormir(esperar)
                continue
            print(f"  falha ({estado or 'sem ligação'}: {resposta.get('mensagem', '')}); tentativa {tentativa} de "
                  f"{TENTATIVAS}", flush=True)
            self.dormir(espera)
            espera = min(espera * 2, 30)
        raise ErroFatal("O servidor não respondeu depois de várias tentativas.")


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{100 * x:.1f}%".replace(".", ",")


def resumo(est: Estatisticas, agora: datetime) -> str:
    linhas = [f"[{agora:%d/%m %H:%M} simulado] enviadas {est.enviadas} · fraudes {est.fraudes} "
              f"(apanhadas {est.fraudes_com_alerta}, {_pct(est.detecao)}) · "
              f"alertas em legítimas {est.legitimas_com_alerta} ({_pct(est.falsos_alertas)})"]
    linhas.append(f"   recusadas por saldo {est.recusadas_saldo} · retidas {est.retidas}")
    tipos = [f"{NOMES_TIPO_SIMULACAO[t]} {a}/{n}" for t, (n, a) in est.por_tipo.items() if n]
    if tipos:
        linhas.append("   por tipo (apanhadas/enviadas): " + " · ".join(tipos))
    return "\n".join(linhas)


def simular(api: ClienteApi, sim: Simulador, velocidade: float, duracao: float = 0, passo: float = 1.0,
            intervalo_resumo: float = 10.0, relogio: Callable = time.monotonic, dormir: Callable = time.sleep,
            escrever: Callable = print) -> Estatisticas:
    """Avança o tempo simulado ao ritmo de `velocidade` e envia, a cada passo, as transações que já aconteceram."""
    est = Estatisticas()
    inicio_real, inicio_simulado = relogio(), sim.agora
    ultimo_resumo = inicio_real
    avisou_sem_conta = False
    try:
        while True:
            decorrido = relogio() - inicio_real
            if duracao and decorrido >= duracao:
                break
            agora = inicio_simulado + timedelta(seconds=decorrido * velocidade)
            prontas, depositos = sim.avancar(agora)
            for i in range(0, len(depositos), MAX_LOTE):
                try:
                    api.pedir("POST", "/api/v1/depositos", {"depositos": depositos[i:i + MAX_LOTE]})
                except ErroFatal as exc:
                    if exc.estado != 422:
                        raise
                    if not avisou_sem_conta:  # a API não escreveu nada: seguem só as transações
                        escrever("Os clientes do simulador não têm conta no banco: corra gerir.py preparar.")
                        avisou_sem_conta = True
            for i in range(0, len(prontas), MAX_LOTE):
                lote = prontas[i:i + MAX_LOTE]
                resposta = api.pedir("POST", "/api/v1/transacoes", {"transacoes": lote})
                for enviada, resultado in zip(lote, resposta["resultados"], strict=True):
                    est.registar(enviada, resultado)
            if relogio() - ultimo_resumo >= intervalo_resumo:
                escrever(resumo(est, agora))
                ultimo_resumo = relogio()
            dormir(passo)
    except KeyboardInterrupt:
        escrever("Interrompido.")
    escrever(resumo(est, sim.agora))
    return est


def inicio_padrao(api: ClienteApi) -> datetime:
    """Continua onde o histórico parou: depois do fim do treino ou da última transação já recebida pela API."""
    estado = api.pedir("GET", "/api/v1/estado")
    marcos = [datetime.fromisoformat(estado["fim_do_historico_de_treino"])]
    if estado.get("ultima_transacao_recebida"):
        marcos.append(datetime.fromisoformat(estado["ultima_transacao_recebida"]))
    return max(marcos).replace(microsecond=0) + timedelta(minutes=1)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Envia transações simuladas à API do detetor de fraudes.")
    p.add_argument("--url", default=os.environ.get("DETETOR_URL", "http://127.0.0.1:5000"))
    p.add_argument("--chave", default=os.environ.get("DETETOR_CHAVE_API"), help="chave da API (dtf_...)")
    p.add_argument("--velocidade", type=float, default=60,
                   help="segundos simulados por segundo real (60 = um dia em 24 minutos; 1 = tempo real)")
    p.add_argument("--fraude", type=float, default=2.0, help="percentagem de transações fraudulentas (0 a 50)")
    p.add_argument("--tipos", default=",".join(TIPOS_FRAUDE), help=f"tipos de fraude: {', '.join(TIPOS_SIMULACAO)}")
    p.add_argument("--cenario", default="nenhum", choices=list(CENARIOS),
                   help="mudança no comportamento: " + "; ".join(f"{k}: {v}" for k, v in CENARIOS.items()))
    p.add_argument("--duracao", type=float, default=0, help="segundos reais até parar (0 = até Ctrl+C)")
    p.add_argument("--inicio", help="data e hora simulada inicial (ISO 8601); por omissão, onde o histórico parou")
    p.add_argument("--clientes", type=int, default=300, help="clientes do treino (tem de ser igual ao treino)")
    p.add_argument("--semente", type=int, default=None, help="para repetir exatamente a mesma simulação")
    args = p.parse_args(argv)
    if not args.chave:
        print("Falta a chave: use --chave ou a variável DETETOR_CHAVE_API "
              "(crie uma em API > Chaves da API ou com python gerir.py criar-chave Simulador).", file=sys.stderr)
        return 2
    if not 0 < args.velocidade <= 100_000:
        print("--velocidade tem de estar entre 0 e 100000.", file=sys.stderr)
        return 2
    api = ClienteApi(args.url, args.chave)
    try:
        inicio = datetime.fromisoformat(args.inicio) if args.inicio else inicio_padrao(api)
        sim = Simulador(clientes_do_treino(args.clientes), inicio, args.fraude,
                        tuple(t.strip() for t in args.tipos.split(",") if t.strip()), args.semente, cenario=args.cenario)
    except (ValueError, ErroFatal) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    print(f"Simulador ligado a {args.url}: início simulado {inicio:%d/%m/%Y %H:%M}, velocidade {args.velocidade:g}x, "
          f"{args.fraude:g}% de fraude ({', '.join(sorted(set(sim.tipos)))}), cenário {args.cenario}. "
          f"Ids {sim.prefixo}-*. Ctrl+C para parar.", flush=True)
    try:
        simular(api, sim, args.velocidade, args.duracao, escrever=lambda t: print(t, flush=True))
    except ErroFatal as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
