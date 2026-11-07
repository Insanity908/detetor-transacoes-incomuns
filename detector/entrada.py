"""Leitura e validação de transações vindas de um ficheiro CSV ou do formulário."""
from __future__ import annotations

import io
from datetime import datetime

import pandas as pd

COLUNAS = ["cliente", "data_hora", "valor", "cidade", "pais", "categoria"]
MAX_BYTES = 5 * 1024 * 1024
FORMATOS_DATA = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                 "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M")


def ler_csv(conteudo: bytes) -> pd.DataFrame:
    """Lê com `,` ou `;`. Com `;` (Excel em português), os valores usam vírgula decimal."""
    if len(conteudo) > MAX_BYTES:
        raise ValueError("O ficheiro passa de 5 MB")
    try:
        texto = conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("O ficheiro não está em UTF-8: guarde-o como CSV UTF-8 e envie de novo") from None
    primeira = texto.split("\n", 1)[0]
    separador = ";" if primeira.count(";") > primeira.count(",") else ","
    try:
        df = pd.read_csv(io.StringIO(texto), sep=separador, dtype=str, keep_default_na=False)
    except pd.errors.ParserError as exc:
        raise ValueError(f"O ficheiro não é um CSV válido: {exc}") from None
    df.columns = [str(c).strip().lower() for c in df.columns]
    faltam = [c for c in COLUNAS if c not in df.columns]
    if faltam:
        raise ValueError(f"Faltam as colunas: {', '.join(faltam)}")
    if df.empty:
        raise ValueError("O ficheiro não tem transações")
    if "id_transacao" not in df.columns or (df["id_transacao"].str.strip() == "").all():
        df["id_transacao"] = [f"L{i + 1}" for i in range(len(df))]
    df.attrs["virgula_decimal"] = separador == ";"
    return df[["id_transacao", *COLUNAS]]


def _data(texto: str) -> datetime | None:
    for formato in FORMATOS_DATA:
        try:
            return datetime.strptime(texto, formato)
        except ValueError:
            continue
    return None


def _valor(texto: str, virgula_decimal: bool) -> float | None:
    if virgula_decimal:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto and "." not in texto:
        texto = texto.replace(",", ".")
    try:
        valor = float(texto)
    except ValueError:
        return None
    return valor if valor > 0 and valor < float("inf") else None


def validar(df: pd.DataFrame) -> pd.DataFrame:
    """Converte e confere cada linha. Os erros indicam a linha do ficheiro (o cabeçalho é a linha 1)."""
    virgula = df.attrs.get("virgula_decimal", False)
    linhas = []
    for i, linha in enumerate(df.astype(str).to_dict("records")):
        n = i + 2
        for coluna in COLUNAS:
            if not linha[coluna].strip():
                raise ValueError(f"Linha {n}: o campo {coluna} está vazio")
        quando = _data(linha["data_hora"].strip())
        if quando is None:
            raise ValueError(f"Linha {n}: data_hora inválida ('{linha['data_hora']}'); "
                             "use aaaa-mm-dd hh:mm ou dd/mm/aaaa hh:mm")
        valor = _valor(linha["valor"].strip(), virgula)
        if valor is None:
            raise ValueError(f"Linha {n}: valor inválido ('{linha['valor']}'); use um número maior que zero")
        linhas.append(linha | {"data_hora": quando, "valor": valor,
                               "id_transacao": linha.get("id_transacao", "").strip() or f"L{i + 1}"})
    resultado = pd.DataFrame(linhas, columns=["id_transacao", *COLUNAS])
    resultado["data_hora"] = pd.to_datetime(resultado["data_hora"])
    return resultado
