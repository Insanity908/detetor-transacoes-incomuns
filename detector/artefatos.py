"""Gravar e carregar os modelos treinados (exportação com joblib) e os resultados.

Os ficheiros .joblib usam pickle, que pode executar código ao ser lido. Por isso, ao gravar, guarda-se em
integridade.json o SHA-256 de cada ficheiro, assinado com HMAC e a CHAVE_SECRETA da instalação; ao carregar,
nada é lido sem a assinatura e os SHA-256 conferirem. Quem trocar um ficheiro sem conhecer a chave é detetado.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import joblib
import pandas as pd

from .modelos import Detector

FICHEIROS = ("detector.joblib", "historico.joblib", "teste_avaliado.joblib", "resultados.json")
MANIFESTO = "integridade.json"
registo = logging.getLogger("detetor")


@dataclass
class Artefatos:
    detector: Detector
    historico: pd.DataFrame
    teste_avaliado: pd.DataFrame
    resultados: dict
    pasta: Path


def _chave_padrao() -> str:
    from .configuracao import ler_configuracao
    return ler_configuracao().chave_secreta


def _sha256(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            resumo.update(bloco)
    return resumo.hexdigest()


def _assinar(ficheiros: dict[str, str], chave: str) -> str:
    texto = json.dumps(ficheiros, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(chave.encode("utf-8"), texto, hashlib.sha256).hexdigest()


def assinar(pasta: Path, chave: str | None = None) -> None:
    """Grava o integridade.json com o SHA-256 de cada ficheiro e a assinatura HMAC."""
    pasta = Path(pasta)
    ficheiros = {f: _sha256(pasta / f) for f in FICHEIROS}
    manifesto = {"algoritmo": "HMAC-SHA256", "ficheiros": ficheiros,
                 "assinatura": _assinar(ficheiros, chave or _chave_padrao())}
    (pasta / MANIFESTO).write_text(json.dumps(manifesto, indent=2), encoding="utf-8")


def verificar_integridade(pasta: Path, chave: str | None = None) -> str | None:
    """None se tudo confere; senão, o motivo, para mostrar no registo."""
    pasta = Path(pasta)
    try:
        manifesto = json.loads((pasta / MANIFESTO).read_text(encoding="utf-8"))
        ficheiros, assinatura = manifesto["ficheiros"], manifesto["assinatura"]
    except FileNotFoundError:
        return f"falta o {MANIFESTO} (modelos de uma versão antiga: treine de novo)"
    except (ValueError, KeyError, TypeError):
        return f"o {MANIFESTO} está danificado"
    if not isinstance(ficheiros, dict) or set(ficheiros) != set(FICHEIROS) or not isinstance(assinatura, str):
        return f"o {MANIFESTO} não tem a lista de ficheiros esperada"
    if not hmac.compare_digest(_assinar(ficheiros, chave or _chave_padrao()), assinatura):
        return "a assinatura não confere (o manifesto foi alterado ou a CHAVE_SECRETA mudou depois do treino)"
    for nome, esperado in ficheiros.items():
        if not hmac.compare_digest(_sha256(pasta / nome), str(esperado)):
            return f"o ficheiro {nome} foi alterado depois do treino"
    return None


def gravar(pasta: Path, artefatos: Artefatos, chave: str | None = None) -> None:
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    joblib.dump(artefatos.detector, pasta / "detector.joblib")
    joblib.dump(artefatos.historico, pasta / "historico.joblib")
    joblib.dump(artefatos.teste_avaliado, pasta / "teste_avaliado.joblib")
    (pasta / "resultados.json").write_text(json.dumps(artefatos.resultados, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    assinar(pasta, chave)


def carregar(pasta: Path, chave: str | None = None) -> Artefatos | None:
    """None se faltar algum ficheiro, se a integridade não conferir ou se algum não puder ser lido."""
    pasta = Path(pasta)
    if not all((pasta / f).exists() for f in FICHEIROS):
        return None
    problema = verificar_integridade(pasta, chave)
    if problema is not None:
        registo.error("Modelos em %s recusados: %s", pasta, problema)
        return None
    try:  # só chega aqui com a integridade confirmada
        detector = joblib.load(pasta / "detector.joblib")
        historico = joblib.load(pasta / "historico.joblib")
        teste = joblib.load(pasta / "teste_avaliado.joblib")
        resultados = json.loads((pasta / "resultados.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(detector, Detector) or not isinstance(historico, pd.DataFrame):
        return None
    return Artefatos(detector, historico, teste, resultados, pasta)
