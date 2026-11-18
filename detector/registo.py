"""Registo de eventos (logs): consola e ficheiro logs/detetor.log, que roda aos 2 MB e guarda 5 cópias."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

NOME = "detetor"
FORMATO = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_MARCA = "_detetor"  # identifica os handlers criados aqui, para não os duplicar


def configurar_registo(pasta_logs: Path | None, nivel: str = "INFO") -> logging.Logger:
    """Configura o logger 'detetor' (e o 'waitress'). Chamar de novo substitui os handlers anteriores."""
    formato = logging.Formatter(FORMATO, datefmt="%Y-%m-%d %H:%M:%S")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if pasta_logs is not None:
        Path(pasta_logs).mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(Path(pasta_logs) / "detetor.log", maxBytes=2 * 1024 * 1024,
                                            backupCount=5, encoding="utf-8"))
    for nome in (NOME, "waitress"):
        registo = logging.getLogger(nome)
        for antigo in [h for h in registo.handlers if getattr(h, _MARCA, False)]:
            registo.removeHandler(antigo)
            antigo.close()
        for h in handlers:
            h.setFormatter(formato)
            setattr(h, _MARCA, True)
            registo.addHandler(h)
        registo.setLevel(nivel)
        registo.propagate = False
    return logging.getLogger(NOME)
