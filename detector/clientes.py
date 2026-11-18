"""Fichas de clientes do banco de demonstração: validação, criação (com conta), pesquisa e edição."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from detector.banco import Cliente, ContaBancaria
from detector.gerador import CIDADES

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
    soma = sum(int(d) * p for d, p in zip(reversed(base), range(2, len(base) + 2)))
    return base + str((11 - soma % 11) % 11 % 10)


def _proximo_codigo(s) -> str:
    codigos = s.scalars(select(Cliente.codigo))
    maior = max((int(c[1:]) for c in codigos if c[:1] == "C" and c[1:].isdigit()), default=0)
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


def pesquisar(sessoes, texto: str, limite: int = 50) -> list[Cliente]:
    """Procura por parte do nome, do código ou do número de documento."""
    texto = texto.strip()
    consulta = select(Cliente).order_by(Cliente.codigo).limit(limite)
    if texto:
        padrao = f"%{texto}%"
        consulta = consulta.where(or_(Cliente.nome.ilike(padrao), Cliente.codigo.ilike(padrao),
                                      Cliente.numero_documento.ilike(padrao)))
    with sessoes() as s:
        encontrados = list(s.scalars(consulta))
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
