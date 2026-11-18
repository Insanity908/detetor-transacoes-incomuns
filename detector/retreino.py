"""Re-treino a partir da interface, com o histórico do treino e as transações recebidas pela API.

Rótulos: a decisão do analista, quando existe (fraude confirmada = 1, falso alarme = 0); senão, o que o
gerador do simulador marcou. Transações sem nenhum dos dois ficam de fora.

Divisão por tempo, centrada no que é recente, porque é aí que o comportamento pode ter mudado:
- treino: o histórico anterior (sem o aquecimento e sem os seus últimos dias) e os primeiros 60% das
  transações da API, estas com peso maior (PESO_RECENTES), para o modelo aprender padrões novos com poucos exemplos;
- validação (escolha do modelo e do limiar): os últimos dias do histórico e os 20% seguintes da API. Só com a
  API haveria poucas fraudes e o limiar ficaria instável (testado: muitos mais falsos alarmes);
- teste: os 20% mais recentes da API. A versão publicada é medida nas mesmas transações, para comparar.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from collections.abc import Callable

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance
from sklearn.metrics import confusion_matrix
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from . import versoes
from .artefatos import Artefatos, gravar
from .avaliacao import DIAS_AQUECIMENTO, metricas
from .banco import Alerta, Analise, TransacaoAvaliada
from .gerador import INICIO
from .limiares import metricas_alerta
from .modelos import NOMES, ORDEM_SIMPLICIDADE, treinar
from .perfis import FEATURES, calcular_features
from .servico import montar_resultado
from .simulacao import TIPOS_SIMULACAO

registo = logging.getLogger("detetor")
MIN_TRANSACOES = 300
MIN_FRAUDES = 5  # em cada um dos períodos de validação e de teste
FRACAO_TREINO_API, FRACAO_VALIDACAO_API = 0.6, 0.2
FRACAO_VALIDACAO_HISTORICO = 0.2  # dos dias do histórico depois do aquecimento
PESO_RECENTES = 5.0
COLUNAS = ["id_transacao", "cliente", "data_hora", "valor", "cidade", "pais", "categoria", "fraude", "tipo_fraude"]


def transacoes_rotuladas(sessoes: sessionmaker) -> pd.DataFrame:
    """As transações recebidas pela API que têm rótulo, com a origem do rótulo."""
    consulta = (select(TransacaoAvaliada.id_transacao, TransacaoAvaliada.cliente, TransacaoAvaliada.data_hora,
                       TransacaoAvaliada.valor, TransacaoAvaliada.cidade, TransacaoAvaliada.pais,
                       TransacaoAvaliada.categoria, TransacaoAvaliada.fraude_simulada,
                       TransacaoAvaliada.tipo_fraude_simulada, Alerta.estado)
                .join(Analise, Analise.id == TransacaoAvaliada.analise_id)
                .outerjoin(Alerta, Alerta.transacao_id == TransacaoAvaliada.id)
                .where(Analise.origem == "api")
                .order_by(TransacaoAvaliada.data_hora, TransacaoAvaliada.id))
    with sessoes() as sessao:
        linhas = sessao.execute(consulta).all()
    registos = []
    for (id_t, cliente, quando, valor, cidade, pais, categoria, simulada, tipo, estado) in linhas:
        if estado in ("fraude", "falso_alarme"):
            fraude, fonte = int(estado == "fraude"), "analista"
        elif simulada is not None:
            fraude, fonte = int(simulada), "gerador"
        else:
            continue
        registos.append({"id_transacao": id_t, "cliente": cliente, "data_hora": quando, "valor": valor,
                         "cidade": cidade, "pais": pais, "categoria": categoria, "fraude": fraude,
                         "tipo_fraude": (tipo or "") if fraude else "", "fonte_rotulo": fonte,
                         "corrigido": fonte == "analista" and simulada is not None and int(simulada) != fraude})
    df = pd.DataFrame(registos, columns=[*COLUNAS, "fonte_rotulo", "corrigido"])
    df["data_hora"] = pd.to_datetime(df["data_hora"])
    return df


def _resumo_alertas(y: np.ndarray, avaliado: pd.DataFrame) -> dict:
    return metricas_alerta(y, avaliado["nivel"].to_numpy())


def treinar_nova_versao(atual: Artefatos, sessoes: sessionmaker, pasta: Path, cortes=None) -> tuple[Artefatos, dict]:
    api = transacoes_rotuladas(sessoes)
    if len(api) < MIN_TRANSACOES:
        raise ValueError(f"São precisas pelo menos {MIN_TRANSACOES} transações da API com rótulo; há {len(api)}. "
                         "Deixe o simulador a correr mais tempo.")
    historico = atual.historico.copy()
    if "tipo_fraude" not in historico.columns:
        historico["tipo_fraude"] = ""
    ids_api = set(api["id_transacao"])
    historico = historico[~historico["id_transacao"].astype(str).isin(ids_api)]  # já entraram num re-treino anterior
    n_hist = len(historico)
    todos = pd.concat([historico[COLUNAS], api[COLUNAS]], ignore_index=True)
    todos["data_hora"] = pd.to_datetime(todos["data_hora"])
    origem_api = np.r_[np.zeros(n_hist, dtype=bool), np.ones(len(api), dtype=bool)]

    X = calcular_features(todos)  # cada transação vê as anteriores do mesmo cliente, do treino e da API
    y = todos["fraude"].to_numpy().astype(int)
    posicao_api = np.full(len(todos), -1)
    posicao_api[origem_api] = np.arange(len(api))  # api já vem ordenada por data_hora
    corte_treino = int(FRACAO_TREINO_API * len(api))
    corte_validacao = int((FRACAO_TREINO_API + FRACAO_VALIDACAO_API) * len(api))
    dia = (todos["data_hora"].dt.normalize() - pd.Timestamp(INICIO)).dt.days.to_numpy() + 1
    ultimo_dia = int(dia[~origem_api].max()) if n_hist else DIAS_AQUECIMENTO
    inicio_val_hist = ultimo_dia - round(FRACAO_VALIDACAO_HISTORICO * (ultimo_dia - DIAS_AQUECIMENTO))
    hist_treino = ~origem_api & (dia > DIAS_AQUECIMENTO) & (dia <= inicio_val_hist)
    hist_val = ~origem_api & (dia > inicio_val_hist)
    tr = hist_treino | (origem_api & (posicao_api < corte_treino))
    val = hist_val | (origem_api & (posicao_api >= corte_treino) & (posicao_api < corte_validacao))
    te = origem_api & (posicao_api >= corte_validacao)
    val_api = origem_api & val
    for nome, m in (("validação", val_api), ("teste", te)):
        if y[m].sum() < MIN_FRAUDES:
            raise ValueError(f"O período de {nome} (transações mais recentes da API) só tem {int(y[m].sum())} fraudes; "
                             f"são precisas pelo menos {MIN_FRAUDES}. Deixe o simulador a correr mais tempo.")

    pesos = np.where(origem_api[tr], PESO_RECENTES, 1.0)
    detector, parcial = treinar(X[tr], y[tr], X[val], y[val], pesos)
    modelos = {}
    for nome, pipeline in detector.classificadores.items():
        prob = pipeline.predict_proba(X.loc[te, FEATURES])[:, 1]
        modelos[nome] = {"limiar": detector.limiares[nome], "validacao": parcial["validacao"][nome],
                         "teste": metricas(y[te], prob, detector.limiares[nome])}
    modelos["isolation_forest"] = {
        "limiar": detector.limiar_anomalia, "validacao": parcial["validacao"]["isolation_forest"],
        "teste": metricas(y[te], detector.pontuacoes(X[te])["anomalia_bruta"], detector.limiar_anomalia)}

    novo = detector.avaliar(X[te], cortes)
    anterior = atual.detector.avaliar(X[te], cortes)  # a versão publicada, nas mesmas transações
    tipos = todos.loc[te, "tipo_fraude"].to_numpy()

    def recall_tipo(alerta):
        return {t: float(alerta[tipos == t].mean()) for t in TIPOS_SIMULACAO if (tipos == t).any()}
    alerta_novo = novo["nivel"].to_numpy() != "Normal"
    alerta_anterior = anterior["nivel"].to_numpy() != "Normal"
    alerta_c = novo["alerta_classificador"].to_numpy()
    alerta_a = novo["alerta_anomalia"].to_numpy()

    importancia = permutation_importance(detector.classificadores[detector.escolhido], X.loc[val, FEATURES], y[val],
                                         scoring="average_precision", n_repeats=3, random_state=42)
    ordem = np.argsort(importancia.importances_mean)[::-1]
    periodo = lambda m: {"inicio": str(todos.loc[m, "data_hora"].min().date()),
                         "fim": str(todos.loc[m, "data_hora"].max().date()),
                         "transacoes": int(m.sum()), "fraudes": int(y[m].sum())}
    f1_val = {n: modelos[n]["validacao"]["f1"] for n in ORDEM_SIMPLICIDADE}
    motivo = _motivo(f1_val, detector.escolhido)
    comparacao = {"publicada": atual.versao, "anterior": _resumo_alertas(y[te], anterior),
                  "nova": _resumo_alertas(y[te], novo),
                  "recall_por_tipo": {"anterior": recall_tipo(alerta_anterior), "nova": recall_tipo(alerta_novo)}}
    resultados = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "retreino": True,
        "dados": {"transacoes": int(len(todos)), "clientes": int(todos["cliente"].nunique()), "fraudes": int(y.sum()),
                  "api": int(len(api)), "rotulos_analista": int((api["fonte_rotulo"] == "analista").sum()),
                  "rotulos_gerador": int((api["fonte_rotulo"] == "gerador").sum()),
                  "corrigidos_pelo_analista": int(api["corrigido"].sum())},
        "periodos": {"treino": periodo(tr), "validacao": periodo(val), "teste": periodo(te)},
        "modelos": modelos,
        "escolhido": detector.escolhido,
        "motivo_escolha": motivo,
        "matriz_confusao": confusion_matrix(y[te], alerta_c, labels=[0, 1]).tolist(),
        "recall_por_tipo": {"escolhido": recall_tipo(alerta_c), "isolation_forest": recall_tipo(alerta_a)},
        "ganho_isolation": int((alerta_a & ~alerta_c & (y[te] == 1)).sum()),
        "importancia": [{"feature": FEATURES[i], "valor": float(importancia.importances_mean[i])} for i in ordem],
        "comparacao": comparacao,
    }
    teste_avaliado = montar_resultado(todos[te], X[te], novo, todos[~te])
    teste_avaliado["fraude"] = y[te]
    pasta = Path(pasta)
    try:
        import treinar as script_treino  # os mesmos gráficos do treino inicial
        script_treino._graficos(pasta / "graficos", resultados)
    except Exception:  # os gráficos são um extra: sem eles a versão continua válida
        registo.exception("Não foi possível desenhar os gráficos da nova versão")
    novos = Artefatos(detector, todos, teste_avaliado, resultados, pasta)
    gravar(pasta, novos)
    metricas_versao = {"classificador": detector.escolhido, "teste_alertas": comparacao["nova"],
                       "comparacao": comparacao, "dados": resultados["dados"], "periodos": resultados["periodos"]}
    return novos, metricas_versao


def _motivo(f1: dict[str, float], escolhido: str) -> str:
    texto = lambda v: f"{v:.3f}".replace(".", ",")
    melhor = max(f1, key=f1.get)
    if melhor == escolhido:
        return f"Teve o maior F1 na validação ({texto(f1[melhor])})."
    return (f"Ficou a menos de 0,02 do maior F1 na validação ({NOMES[melhor]}, {texto(f1[melhor])}) e é mais simples, "
            "por isso foi o escolhido.")


class Retreino:
    """Corre um re-treino de cada vez, numa thread, e regista o resultado como versão candidata."""

    def __init__(self, sessoes: sessionmaker, pasta_modelos: Path, obter_atual: Callable, obter_cortes: Callable):
        self.sessoes, self.pasta_modelos = sessoes, Path(pasta_modelos)
        self.obter_atual, self.obter_cortes = obter_atual, obter_cortes
        self._thread: threading.Thread | None = None

    def pedir(self, por: str) -> int:
        atual = self.obter_atual()
        if atual is None:
            raise ValueError("Não há uma versão publicada carregada para servir de ponto de partida.")
        versao = versoes.criar_em_treino(self.sessoes, por)
        self._thread = threading.Thread(target=self._correr, args=(versao.numero, versao.pasta, atual),
                                        name=f"retreino-v{versao.numero}", daemon=True)
        self._thread.start()
        registo.info("Re-treino da versão %d pedido por %s", versao.numero, por)
        return versao.numero

    def esperar(self) -> None:
        if self._thread is not None:
            self._thread.join()

    def _correr(self, numero: int, pasta: str, atual: Artefatos) -> None:
        inicio = datetime.now()
        try:
            novos, metricas_versao = treinar_nova_versao(atual, self.sessoes, self.pasta_modelos / pasta,
                                                         self.obter_cortes())
            metricas_versao["segundos"] = round((datetime.now() - inicio).total_seconds(), 1)
            versoes.concluir(self.sessoes, numero, novos.detector.escolhido, metricas_versao)
            registo.info("Versão %d treinada em %.0f s (%s)", numero, metricas_versao["segundos"],
                         novos.detector.escolhido)
        except ValueError as exc:
            versoes.falhar(self.sessoes, numero, str(exc))
            registo.warning("Re-treino da versão %d não foi feito: %s", numero, exc)
        except Exception as exc:
            registo.exception("Re-treino da versão %d falhou", numero)
            versoes.falhar(self.sessoes, numero, f"Erro inesperado: {exc}")
