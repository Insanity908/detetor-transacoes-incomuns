"""Configuração lida das variáveis de ambiente e do ficheiro `.env` na raiz do projeto.

As variáveis já definidas no ambiente têm prioridade sobre o `.env` (útil nos testes).
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
AMBIENTES = ("desenvolvimento", "teste", "producao")
NIVEIS_LOG = ("DEBUG", "INFO", "WARNING", "ERROR")


@dataclass(frozen=True)
class Configuracao:
    ambiente: str
    chave_secreta: str
    url_banco: str
    pasta_modelos: Path
    pasta_logs: Path
    nivel_log: str
    host: str
    porta: int
    threads: int
    sessao_horas: int = 8

    @property
    def producao(self) -> bool:
        return self.ambiente == "producao"


def _caminho(valor: str) -> Path:
    caminho = Path(valor)
    return caminho if caminho.is_absolute() else RAIZ / caminho


def _inteiro(nome: str, padrao: int, minimo: int, maximo: int) -> int:
    texto = os.environ.get(nome, str(padrao)).strip()
    try:
        valor = int(texto)
    except ValueError:
        raise ValueError(f"{nome} tem de ser um número inteiro (recebido: '{texto}')") from None
    if not minimo <= valor <= maximo:
        raise ValueError(f"{nome} tem de estar entre {minimo} e {maximo} (recebido: {valor})")
    return valor


def _chave_local() -> str:
    """Fora de produção, sem CHAVE_SECRETA: uma chave gerada uma vez e guardada em instancia/.

    Tem de ser estável entre arranques, porque também assina os modelos treinados.
    """
    ficheiro = RAIZ / "instancia" / "chave_local.txt"
    if ficheiro.exists():
        chave = ficheiro.read_text(encoding="utf-8").strip()
        if len(chave) >= 32:
            return chave
    ficheiro.parent.mkdir(parents=True, exist_ok=True)
    chave = secrets.token_hex(32)
    ficheiro.write_text(chave + "\n", encoding="utf-8")
    return chave


def ler_configuracao(ficheiro_env: Path | None = RAIZ / ".env") -> Configuracao:
    if ficheiro_env is not None and Path(ficheiro_env).exists():
        load_dotenv(ficheiro_env, override=False, encoding="utf-8")

    ambiente = os.environ.get("AMBIENTE", "desenvolvimento").strip().lower()
    if ambiente not in AMBIENTES:
        raise ValueError(f"AMBIENTE tem de ser um de: {', '.join(AMBIENTES)} (recebido: '{ambiente}')")

    chave = os.environ.get("CHAVE_SECRETA", "").strip()
    if not chave:
        if ambiente == "producao":
            raise ValueError("Defina CHAVE_SECRETA no .env antes de iniciar em produção (o instalar.bat cria uma)")
        chave = _chave_local()
    elif len(chave) < 32:
        raise ValueError("CHAVE_SECRETA tem de ter pelo menos 32 caracteres (o instalar.bat gera uma com 64)")

    nivel = os.environ.get("NIVEL_LOG", "INFO").strip().upper()
    if nivel not in NIVEIS_LOG:
        raise ValueError(f"NIVEL_LOG tem de ser um de: {', '.join(NIVEIS_LOG)} (recebido: '{nivel}')")

    url = os.environ.get("URL_BANCO", "").strip()
    if not url:
        url = "sqlite:///" + (RAIZ / "instancia" / "detetor.db").as_posix()

    return Configuracao(
        ambiente=ambiente,
        chave_secreta=chave,
        url_banco=url,
        pasta_modelos=_caminho(os.environ.get("PASTA_MODELOS", "modelos")),
        pasta_logs=_caminho(os.environ.get("PASTA_LOGS", "logs")),
        nivel_log=nivel,
        host=os.environ.get("HOST", "127.0.0.1").strip(),
        porta=_inteiro("PORTA", 5000, 1, 65535),
        threads=_inteiro("THREADS", 4, 1, 64),
        sessao_horas=_inteiro("SESSAO_HORAS", 8, 1, 24 * 7),
    )
