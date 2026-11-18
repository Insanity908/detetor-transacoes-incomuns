"""Conversão de valores em metical para cêntimos inteiros (nunca float nos saldos)."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

MAXIMO_CENTAVOS = 10_000_000_000  # 100 milhões MT


def para_centavos(texto: str | int | float) -> int:
    """Converte "1 234,50", "1.234,50", "1.500" (milhares), "1234.5" ou um número em cêntimos; recusa o que não for um valor válido."""
    if isinstance(texto, bool):
        raise ValueError("Valor inválido.")
    if isinstance(texto, (int, float)):
        bruto = str(texto)
    else:
        bruto = str(texto).strip()
        if re.search(r"[0-9]\s+[0-9]", bruto):  # espaços como separador de milhares: grupos de 3
            inteira = re.split(r"[,.]", bruto, maxsplit=1)[0] if "," in bruto else bruto
            if not re.fullmatch(r"[0-9]{1,3}(\s[0-9]{3})+", inteira.strip()):
                raise ValueError("Valor inválido: use apenas algarismos, por exemplo 1 234,50.")
        bruto = re.sub(r"\s+", "", bruto)
    if not bruto:
        raise ValueError("Indique um valor.")
    if "," in bruto:
        bruto = bruto.replace(".", "").replace(",", ".")  # vírgula decimal, ponto de milhares
    elif bruto.count(".") > 1 or re.fullmatch(r"[1-9][0-9]{0,2}\.[0-9]{3}", bruto):
        # vários pontos, ou um só ponto seguido de três algarismos ("1.500"): separador de milhares
        if not re.fullmatch(r"-?[0-9]{1,3}(\.[0-9]{3})+", bruto):
            raise ValueError("Valor inválido: use apenas algarismos, por exemplo 1 234,50.")
        bruto = bruto.replace(".", "")
    if not re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", bruto):
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
