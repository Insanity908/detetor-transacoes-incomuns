"""Motivos em linguagem simples: porque é que uma transação parece estranha para aquele cliente."""
from __future__ import annotations

from .gerador import PAIS_HABITUAL

NOMES_CATEGORIA = {
    "MERCEARIA": "mercearia", "COMBUSTIVEL": "combustível", "RESTAURANTE": "restaurante", "FARMACIA": "farmácia",
    "RECARGA_TELEMOVEL": "recarga de telemóvel", "TRANSPORTE": "transporte", "VESTUARIO": "vestuário",
    "ELETRONICA": "eletrónica", "VIAGEM": "viagem",
    "TRANSFERENCIA": "transferência", "LEVANTAMENTO": "levantamento",
}
LIMIAR_VALOR, LIMIAR_HORA, LIMIAR_RAJADA = 3, 0.9, 3


def formatar_decimal(x: float, casas: int = 1) -> str:
    """Vírgula decimal e ponto nos milhares: 1234.5 -> "1.234,5"."""
    texto = f"{x:,.{casas}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_mt(valor: float) -> str:
    inteiro = abs(valor - round(valor)) < 0.005
    return f"{formatar_decimal(valor, 0 if inteiro else 2)} MT"


def nome_categoria(categoria: str) -> str:
    return NOMES_CATEGORIA.get(categoria, categoria.lower().replace("_", " "))


def motivos(transacao: dict, features: dict, perfil: dict | None) -> list[str]:
    lista = []
    if features["valor_vs_habitual"] >= LIMIAR_VALOR:
        texto = f"Valor {formatar_decimal(features['valor_vs_habitual'])} vezes o habitual deste cliente"
        if perfil:
            texto += f" (habitual: {formatar_mt(perfil['valor_mediana'])})"
        lista.append(texto)
    if features["pais_novo"]:
        nunca_saiu = perfil is None or set(perfil["paises"]) <= {PAIS_HABITUAL}
        if transacao["pais"] != PAIS_HABITUAL and nunca_saiu:
            lista.append(f"Primeira compra deste cliente fora de Moçambique ({transacao['pais']})")
        else:
            lista.append(f"Primeira compra deste cliente em {transacao['pais']}")
    if features["cidade_nova"]:
        lista.append(f"Primeira compra deste cliente em {transacao['cidade'].title()}")
    if features["categoria_nova"]:
        lista.append(f"Primeira compra deste cliente na categoria {nome_categoria(transacao['categoria'])}")
    if features["hora_incomum"] >= LIMIAR_HORA:
        lista.append(f"Compra às {int(features['hora'])}h; este cliente quase nunca compra a esta hora")
    if features["compras_ultima_hora"] >= LIMIAR_RAJADA:
        texto = f"{int(features['compras_ultima_hora']) + 1} compras na última hora"
        if perfil:
            texto += f"; o normal deste cliente é {formatar_decimal(perfil['compras_por_dia'])} por dia"
        lista.append(texto)
    if features["historico_curto"]:
        lista.append("Cliente com pouco histórico: a avaliação é menos fiável")
    return lista
