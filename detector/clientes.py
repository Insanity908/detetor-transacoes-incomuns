"""Fichas de clientes do banco de demonstração: validação, criação (com conta), pesquisa e edição."""
from __future__ import annotations

import random
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from detector.banco import Cliente, ContaBancaria
from detector.gerador import CIDADES, N_CLIENTES_TREINO

IDADE_MINIMA = 18
TIPOS_DOCUMENTO = ("BI", "NUIT")


@dataclass
class DadosCliente:
    nome: str
    data_nascimento: date
    tipo_documento: str
    numero_documento: str
    telefone: str
    email: str
    cidade: str
    morada: str


def _agora() -> datetime:
    return datetime.now().replace(microsecond=0)


def _idade(nascimento: date, hoje: date) -> int:
    return hoje.year - nascimento.year - ((hoje.month, hoje.day) < (nascimento.month, nascimento.day))


def _data(texto: str) -> date:
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    raise ValueError("Data de nascimento inválida (use AAAA-MM-DD).")


def validar(form: Mapping[str, str]) -> DadosCliente:
    """Valida os campos de uma ficha; levanta ValueError com a primeira falha, em português."""
    def campo(chave: str) -> str:
        return (form.get(chave) or "").strip()

    nome = " ".join(campo("nome").split())
    if len(nome) < 3:
        raise ValueError("Indique o nome completo do cliente.")
    nascimento = _data(campo("data_nascimento"))
    if _idade(nascimento, date.today()) < IDADE_MINIMA:
        raise ValueError(f"O cliente tem de ter pelo menos {IDADE_MINIMA} anos.")
    tipo = campo("tipo_documento").upper()
    if tipo not in TIPOS_DOCUMENTO:
        raise ValueError("Tipo de documento inválido (BI ou NUIT).")
    numero = campo("numero_documento").replace(" ", "").upper()
    if tipo == "NUIT" and not re.fullmatch(r"\d{9}", numero):
        raise ValueError("O NUIT deve ter 9 dígitos.")
    if tipo == "BI" and not re.fullmatch(r"\d{12}[A-Z]", numero):
        raise ValueError("O BI deve ter 12 dígitos seguidos de uma letra.")
    telefone = campo("telefone").replace(" ", "")
    if not re.fullmatch(r"8\d{8}", telefone):
        raise ValueError("O telefone deve ter 9 dígitos e começar por 8.")
    email = campo("email")
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("E-mail inválido.")
    cidade = campo("cidade").upper()
    if cidade not in CIDADES:
        raise ValueError("Cidade inválida.")
    morada = campo("morada")
    if not morada:
        raise ValueError("Indique a morada.")
    return DadosCliente(nome, nascimento, tipo, numero, telefone, email, cidade, morada)


def _numero_conta(numero_cliente: int) -> str:
    """'0001' + número do cliente com 7 dígitos + dígito de controlo (mod 11; 10 conta como 0)."""
    base = f"0001{numero_cliente:07d}"
    soma = sum(int(d) * p for d, p in zip(reversed(base), range(2, len(base) + 2), strict=True))
    return base + str((11 - soma % 11) % 11 % 10)


def _proximo_codigo(s) -> str:
    codigos = s.scalars(select(Cliente.codigo))
    maior = max((int(c[1:]) for c in codigos if c[:1] == "C" and c[1:].isdigit()), default=0)
    maior = max(maior, N_CLIENTES_TREINO)  # C0001..C0300 são dos clientes do treino, nunca de um cliente novo
    return f"C{maior + 1:04d}"


def _carregar(s, cliente: Cliente | None) -> Cliente | None:
    if cliente is not None:
        _ = cliente.conta
        s.expunge_all()
    return cliente


def criar(sessoes, dados: DadosCliente, por: str, sintetico: bool = False, codigo: str | None = None) -> Cliente:
    """Cria o cliente e a conta (saldo 0) na mesma transação."""
    with sessoes() as s:
        if s.scalar(select(Cliente.id).where(Cliente.tipo_documento == dados.tipo_documento,
                                             Cliente.numero_documento == dados.numero_documento)):
            raise ValueError(f"Já existe um cliente com {dados.tipo_documento} {dados.numero_documento}.")
        codigo = codigo or _proximo_codigo(s)
        agora = _agora()
        cliente = Cliente(codigo=codigo, nome=dados.nome, data_nascimento=dados.data_nascimento,
                          tipo_documento=dados.tipo_documento, numero_documento=dados.numero_documento,
                          telefone=dados.telefone, email=dados.email or None, cidade=dados.cidade,
                          morada=dados.morada, sintetico=sintetico, criado_em=agora, criado_por=por)
        s.add(cliente)
        try:
            s.flush()
            s.add(ContaBancaria(numero=_numero_conta(cliente.id), cliente_id=cliente.id, saldo=0,
                                saldo_retido=0, estado="ativa", aberta_em=agora))
            s.commit()
        except IntegrityError:
            s.rollback()
            raise ValueError("Já existe um cliente com estes dados (documento ou código repetido).") from None
        s.refresh(cliente)
        return _carregar(s, cliente)


def obter(sessoes, cliente_id: int) -> Cliente | None:
    with sessoes() as s:
        return _carregar(s, s.get(Cliente, cliente_id))


def obter_por_codigo(sessoes, codigo: str) -> Cliente | None:
    with sessoes() as s:
        return _carregar(s, s.scalar(select(Cliente).where(Cliente.codigo == codigo.strip().upper())))


def _normalizar(texto: str) -> str:
    """Minúsculas e sem acentos, para comparar nomes."""
    base = unicodedata.normalize("NFD", texto.casefold())
    return "".join(c for c in base if not unicodedata.combining(c))


