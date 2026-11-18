"""Base de dados: tabelas (SQLAlchemy), ligação e migrações (Alembic).

Usa SQLite por omissão (um único ficheiro em instancia/), mas só depende do SQLAlchemy:
basta mudar URL_BANCO no .env para usar PostgreSQL no futuro.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint, Index, create_engine, event, text)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

RAIZ = Path(__file__).resolve().parent.parent
PASTA_MIGRACOES = RAIZ / "migracoes"


class Base(DeclarativeBase):
    pass


PERFIS = ("balcao", "analista", "supervisor", "administrador")  # o Balcão é à parte: só vê a sua área
AREAS_PERFIL = {
    "balcao": {"balcao"},
    "analista": {"alertas"},
    "supervisor": {"alertas", "supervisao"},
    "administrador": {"balcao", "alertas", "supervisao", "administracao"},
}


class Utilizador(Base):
    """Pessoa que entra no sistema. A senha fica guardada só como hash (scrypt, do werkzeug)."""
    __tablename__ = "utilizadores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome_utilizador: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    nome: Mapped[str] = mapped_column(String(120))
    perfil: Mapped[str] = mapped_column(String(20))  # balcao | analista | supervisor | administrador
    senha_hash: Mapped[str] = mapped_column(String(255))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    trocar_senha: Mapped[bool] = mapped_column(Boolean, default=False)  # depois de o administrador a redefinir
    tentativas_falhadas: Mapped[int] = mapped_column(Integer, default=0)
    bloqueado_ate: Mapped[datetime | None] = mapped_column(DateTime)
    versao_sessao: Mapped[int] = mapped_column(Integer, default=1)  # muda para terminar as sessões abertas
    criado_em: Mapped[datetime] = mapped_column(DateTime)
    ultimo_acesso: Mapped[datetime | None] = mapped_column(DateTime)
    ultimo_alerta_visto: Mapped[int] = mapped_column(Integer, default=0)  # para o sino de notificações

    def pode(self, perfil_minimo: str) -> bool:
        if "balcao" in (self.perfil, perfil_minimo):  # o Balcão não se compara com os perfis do detetor
            return self.perfil == perfil_minimo
        return PERFIS.index(self.perfil) >= PERFIS.index(perfil_minimo)

    def tem_area(self, area: str) -> bool:
        return area in AREAS_PERFIL.get(self.perfil, ())


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
    origem: Mapped[str] = mapped_column(String(20))  # ficheiro | formulario | exemplo | api
    nome_ficheiro: Mapped[str | None] = mapped_column(String(255))
    criada_em: Mapped[datetime] = mapped_column(DateTime, index=True)
    total: Mapped[int] = mapped_column(Integer)
    alertas: Mapped[int] = mapped_column(Integer)
    modelo: Mapped[str] = mapped_column(String(50))  # classificador escolhido no treino
    utilizador_id: Mapped[int | None] = mapped_column(ForeignKey("utilizadores.id", ondelete="SET NULL"),
                                                      index=True)  # quem pediu; vazio nas análises da Fase 1

    chave_api_id: Mapped[int | None] = mapped_column(ForeignKey("chaves_api.id", ondelete="SET NULL"),
                                                     index=True)  # nas transações recebidas pela API
    versao_modelo: Mapped[int | None] = mapped_column(Integer, index=True)  # número da versão que avaliou

    utilizador: Mapped[Utilizador | None] = relationship(lazy="joined")

    transacoes: Mapped[list[TransacaoAvaliada]] = relationship(
        back_populates="analise", cascade="all, delete-orphan", order_by="TransacaoAvaliada.ordem")


class TransacaoAvaliada(Base):
    """Uma transação enviada e o resultado da avaliação, pela ordem mostrada na interface."""
    __tablename__ = "transacoes_avaliadas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analise_id: Mapped[int] = mapped_column(ForeignKey("analises.id", ondelete="CASCADE"), index=True)
    ordem: Mapped[int] = mapped_column(Integer)
    id_transacao: Mapped[str] = mapped_column(String(100), index=True)
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
    # Só nas transações do simulador: o que o gerador marcou. Nunca aparece aos analistas (enviesaria a decisão);
    # serve para medir o desempenho e, na Fase 5, para comparar com as decisões.
    fraude_simulada: Mapped[bool | None] = mapped_column(Boolean)
    tipo_fraude_simulada: Mapped[str | None] = mapped_column(String(30))

    analise: Mapped[Analise] = relationship(back_populates="transacoes")


def criar_motor(url: str, chaves_estrangeiras: bool = True) -> Engine:
    """Com chaves_estrangeiras=False (só nas migrações): no SQLite, alterar uma tabela é recriá-la, e apagar a
    antiga com as chaves ligadas dispararia os ON DELETE CASCADE / SET NULL das outras tabelas."""
    if url.startswith("sqlite"):
        caminho = url.split("///", 1)[1] if "///" in url else ""
        if caminho and caminho != ":memory:":
            Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        motor = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(motor, "connect")
        def _pragmas(ligacao, _):
            cursor = ligacao.cursor()
            cursor.execute(f"PRAGMA foreign_keys={'ON' if chaves_estrangeiras else 'OFF'}")
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


ESTADOS_ALERTA = ("novo", "em_analise", "fraude", "falso_alarme", "recusado_auto")
ESTADOS_ABERTOS = ("novo", "em_analise")


class Alerta(Base):
    """Uma transação com alerta (alto risco ou atenção), à espera de ser investigada e decidida."""
    __tablename__ = "alertas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transacao_id: Mapped[int] = mapped_column(ForeignKey("transacoes_avaliadas.id", ondelete="CASCADE"),
                                              unique=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, index=True)
    estado: Mapped[str] = mapped_column(String(20), index=True)  # novo | em_analise | fraude | falso_alarme
    atribuido_a: Mapped[int | None] = mapped_column(ForeignKey("utilizadores.id", ondelete="SET NULL"), index=True)
    atribuido_em: Mapped[datetime | None] = mapped_column(DateTime)
    decidido_por: Mapped[int | None] = mapped_column(ForeignKey("utilizadores.id", ondelete="SET NULL"))
    decidido_em: Mapped[datetime | None] = mapped_column(DateTime, index=True)

    transacao: Mapped[TransacaoAvaliada] = relationship(lazy="joined")
    responsavel: Mapped[Utilizador | None] = relationship(foreign_keys=[atribuido_a], lazy="joined")
    decisor: Mapped[Utilizador | None] = relationship(foreign_keys=[decidido_por], lazy="joined")


class EventoAlerta(Base):
    """Histórico de um alerta: atribuições, mudanças de estado e comentários, pela ordem em que aconteceram."""
    __tablename__ = "eventos_alerta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alerta_id: Mapped[int] = mapped_column(ForeignKey("alertas.id", ondelete="CASCADE"), index=True)
    momento: Mapped[datetime] = mapped_column(DateTime)
    utilizador_id: Mapped[int | None] = mapped_column(ForeignKey("utilizadores.id", ondelete="SET NULL"))
    nome_utilizador: Mapped[str] = mapped_column(String(50))
    tipo: Mapped[str] = mapped_column(String(20))  # atribuicao | estado | comentario
    de: Mapped[str | None] = mapped_column(String(50))
    para: Mapped[str | None] = mapped_column(String(50))
    texto: Mapped[str | None] = mapped_column(Text)


class ChaveApi(Base):
    """Chave de acesso à API. Só se guarda o SHA-256: a chave completa aparece uma única vez, ao ser criada."""
    __tablename__ = "chaves_api"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(80))
    prefixo: Mapped[str] = mapped_column(String(16), index=True)  # início da chave, para a reconhecer
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    limite_por_minuto: Mapped[int] = mapped_column(Integer)
    ativa: Mapped[bool] = mapped_column(Boolean, default=True)
    criada_em: Mapped[datetime] = mapped_column(DateTime)
    criada_por: Mapped[str] = mapped_column(String(50))
    ultimo_uso: Mapped[datetime | None] = mapped_column(DateTime)
    pedidos: Mapped[int] = mapped_column(Integer, default=0)
    transacoes: Mapped[int] = mapped_column(Integer, default=0)


ESTADOS_TAREFA = ("pendente", "a_processar", "concluida", "falhou")


class Tarefa(Base):
    """Um ficheiro grande analisado em segundo plano."""
    __tablename__ = "tarefas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chave: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    estado: Mapped[str] = mapped_column(String(20), index=True)
    nome_ficheiro: Mapped[str] = mapped_column(String(255))
    linhas: Mapped[int] = mapped_column(Integer)
    utilizador_id: Mapped[int | None] = mapped_column(ForeignKey("utilizadores.id", ondelete="SET NULL"), index=True)
    criada_em: Mapped[datetime] = mapped_column(DateTime)
    iniciada_em: Mapped[datetime | None] = mapped_column(DateTime)
    concluida_em: Mapped[datetime | None] = mapped_column(DateTime)
    analise_chave: Mapped[str | None] = mapped_column(String(32))
    erro: Mapped[str | None] = mapped_column(Text)


ESTADOS_VERSAO = ("a_treinar", "candidata", "publicada", "anterior", "falhou")


class VersaoModelo(Base):
    """Uma versão dos modelos: a pasta com os ficheiros assinados e as métricas com que foi aprovada."""
    __tablename__ = "versoes_modelo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    numero: Mapped[int] = mapped_column(Integer, unique=True)
    estado: Mapped[str] = mapped_column(String(20), index=True)
    origem: Mapped[str] = mapped_column(String(20))  # inicial | retreino
    pasta: Mapped[str] = mapped_column(String(255))  # relativa a PASTA_MODELOS
    classificador: Mapped[str | None] = mapped_column(String(50))
    criada_em: Mapped[datetime] = mapped_column(DateTime)
    criada_por: Mapped[str] = mapped_column(String(50))
    publicada_em: Mapped[datetime | None] = mapped_column(DateTime)
    publicada_por: Mapped[str | None] = mapped_column(String(50))
    metricas: Mapped[str | None] = mapped_column(Text)  # JSON: desempenho no teste, comparação, dados usados
    erro: Mapped[str | None] = mapped_column(Text)


class Ajuste(Base):
    """Configurações mudadas na interface (por exemplo, os limiares de alerta), com quem as mudou."""
    __tablename__ = "ajustes"

    chave: Mapped[str] = mapped_column(String(50), primary_key=True)
    valor: Mapped[str] = mapped_column(Text)
    alterado_em: Mapped[datetime] = mapped_column(DateTime)
    alterado_por: Mapped[str] = mapped_column(String(50))


TIPOS_MOVIMENTO = ("deposito", "levantamento", "compra", "transferencia_enviada", "transferencia_recebida")
ESTADOS_MOVIMENTO = ("autorizado", "recusado", "retido", "cancelado")
CANAIS_MOVIMENTO = ("balcao", "cliente", "api", "simulador")


class Cliente(Base):
    """Cliente do banco de demonstração. Não confundir com as contas da equipa (Utilizador)."""
    __tablename__ = "clientes"
    __table_args__ = (UniqueConstraint("tipo_documento", "numero_documento", name="uq_clientes_documento"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(10), unique=True)  # C0001
    nome: Mapped[str] = mapped_column(String(120))
    data_nascimento: Mapped[date] = mapped_column(Date)
    tipo_documento: Mapped[str] = mapped_column(String(4))  # BI | NUIT
    numero_documento: Mapped[str] = mapped_column(String(20))
    telefone: Mapped[str] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(120))
    cidade: Mapped[str] = mapped_column(String(100))
    morada: Mapped[str] = mapped_column(String(255))
    sintetico: Mapped[bool] = mapped_column(Boolean, default=False)  # criado pelo gerador de dados de teste
    criado_em: Mapped[datetime] = mapped_column(DateTime)
    criado_por: Mapped[str] = mapped_column(String(50))

    conta: Mapped[ContaBancaria | None] = relationship(back_populates="cliente", uselist=False)


class ContaBancaria(Base):
    """Conta de um cliente. Os valores são cêntimos inteiros; o retido espera a decisão da equipa."""
    __tablename__ = "contas_bancarias"
    __table_args__ = (CheckConstraint("saldo_retido >= 0", name="ck_contas_saldo_retido"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    numero: Mapped[str] = mapped_column(String(20), unique=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id", ondelete="CASCADE"), unique=True)
    saldo: Mapped[int] = mapped_column(Integer, default=0)
    saldo_retido: Mapped[int] = mapped_column(Integer, default=0)
    estado: Mapped[str] = mapped_column(String(10), default="ativa")  # ativa | bloqueada
    aberta_em: Mapped[datetime] = mapped_column(DateTime)

    cliente: Mapped[Cliente] = relationship(back_populates="conta")

    @property
    def disponivel(self) -> int:
        return self.saldo - self.saldo_retido


class Movimento(Base):
    """Uma operação pedida a uma conta e o que foi decidido (autorizada, recusada, retida ou cancelada)."""
    __tablename__ = "movimentos"
    __table_args__ = (CheckConstraint("valor > 0", name="ck_movimentos_valor"),
                      Index("ux_movimentos_conta_id_transacao", "conta_id", "id_transacao", unique=True,
                            sqlite_where=text("id_transacao IS NOT NULL")))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conta_id: Mapped[int] = mapped_column(ForeignKey("contas_bancarias.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(30))
    valor: Mapped[int] = mapped_column(Integer)  # cêntimos, sempre positivo
    estado: Mapped[str] = mapped_column(String(12))
    motivo: Mapped[str | None] = mapped_column(String(255))
    contraparte_id: Mapped[int | None] = mapped_column(ForeignKey("contas_bancarias.id"))
    canal: Mapped[str] = mapped_column(String(12))
    momento: Mapped[datetime] = mapped_column(DateTime)  # tempo da transação
    registado_em: Mapped[datetime] = mapped_column(DateTime)  # hora real do registo
    transacao_id: Mapped[int | None] = mapped_column(ForeignKey("transacoes_avaliadas.id", ondelete="SET NULL"),
                                                     index=True)
    id_transacao: Mapped[str | None] = mapped_column(String(100), index=True)
    descricao: Mapped[str | None] = mapped_column(String(140))
    saldo_apos: Mapped[int | None] = mapped_column(Integer)
    criado_por: Mapped[str] = mapped_column(String(50))


class AcessoCliente(Base):
    """Credenciais de um cliente na área do cliente (mesma lógica das contas da equipa)."""
    __tablename__ = "acessos_cliente"

    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id", ondelete="CASCADE"), primary_key=True)
    senha_hash: Mapped[str] = mapped_column(String(255))
    trocar_senha: Mapped[bool] = mapped_column(Boolean, default=False)
    tentativas_falhadas: Mapped[int] = mapped_column(Integer, default=0)
    bloqueado_ate: Mapped[datetime | None] = mapped_column(DateTime)
    versao_sessao: Mapped[int] = mapped_column(Integer, default=1)
    ultimo_acesso: Mapped[datetime | None] = mapped_column(DateTime)