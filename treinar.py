"""Gera os dados, treina e avalia os modelos e grava tudo o que a interface precisa.

Execução, na raiz do projeto:
    python treinar.py
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.inspection import permutation_importance  # noqa: E402
from sklearn.metrics import average_precision_score, confusion_matrix, f1_score  # noqa: E402
from sklearn.model_selection import train_test_split  # noqa: E402

from detector.artefatos import Artefatos, gravar  # noqa: E402
from detector.avaliacao import NOMES_PERIODO, dividir_periodos, melhor_limiar, metricas  # noqa: E402
from detector.explicacao import formatar_decimal  # noqa: E402
from detector.gerador import NOMES_TIPO_FRAUDE, TIPOS_FRAUDE, gerar_transacoes  # noqa: E402
from detector.modelos import MARGEM_EMPATE, NOMES, ORDEM_SIMPLICIDADE, criar_classificadores, treinar  # noqa: E402
from detector.perfis import FEATURES, calcular_features  # noqa: E402
from detector.servico import montar_resultado  # noqa: E402

LIMITE_GERADOR_FACIL = 0.98
AZUL, LARANJA = "#2a78d6", "#eb6834"
VIRGULA = FuncFormatter(lambda v, _: f"{v:g}".replace(".", ","))  # eixos com vírgula decimal
ROTULOS_FEATURES = {
    "valor_vs_habitual": "Valor / habitual", "hora_incomum": "Hora incomum", "pais_novo": "País novo",
    "cidade_nova": "Cidade nova", "categoria_nova": "Categoria nova", "compras_ultima_hora": "Compras na última hora",
    "compras_24h": "Compras em 24 h", "minutos_desde_anterior": "Minutos desde a anterior",
    "historico_curto": "Histórico curto", "valor": "Valor (MT)", "hora": "Hora do dia",
}


def _pct(v) -> str:
    return "—" if v is None else f"{formatar_decimal(100 * v)}%"


def _num(v, casas=3) -> str:
    return "—" if v is None else formatar_decimal(v, casas)


def _int(v: int) -> str:
    return formatar_decimal(v, 0)


def _atalhos(df: pd.DataFrame, treino: np.ndarray, teste: np.ndarray) -> dict[str, float]:
    """PR AUC de cada coluna bruta sozinha no teste: se alguma for alta, o gerador deixou um atalho."""
    y = df["fraude"].to_numpy()
    resultado = {}
    for coluna, serie in (("valor", df["valor"]), ("hora", df["data_hora"].dt.hour)):
        s = serie.to_numpy(float)[teste]
        resultado[coluna] = max(average_precision_score(y[teste], s), average_precision_score(y[teste], -s))
    for coluna in ("pais", "categoria", "cidade"):
        taxa = df[treino].groupby(coluna)["fraude"].mean()
        s = df.loc[teste, coluna].map(taxa).fillna(y[treino].mean()).to_numpy()
        resultado[coluna] = float(average_precision_score(y[teste], s))
    return {k: float(v) for k, v in resultado.items()}


def _divisao_aleatoria(nome: str, X: pd.DataFrame, y: np.ndarray) -> float:
    """O 80/20 aleatório das aulas, com o limiar escolhido numa validação também aleatória."""
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=42)
    X_in, X_val, y_in, y_val = train_test_split(X_tr, y_tr, test_size=0.2, random_state=42)
    modelo = criar_classificadores()[nome].fit(X_in[FEATURES], y_in)
    limiar = melhor_limiar(y_val, modelo.predict_proba(X_val[FEATURES])[:, 1])
    return float(f1_score(y_te, modelo.predict_proba(X_te[FEATURES])[:, 1] >= limiar, zero_division=0))


def _graficos(pasta: Path, res: dict) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    (vn, fp), (fn, vp) = res["matriz_confusao"]
    fig, ax = plt.subplots(figsize=(4.6, 4), dpi=150)
    matriz = np.array([[vn, fp], [fn, vp]])
    ax.imshow(np.log1p(matriz), cmap="Blues")
    for (i, j), v in np.ndenumerate(matriz):
        ax.text(j, i, f"{v:,}".replace(",", "."), ha="center", va="center", fontsize=12,
                color="white" if np.log1p(v) > np.log1p(matriz).max() / 2 else "#1f1f1e")
    ax.set_xticks([0, 1], ["Legítima", "Fraude"])
    ax.set_yticks([0, 1], ["Legítima", "Fraude"])
    ax.set_xlabel("Previsto")
    ax.set_ylabel("Real")
    ax.set_title(f"Matriz de confusão no teste\n{NOMES[res['escolhido']]}", fontsize=11)
    fig.tight_layout()
    fig.savefig(pasta / "matriz_confusao.png")
    plt.close(fig)

    imp = res["importancia"][::-1]
    fig, ax = plt.subplots(figsize=(6, 4.2), dpi=150)
    ax.barh([ROTULOS_FEATURES[i["feature"]] for i in imp], [i["valor"] for i in imp], color=AZUL)
    ax.set_xlabel("Queda da PR AUC ao embaralhar a feature")
    ax.xaxis.set_major_formatter(VIRGULA)
    ax.set_title("Importância das features (validação)", fontsize=11, loc="left")
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    fig.tight_layout()
    fig.savefig(pasta / "importancia.png")
    plt.close(fig)

    nomes = [*ORDEM_SIMPLICIDADE, "isolation_forest"]
    posicoes = np.arange(len(nomes))
    fig, ax = plt.subplots(figsize=(6.4, 3.8), dpi=150)
    for k, (periodo, cor) in enumerate((("validacao", AZUL), ("teste", LARANJA))):
        valores = [res["modelos"][n][periodo]["f1"] for n in nomes]
        ax.bar(posicoes + (k - 0.5) * 0.38, valores, 0.36, color=cor, label="Validação" if k == 0 else "Teste")
    ax.set_xticks(posicoes, [NOMES[n].split(" (")[0] for n in nomes], fontsize=8)
    ax.set_ylim(0, 1)
    ax.set_ylabel("F1 da classe fraude")
    ax.yaxis.set_major_formatter(VIRGULA)
    ax.set_title("F1 por modelo", fontsize=11, loc="left")
    ax.legend(frameon=False)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    fig.tight_layout()
    fig.savefig(pasta / "comparacao.png")
    plt.close(fig)


def _motivo_escolha(f1: dict[str, float], escolhido: str) -> str:
    melhor = max(f1, key=f1.get)
    if escolhido == melhor:
        return f"Maior F1 na validação ({_num(f1[escolhido])})."
    return (f"F1 de {_num(f1[escolhido])} na validação, até {_num(MARGEM_EMPATE, 2)} abaixo do melhor "
            f"({NOMES[melhor]}, {_num(f1[melhor])}): fica o modelo mais simples.")


def _leitura_divisoes(d: dict) -> str:
    if d["f1_aleatoria"] > d["f1_tempo"]:
        return ("Neste conjunto, a divisão aleatória deu um resultado mais otimista do que a divisão por tempo, "
                "que é a forma como o sistema seria usado: treinar com o passado e avaliar o futuro.")
    return ("Neste conjunto, a divisão aleatória não inflou o resultado. As features já usam só o passado de cada "
            "cliente, o que evita o vazamento mais comum; mesmo assim, a divisão por tempo é a que corresponde ao "
            "uso real: treinar com o passado e avaliar o futuro.")


def _relatorio(res: dict, graficos: str) -> str:
    d = res["dados"]
    linhas = [
        "# Relatório do treino", "",
        f"Gerado por `python treinar.py` em {res['gerado_em']}, em {formatar_decimal(res['tempo_segundos'])} s. "
        "Dados sintéticos, gerados para a disciplina; os resultados não valem para fraudes reais.", "",
        "## Dados", "",
        f"{_int(d['transacoes'])} transações de {d['clientes']} clientes, com {_int(d['fraudes'])} fraudes "
        f"({_pct(d['fraudes'] / d['transacoes'])}).", "",
        "| Tipo de fraude | Transações |", "|---|---|",
        *[f"| {NOMES_TIPO_FRAUDE[t]} | {_int(n)} |" for t, n in d["por_tipo"].items()], "",
        "## Períodos", "",
        "A divisão é por tempo: cada período só vem depois do anterior. As features usam só o passado de "
        "cada cliente.", "",
        "| Período | Início | Fim | Transações | Fraudes |", "|---|---|---|---|---|",
        *[f"| {NOMES_PERIODO[n]} | {p['inicio']} | {p['fim']} | {_int(p['transacoes'])} | {_int(p['fraudes'])} |"
          for n, p in res["periodos"].items()], "",
        "## Modelos", "",
        "Métricas da classe fraude. A acurácia engana: com cerca de 1% de fraudes, dizer \"legítima\" para "
        "tudo já dá 99% de acurácia.", "",
        "| Modelo | Período | Precisão | Recall | F1 | PR AUC | Acurácia |", "|---|---|---|---|---|---|---|",
    ]
    for nome, m in res["modelos"].items():
        for periodo in ("validacao", "teste"):
            x = m[periodo]
            linhas.append(f"| {NOMES[nome]} | {'Validação' if periodo == 'validacao' else 'Teste'} "
                          f"| {_pct(x['precisao'])} | {_pct(x['recall'])} | {_num(x['f1'])} "
                          f"| {_num(x['pr_auc'])} | {_pct(x['acuracia'])} |")
    esc = res["escolhido"]
    teste = res["modelos"][esc]["teste"]
    linhas += [
        "", f"![F1 por modelo]({graficos}/comparacao.png)", "",
        "## Modelo escolhido", "",
        f"**{NOMES[esc]}.** {res['motivo_escolha']} A regra foi fixada antes do treino: o limiar e o modelo "
        f"vêm só da validação. No teste, o F1 foi {_num(teste['f1'])}.",
    ]
    if res["aviso_gerador_facil"]:
        linhas += ["", f"**Aviso:** o F1 no teste passou de {_num(LIMITE_GERADOR_FACIL, 2)}. O gerador "
                   "provavelmente ficou fácil demais, e este número não indica desempenho com fraudes reais."]
    (vn, fp), (fn, vp) = res["matriz_confusao"]
    linhas += [
        "", "## Matriz de confusão", "",
        f"No teste: {_int(vp)} fraudes apanhadas, {_int(fn)} deixadas passar, {_int(fp)} alertas falsos e "
        f"{_int(vn)} legítimas aceites.", "",
        f"![Matriz de confusão]({graficos}/matriz_confusao.png)", "",
        "## Recall por tipo de fraude", "",
        f"| Tipo | {NOMES[esc]} | Isolation Forest |", "|---|---|---|",
        *[f"| {NOMES_TIPO_FRAUDE[t]} | {_pct(res['recall_por_tipo']['escolhido'].get(t))} "
          f"| {_pct(res['recall_por_tipo']['isolation_forest'].get(t))} |" for t in TIPOS_FRAUDE],
        "", "## Isolation Forest", "",
        "Treinado sem os rótulos (aprendizado não supervisionado), com o limiar que marca 2% da validação. "
        f"No teste, apanhou {res['ganho_isolation']} fraudes que o classificador deixou passar; essas "
        "transações aparecem com o nível \"Atenção\".", "",
        "## Importância das features", "",
        "Calculada por permutação na validação: quanto a PR AUC cai quando os valores de uma feature são "
        "embaralhados.", "",
        f"![Importância das features]({graficos}/importancia.png)", "",
        "## Divisão aleatória contra divisão por tempo", "",
        "As aulas usam `train_test_split` com 80/20 aleatório. Com transações, isso mistura o futuro e o "
        "passado de cada cliente no treino.", "",
        "| Divisão | F1 no teste |", "|---|---|",
        f"| Por tempo (usada no sistema) | {_num(res['divisao_aleatoria']['f1_tempo'])} |",
        f"| Aleatória 80/20 | {_num(res['divisao_aleatoria']['f1_aleatoria'])} |", "",
        _leitura_divisoes(res["divisao_aleatoria"]), "",
        "## Atalhos", "",
        "PR AUC de cada coluna bruta usada sozinha no teste. Precisa de ficar bem abaixo da do modelo "
        f"({_num(teste['pr_auc'])}); senão, o gerador deixou uma pista fácil.", "",
        "| Coluna | PR AUC |", "|---|---|",
        *[f"| {c} | {_num(v)} |" for c, v in res["atalhos"].items()], "",
        "## Limitações", "",
        "- Os dados são sintéticos e de um único gerador; os resultados não valem automaticamente para "
        "fraudes reais.",
        "- O sistema só conhece os padrões de fraude que o gerador cria.",
        "- Clientes com menos de 5 compras são avaliados com pouca informação.",
        "- O perfil de cada cliente só muda quando o treino é executado de novo.",
    ]
    return "\n".join(linhas) + "\n"


def executar(pasta_modelos: Path = Path("modelos"), pasta_dados: Path = Path("data"),
             relatorio: Path | None = Path("RELATORIO.md"), n_clientes: int = 300, dias: int = 120,
             semente: int = 42) -> dict:
    inicio = time.monotonic()
    pasta_modelos, pasta_dados = Path(pasta_modelos), Path(pasta_dados)
    df = gerar_transacoes(n_clientes, dias, semente)
    pasta_dados.mkdir(parents=True, exist_ok=True)
    df.to_csv(pasta_dados / "transacoes.csv", index=False)

    X = calcular_features(df)
    p = dividir_periodos(df["data_hora"])
    y = df["fraude"].to_numpy()
    tr, val, te = p["treino"], p["validacao"], p["teste"]
    detector, parcial = treinar(X[tr], y[tr], X[val], y[val])

    modelos = {}
    for nome, pipeline in detector.classificadores.items():
        prob = pipeline.predict_proba(X.loc[te, FEATURES])[:, 1]
        modelos[nome] = {"limiar": detector.limiares[nome], "validacao": parcial["validacao"][nome],
                         "teste": metricas(y[te], prob, detector.limiares[nome])}
    avaliado = detector.avaliar(X[te])
    modelos["isolation_forest"] = {
        "limiar": detector.limiar_anomalia, "validacao": parcial["validacao"]["isolation_forest"],
        "teste": metricas(y[te], detector.pontuacoes(X[te])["anomalia_bruta"], detector.limiar_anomalia)}

    alerta_c = avaliado["alerta_classificador"].to_numpy()
    alerta_a = avaliado["alerta_anomalia"].to_numpy()
    tipos = df.loc[te, "tipo_fraude"].to_numpy()
    recall_tipo = lambda alerta: {t: float(alerta[tipos == t].mean()) for t in TIPOS_FRAUDE if (tipos == t).any()}
    importancia = permutation_importance(detector.classificadores[detector.escolhido], X.loc[val, FEATURES], y[val],
                                         scoring="average_precision", n_repeats=5, random_state=42)
    ordem = np.argsort(importancia.importances_mean)[::-1]
    f1_val = {n: modelos[n]["validacao"]["f1"] for n in ORDEM_SIMPLICIDADE}

    res = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "dados": {"transacoes": len(df), "clientes": int(df["cliente"].nunique()), "fraudes": int(y.sum()),
                  "por_tipo": {t: int((df["tipo_fraude"] == t).sum()) for t in TIPOS_FRAUDE}},
        "periodos": {n: {"inicio": str(df.loc[m, "data_hora"].min().date()), "fim": str(df.loc[m, "data_hora"].max().date()),
                         "transacoes": int(m.sum()), "fraudes": int(y[m].sum())} for n, m in p.items()},
        "modelos": modelos,
        "escolhido": detector.escolhido,
        "motivo_escolha": _motivo_escolha(f1_val, detector.escolhido),
        "matriz_confusao": confusion_matrix(y[te], alerta_c, labels=[0, 1]).tolist(),
        "recall_por_tipo": {"escolhido": recall_tipo(alerta_c), "isolation_forest": recall_tipo(alerta_a)},
        "ganho_isolation": int((alerta_a & ~alerta_c & (y[te] == 1)).sum()),
        "importancia": [{"feature": FEATURES[i], "valor": float(importancia.importances_mean[i])} for i in ordem],
        "divisao_aleatoria": {"f1_tempo": modelos[detector.escolhido]["teste"]["f1"],
                              "f1_aleatoria": _divisao_aleatoria(detector.escolhido, X[~p["aquecimento"]],
                                                                 y[~p["aquecimento"]])},
        "aviso_gerador_facil": modelos[detector.escolhido]["teste"]["f1"] > LIMITE_GERADOR_FACIL,
        "atalhos": _atalhos(df, tr, te),
    }
    _graficos(pasta_modelos / "graficos", res)
    teste_avaliado = montar_resultado(df[te], X[te], avaliado, df[~te])  # reaproveita as features já calculadas
    res["tempo_segundos"] = round(time.monotonic() - inicio, 1)
    gravar(pasta_modelos, Artefatos(detector, df, teste_avaliado, res, pasta_modelos))
    if relatorio is not None:
        graficos = Path(os.path.relpath(pasta_modelos / "graficos", Path(relatorio).parent)).as_posix()
        Path(relatorio).write_text(_relatorio(res, graficos), encoding="utf-8")
    return res


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    res = executar()
    teste = res["modelos"][res["escolhido"]]["teste"]
    print(f"Modelo escolhido: {NOMES[res['escolhido']]} — F1 no teste {_num(teste['f1'])}, "
          f"recall {_pct(teste['recall'])}, precisão {_pct(teste['precisao'])}")
    print(f"Tempo total: {formatar_decimal(res['tempo_segundos'])} s. Relatório em RELATORIO.md")


if __name__ == "__main__":
    main()
