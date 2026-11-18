"""Conversão de valores em metical para cêntimos inteiros (nunca float nos saldos)."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

MAXIMO_CENTAVOS = 10_000_000_000  # 100 milhões MT


def para_centavos(texto: str | int | float) -> int:
    """Converte "1 234,50", "1.234,50", "1234.5" ou um número em cêntimos; recusa o que não for um valor válido."""
    if isinstance(texto, bool):
        raise ValueError("Valor inválido.")
    if isinstance(texto, (int, float)):
        bruto = str(texto)
    else:
        bruto = re.sub(r"\s+", "", str(texto))
    if not bruto:
        raise ValueError("Indique um valor.")
    if "," in bruto:
        bruto = bruto.replace(".", "").replace(",", ".")  # vírgula decimal, ponto de milhares
    elif bruto.count(".") > 1:
        bruto = bruto.replace(".", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", bruto):
        raise ValueError("Valor inválido: use apenas algarismos, por exemplo 1 234,50.")
    try:
        valor = Decimal(bruto)
    except InvalidOperation:
        raise ValueError("Valor inválido.") from None
    if valor <= 0:
        raise ValueError("O valor tem de ser maior que zero.")
    centavos = valor * 100
    if centavos != centavos.to_integral_value():
        raise ValueError("O valor não pode ter mais de duas casas decimais.")
    centavos = int(centavos)
    if centavos > MAXIMO_CENTAVOS:
        raise ValueError("O valor excede o máximo permitido (100 000 000 MT).")
    return centavos


def de_centavos(c: int) -> float:
    """Cêntimos para metical (só para apresentação ou estatística, nunca para saldos)."""
    return c / 100
