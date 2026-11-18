"""Análise de ficheiros grandes em segundo plano, com uma fila simples em Python (queue + uma thread), sem Redis.

O ficheiro enviado fica em instancia/envios/ até ser processado e o estado de cada tarefa fica no banco:
se o servidor parar a meio, as tarefas por acabar voltam à fila no arranque seguinte.
"""
from __future__ import annotations

import logging
import queue
import secrets
import threading
from datetime import datetime
from pathlib import Path
from collections.abc import Callable

import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from . import auditoria, contas, correio, repositorio
from .banco import Tarefa
from .entrada import ler_csv, validar
from .servico import avaliar_transacoes

registo = logging.getLogger("detetor")


def _agora() -> datetime:
    return datetime.now().replace(microsecond=0)


def analisar_ficheiro(artefatos, sessoes: sessionmaker, cfg, transacoes: pd.DataFrame, nome_ficheiro: str,
                      utilizador_id: int | None, cortes=None) -> tuple[str, int, int]:
    """Avalia, grava a análise e os alertas e avisa por e-mail. Devolve (chave, transações, alertas)."""
    resultado = avaliar_transacoes(artefatos, transacoes, cortes=cortes)
    resultado = resultado.sort_values("risco", ascending=False, kind="stable").reset_index(drop=True)
    chave = repositorio.guardar_analise(sessoes, resultado, "ficheiro", artefatos.detector.escolhido, nome_ficheiro,
                                        utilizador_id, gerar_alertas=True, versao_modelo=artefatos.versao)
    n_alertas = int((resultado["nivel"] != "Normal").sum())
    correio.avisar_alertas(cfg, nome_ficheiro[:200], int((resultado["nivel"] == "Alto risco").sum()), n_alertas)
    registo.info("Ficheiro '%s' analisado: %d transações, %d com alerta", nome_ficheiro, len(resultado), n_alertas)
    return chave, len(resultado), n_alertas


class FilaTarefas:
    def __init__(self, sessoes: sessionmaker, pasta: Path, obter_artefatos: Callable, cfg):
        self.sessoes, self.pasta, self.obter_artefatos, self.cfg = sessoes, Path(pasta), obter_artefatos, cfg
        self.obter_cortes: Callable = lambda: None  # a aplicação liga aqui os limiares em vigor
        self._fila: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._trinco = threading.Lock()

    # ------------------------------------------------------------ pedir
    def submeter(self, conteudo: bytes, nome_ficheiro: str, linhas: int, utilizador_id: int | None) -> str:
        chave = secrets.token_urlsafe(16)
        self.pasta.mkdir(parents=True, exist_ok=True)
        (self.pasta / f"{chave}.csv").write_bytes(conteudo)
        with self.sessoes.begin() as sessao:
            sessao.add(Tarefa(chave=chave, estado="pendente", nome_ficheiro=nome_ficheiro[:255], linhas=linhas,
                              utilizador_id=utilizador_id, criada_em=_agora()))
        self._enfileirar(chave)
        registo.info("Ficheiro '%s' (%d linhas) na fila de processamento", nome_ficheiro, linhas)
        return chave

    def retomar(self) -> int:
        """Volta a pôr na fila o que ficou por processar (o servidor parou a meio)."""
        with self.sessoes.begin() as sessao:
            pendentes = list(sessao.scalars(select(Tarefa).where(Tarefa.estado.in_(("pendente", "a_processar")))
                                            .order_by(Tarefa.id)))
            for t in pendentes:
                t.estado, t.iniciada_em = "pendente", None
            chaves = [t.chave for t in pendentes]
        for chave in chaves:
            self._enfileirar(chave)
        if chaves:
            registo.info("%d ficheiro(s) por processar voltaram à fila", len(chaves))
        return len(chaves)

    def obter(self, chave: str) -> Tarefa | None:
        with self.sessoes() as sessao:
            return sessao.scalar(select(Tarefa).where(Tarefa.chave == chave))

    def listar_do_utilizador(self, utilizador_id: int | None, limite: int = 10) -> list[Tarefa]:
        consulta = select(Tarefa).where(Tarefa.estado != "concluida").order_by(Tarefa.id.desc()).limit(limite)
        if utilizador_id is not None:
            consulta = consulta.where(Tarefa.utilizador_id == utilizador_id)
        with self.sessoes() as sessao:
            return list(sessao.scalars(consulta))

    def esperar(self) -> None:
        """Bloqueia até a fila ficar vazia (usado nos testes)."""
        self._fila.join()

    # ------------------------------------------------------------ trabalhar
    def _enfileirar(self, chave: str) -> None:
        with self._trinco:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._trabalhar, name="fila-ficheiros", daemon=True)
                self._thread.start()
        self._fila.put(chave)

    def _trabalhar(self) -> None:
        while True:
            chave = self._fila.get()
            try:
                self._executar(chave)
            except Exception:  # nunca deixar a thread morrer: a próxima tarefa tem de correr
                registo.exception("Erro inesperado na tarefa %s", chave)
            finally:
                self._fila.task_done()

    def _mudar(self, chave: str, **valores) -> None:
        with self.sessoes.begin() as sessao:
            sessao.execute(update(Tarefa).where(Tarefa.chave == chave).values(**valores))

    def _executar(self, chave: str) -> None:
        tarefa = self.obter(chave)
        if tarefa is None or tarefa.estado != "pendente":
            return
        caminho = self.pasta / f"{chave}.csv"
        self._mudar(chave, estado="a_processar", iniciada_em=_agora())
        try:
            artefatos = self.obter_artefatos()
            if artefatos is None:
                raise RuntimeError("os modelos não estão carregados")
            transacoes = validar(ler_csv(caminho.read_bytes()))
            analise, total, n_alertas = analisar_ficheiro(artefatos, self.sessoes, self.cfg, transacoes,
                                                          tarefa.nome_ficheiro, tarefa.utilizador_id,
                                                          self.obter_cortes())
        except (ValueError, RuntimeError, OSError) as exc:
            registo.error("Ficheiro '%s' não foi analisado: %s", tarefa.nome_ficheiro, exc)
            self._mudar(chave, estado="falhou", concluida_em=_agora(), erro=str(exc)[:1000])
        except Exception as exc:
            self._mudar(chave, estado="falhou", concluida_em=_agora(), erro="Erro inesperado; veja o registo.")
            raise exc
        else:
            self._mudar(chave, estado="concluida", concluida_em=_agora(), analise_chave=analise)
            u = contas.obter(self.sessoes, tarefa.utilizador_id) if tarefa.utilizador_id else None
            auditoria.registar(self.sessoes, "analisar_ficheiro", u, nome_utilizador="fila", alvo=analise,
                               detalhe=f"{tarefa.nome_ficheiro[:200]}: {total} transações, {n_alertas} com alerta "
                                       "(em segundo plano)")
        finally:
            caminho.unlink(missing_ok=True)
