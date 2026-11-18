"""Base de dados: tabelas (SQLAlchemy), ligação e migrações (Alembic).

Usa SQLite por omissão (um único ficheiro em instancia/), mas só depende do SQLAlchemy:
basta mudar URL_BANCO no .env para usar PostgreSQL no futuro.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event, text)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

RAIZ = Path(__file__).resolve().parent.parent
PASTA_MIGRACOES = RAIZ / "migracoes"


class Base(DeclarativeBase):
    pass


PERFIS = ("analista", "supervisor", "administrador")  # do menor para o maior acesso


class Utilizador(Base):
    """Pessoa que entra no sistema. A senha fica guardada só como hash (scrypt, do werkzeug)."""
    __tablename__ = "utilizadores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome_utilizador: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    nome: Mapped[str] = mapped_column(String(120))
    perfil: Mapped[str] = mapped_column(String(20))  # analista | supervisor | administrador
    senha_hash: Mapped[str] = mapped_column(String(255))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    trocar_senha: Mapped[bool] = mapped_column(Boolean, default=False)  # depois de o administrador a redefinir
    tentativas_falhadas: Mapped[int] = mapped_column(Integer, default=0)
    bloqueado_ate: Mapped[datetime | None] = mapped_column(DateTime)
    versao_sessao: Mapped[int] = mapped_column(Integer, default=1)  # muda para terminar as sessões abertas
    criado_em: Mapped[datetime] = mapped_column(DateTime)
    ultimo_acesso: Mapped[datetime | None] = mapped_column(DateTime)

    def pode(self, perfil_minimo: str) -> bool:
        return PERFIS.index(self.perfil) >= PERFIS.index(perfil_minimo)


class EventoAuditoria(Base):
    """Uma ação feita no sistema: quem, quando, o quê e sobre o quê. Nunca é alterado nem apagado."""
    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    momento: Mapped[datetime] = mapped_column(DateTime, index=True)
    utilizador_id: Mapped[int | None] = mapped_column(ForeignKey("utilizadores.id", ondelete="SET NULL"), index=True)
    nome_utilizador: Mapped[str] = mapped_column(String(50))  # cópia: o registo fica legível mesmo sem a conta
    acao: Mapped[str] = mapped_column(String(40), index=True)
    alvo: Mapped[str | None] = mapped_column(String(255), index=True)  # análise, cliente ou utilizador afetado
    detalhe: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(45))


class Analise(Base):
    """Uma avaliação pedida na interface: um ficheiro CSV, o formulário ou um exemplo."""
    __tablename__ = "analises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chave: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    origem: Mapped[str] = mapped_column(String(20))  # ficheiro | formulario | exemplo
    nome_ficheiro: Mapped[str | None] = mapped_column(String(255))
    criada_em: Mapped[datetime] = mapped_column(DateTime, index=True)
    total: Mapped[int] = mapped_column(Integer)
    alertas: Mapped[int] = mapped_column(Integer)
    modelo: Mapped[str] = mapped_column(String(50))  # classificador escolhido no treino
    utilizador_id: Mapped[int | None] = mapped_column(ForeignKey("utilizadores.id", ondelete="SET NULL"),
                                                      index=True)  # quem pediu; vazio nas análises da Fase 1

    utilizador: Mapped[Utilizador | None] = relationship(lazy="joined")

    transacoes: Mapped[list[TransacaoAvaliada]] = relationship(
        back_populates="analise", cascade="all, delete-orphan", order_by="TransacaoAvaliada.ordem")


class TransacaoAvaliada(Base):
    """Uma transação enviada e o resultado da avaliação, pela ordem mostrada na interface."""
    __tablename__ = "transacoes_avaliadas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analise_id: Mapped[int] = mapped_column(ForeignKey("analises.id", ondelete="CASCADE"), index=True)
    ordem: Mapped[int] = mapped_column(Integer)
    id_transacao: Mapped[str] = mapped_column(String(100))
    cliente: Mapped[str] = mapped_column(String(100), index=True)
    data_hora: Mapped[datetime] = mapped_column(DateTime)
    valor: Mapped[float] = mapped_column(Float)
    cidade: Mapped[str] = mapped_column(String(100))
    pais: Mapped[str] = mapped_column(String(100))
    categoria: Mapped[str] = mapped_column(String(100))
    risco: Mapped[float] = mapped_column(Float)
    anomalia: Mapped[float] = mapped_column(Float)
    nivel: Mapped[str] = mapped_column(String(20), index=True)
    motivos: Mapped[str] = mapped_column(Text)  # lista em JSON
    historico_curto: Mapped[bool] = mapped_column(Boolean)

    analise: Mapped[Analise] = relationship(back_populates="transacoes")


def criar_motor(url: str) -> Engine:
    if url.startswith("sqlite"):
        caminho = url.split("///", 1)[1] if "///" in url else ""
        if caminho and caminho != ":memory:":
            Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        motor = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(motor, "connect")
        def _pragmas(ligacao, _):
            cursor = ligacao.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")  # leituras não bloqueiam durante uma gravação
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()
        return motor
    return create_engine(url, pool_pre_ping=True)


def criar_fabrica_sessoes(motor: Engine) -> sessionmaker:
    return sessionmaker(bind=motor, expire_on_commit=False)


def configuracao_alembic(url: str):
    from alembic.config import Config
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(PASTA_MIGRACOES))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def migrar(url: str) -> None:
    """Cria ou atualiza as tabelas até à versão mais recente. Pode ser chamado sempre: não repete nada."""
    from alembic import command
    if url.startswith("sqlite") and "///" in url:
        caminho = url.split("///", 1)[1]
        if caminho and caminho != ":memory:":
            Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(configuracao_alembic(url), "head")


def descrever(motor: Engine) -> str:
    """Endereço do banco para mostrar ao utilizador: o caminho do ficheiro no SQLite, sem senha nos outros."""
    if motor.url.get_backend_name() == "sqlite":
        return str(Path(motor.url.database)) if motor.url.database else "SQLite em memória"
    return motor.url.render_as_string(hide_password=True)


def verificar_ligacao(motor: Engine) -> bool:
    try:
        with motor.connect() as ligacao:
            ligacao.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
