"""Ambiente do Alembic: usa as tabelas de detector/banco.py e o URL_BANCO do .env."""
from __future__ import annotations

import sys
from pathlib import Path

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from detector.banco import Base, criar_motor  # noqa: E402

config = context.config


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from detector.configuracao import ler_configuracao
    return ler_configuracao().url_banco


def executar_offline() -> None:
    context.configure(url=_url(), target_metadata=Base.metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def executar_online() -> None:
    motor = criar_motor(_url(), chaves_estrangeiras=False)  # ver criar_motor: evita apagar em cascata
    with motor.connect() as ligacao:
        context.configure(connection=ligacao, target_metadata=Base.metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    motor.dispose()


if context.is_offline_mode():
    executar_offline()
else:
    executar_online()