def pesquisar(sessoes, texto: str, limite: int = 50) -> list[Cliente]:
    """Procura por parte do nome (sem distinguir maiúsculas nem acentos), do código ou do documento."""
    procura = _normalizar(texto.strip())
    with sessoes() as s:
        todos = list(s.scalars(select(Cliente).order_by(Cliente.codigo)))
        encontrados = [c for c in todos if not procura or procura in _normalizar(c.nome)
                       or procura in c.codigo.casefold() or procura in c.numero_documento.casefold()][:limite]
        for c in encontrados:
            _ = c.conta
        s.expunge_all()
        return encontrados


def editar(sessoes, cliente_id: int, telefone: str, email: str, morada: str, cidade: str, por: str) -> list[str]:
    """Atualiza contactos e morada; devolve a lista de mudanças (para a auditoria)."""
    dados = validar({"nome": "Cliente", "data_nascimento": "1990-01-01", "tipo_documento": "NUIT",
                     "numero_documento": "000000000", "telefone": telefone, "email": email,
                     "cidade": cidade, "morada": morada})
    novos = {"telefone": dados.telefone, "email": dados.email or None, "morada": dados.morada,
             "cidade": dados.cidade}
    with sessoes() as s:
        cliente = s.get(Cliente, cliente_id)
        if cliente is None:
            raise ValueError("Cliente não encontrado.")
        mudancas = []
        for nome, novo in novos.items():
            antigo = getattr(cliente, nome)
            if antigo != novo:
                mudancas.append(f"{nome}: {antigo or '-'} -> {novo or '-'}")
                setattr(cliente, nome, novo)
        s.commit()
        return mudancas


NOMES = ("Ana", "Maria", "Joana", "Luísa", "Celeste", "Fátima", "Graça", "Teresa", "Isabel", "Amélia",
         "João", "António", "Carlos", "Manuel", "Alberto", "Armando", "Filipe", "Gilberto", "Samuel", "Eduardo")
APELIDOS = ("Sitoe", "Macamo", "Chissano", "Mondlane", "Machava", "Tembe", "Cossa", "Muianga", "Nhantumbo",
            "Mabunda", "Langa", "Matsinhe", "Bila", "Chongo", "Manjate", "Mucavele", "Zandamela", "Nhaca")
RUAS = ("Av. 24 de Julho", "Av. Eduardo Mondlane", "Rua da Resistência", "Av. Samora Machel", "Rua do Bagamoyo")


def _documento(rng: random.Random, usados: set[tuple[str, str]]) -> tuple[str, str]:
    """Sorteia um NUIT ou BI válido que ainda não foi usado."""
    while True:
        if rng.random() < 0.5:
            tipo, numero = "NUIT", f"{rng.randint(0, 999_999_999):09d}"
        else:
            tipo, numero = "BI", f"{rng.randint(0, 999_999_999_999):012d}" + rng.choice("ABCDEFGHJKLMNPQ")
        if (tipo, numero) not in usados:
            usados.add((tipo, numero))
            return tipo, numero


def criar_sinteticos(sessoes, perfis: list[dict], semente: int = 42) -> int:
    """Cria a ficha, a conta e o saldo inicial dos clientes do gerador (mesmo código, ex. C0001).

    Determinístico a partir da semente e idempotente: os códigos que já existem são saltados."""
    from detector import contas_bancarias
    rng = random.Random(semente)
    documentos: set[tuple[str, str]] = set()
    fichas = []
    for perfil in perfis:  # gera todas as fichas, mesmo as já criadas, para a sequência não depender do que existe
        nome = f"{rng.choice(NOMES)} {rng.choice(APELIDOS)} {rng.choice(APELIDOS)}"
        nascimento = date(rng.randint(1960, 2000), rng.randint(1, 12), rng.randint(1, 28))
        tipo, numero = _documento(rng, documentos)
        telefone = f"8{rng.randint(2, 7)}{rng.randint(0, 9_999_999):07d}"
        morada = f"{rng.choice(RUAS)} {rng.randint(1, 999)}"
        dados = validar(dict(nome=nome, data_nascimento=nascimento.isoformat(), tipo_documento=tipo,
                             numero_documento=numero, telefone=telefone, email="", cidade=perfil["cidade"],
                             morada=morada))
        fichas.append((perfil, dados))
    criados = 0
    for perfil, dados in fichas:
        codigo = perfil["cliente"]
        saldo = round(perfil["tipico"] * perfil["taxa"] * 90 * 100)
        existente = obter_por_codigo(sessoes, codigo)
        if existente is not None:
            if not existente.sintetico:
                raise ValueError(f"O código {codigo} já pertence a um cliente real; não pode ser de um cliente de treino.")
            # reparação: ficha criada mas depósito inicial em falta (falha entre os dois passos)
            if (saldo > 0 and existente.conta.saldo == 0
                    and not contas_bancarias.extrato(sessoes, existente.conta.id, 1)):
                contas_bancarias.depositar(sessoes, existente.conta.id, saldo, "balcao", "sistema", None,
                                           "Saldo inicial")
            continue
        for _ in range(50):  # documento repetido (ficha real ou outro sintético): sorteia outro
            try:
                cliente = criar(sessoes, dados, "sistema", sintetico=True, codigo=codigo)
                break
            except ValueError as exc:
                if "Já existe um cliente com" not in str(exc):
                    raise
                dados.tipo_documento, dados.numero_documento = _documento(rng, documentos)
        else:
            raise ValueError(f"Não foi possível gerar um documento único para {codigo}.")
        if saldo > 0:
            contas_bancarias.depositar(sessoes, cliente.conta.id, saldo, "balcao", "sistema", None, "Saldo inicial")
        criados += 1
    return criados
