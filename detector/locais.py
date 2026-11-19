"""Local e data/hora de uma operação na área do cliente (o local e a hora simulados são do modo demonstração)."""
from __future__ import annotations

from datetime import datetime, timedelta

from .gerador import CIDADES, PAIS_HABITUAL, PAISES_ESTRANHOS

NOMES_PAIS = {"MZ": "Moçambique", "NG": "Nigéria", "AE": "Emirados Árabes Unidos", "CN": "China", "BR": "Brasil",
              "IN": "Índia", "ZW": "Zimbabwe", "MW": "Malawi", "TZ": "Tanzânia"}
DIAS_SIMULACAO = 30
ERRO_LOCAL = "Escolha um local da lista."


def opcoes_local() -> list[tuple[str, list[tuple[str, str]]]]:
    """Grupos do campo Local: as cidades de Moçambique e os países estrangeiros."""
    return [("Moçambique", [(f"MZ:{c}", c.capitalize()) for c in CIDADES]),
            ("Estrangeiro", [(f"EXT:{p}", NOMES_PAIS.get(p, p)) for p in PAISES_ESTRANHOS])]


def ler_local(valor: str, cidade_cliente: str) -> tuple[str, str]:
    """(cidade, país). Num país estrangeiro, a cidade fica a do cliente: é assim que o treino vê o "país estranho"."""
    tipo, _, codigo = (valor or "").strip().upper().partition(":")
    if tipo == "MZ" and codigo in CIDADES:
        return codigo, PAIS_HABITUAL
    if tipo == "EXT" and codigo in PAISES_ESTRANHOS:
        return cidade_cliente, codigo
    raise ValueError(ERRO_LOCAL)


def valor_local(cidade: str, pais: str) -> str:
    return f"MZ:{cidade}" if pais == PAIS_HABITUAL else f"EXT:{pais}"


def descrever_local(cidade: str, pais: str) -> str:
    return cidade.capitalize() if pais == PAIS_HABITUAL else NOMES_PAIS.get(pais, pais)


def ler_momento(data: str, hora: str, agora: datetime) -> datetime:
    """Data/hora simulada (AAAA-MM-DD e HH:MM). Vazio = agora. Entre há 30 dias e agora."""
    data, hora = (data or "").strip(), (hora or "").strip()
    if not data and not hora:
        return agora
    data = data or f"{agora:%Y-%m-%d}"
    hora = hora or f"{agora:%H:%M}"
    try:
        quando = datetime.strptime(f"{data} {hora}", "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError("Data ou hora simulada inválida.") from None
    if quando > agora:
        raise ValueError("A data simulada não pode ser futura.")
    if quando < agora - timedelta(days=DIAS_SIMULACAO):
        raise ValueError(f"A data simulada pode ser no máximo {DIAS_SIMULACAO} dias atrás.")
    return quando
