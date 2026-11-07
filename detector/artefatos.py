"""Gravar e carregar os modelos treinados (exportação com joblib) e os resultados."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import joblib
import pandas as pd

from .modelos import Detector

FICHEIROS = ("detector.joblib", "historico.joblib", "teste_avaliado.joblib", "resultados.json")


@dataclass
class Artefatos:
    detector: Detector
    historico: pd.DataFrame
    teste_avaliado: pd.DataFrame
    resultados: dict
    pasta: Path


def gravar(pasta: Path, artefatos: Artefatos) -> None:
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    joblib.dump(artefatos.detector, pasta / "detector.joblib")
    joblib.dump(artefatos.historico, pasta / "historico.joblib")
    joblib.dump(artefatos.teste_avaliado, pasta / "teste_avaliado.joblib")
    (pasta / "resultados.json").write_text(json.dumps(artefatos.resultados, ensure_ascii=False, indent=2),
                                           encoding="utf-8")


def carregar(pasta: Path) -> Artefatos | None:
    """None se faltar algum ficheiro ou se algum não puder ser lido: a interface pede para treinar de novo."""
    pasta = Path(pasta)
    if not all((pasta / f).exists() for f in FICHEIROS):
        return None
    try:  # joblib usa pickle: carregue apenas modelos gerados por você mesmo
        detector = joblib.load(pasta / "detector.joblib")
        historico = joblib.load(pasta / "historico.joblib")
        teste = joblib.load(pasta / "teste_avaliado.joblib")
        resultados = json.loads((pasta / "resultados.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(detector, Detector) or not isinstance(historico, pd.DataFrame):
        return None
    return Artefatos(detector, historico, teste, resultados, pasta)
