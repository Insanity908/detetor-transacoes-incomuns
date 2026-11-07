"""Exemplos prontos para a página de teste, construídos a partir de um cliente real dos dados."""
from __future__ import annotations

import pandas as pd

from .gerador import CATEGORIAS, CIDADES, PAIS_HABITUAL
from .perfis import perfil_cliente

NOMES = ("normal", "valor_alto", "pais_estranho", "madrugada", "conta_tomada", "rajada")
DESCRICOES = {
    "normal": "Compra normal",
    "valor_alto": "Valor alto",
    "pais_estranho": "País estranho",
    "madrugada": "Madrugada",
    "conta_tomada": "Conta tomada",
    "rajada": "Rajada (5 compras)",
}


def criar_exemplos(historico: pd.DataFrame) -> dict[str, list[dict]]:
    """Usa o cliente legítimo com mais compras, um dia depois do fim do histórico, na hora de pico dele."""
    legitimas = historico[historico["fraude"] == 0] if "fraude" in historico.columns else historico
    cliente = legitimas["cliente"].value_counts().idxmax()
    perfil = perfil_cliente(historico, cliente)
    pico = max(range(24), key=lambda h: perfil["horas"][h])
    dia = pd.Timestamp(historico["data_hora"].max()).normalize() + pd.Timedelta(days=1)
    quando = dia + pd.Timedelta(hours=pico)
    cidade = next(iter(perfil["cidades"]))
    categoria = next(iter(perfil["categorias"]))
    mediana = perfil["valor_mediana"]
    cidade_nova = next(c for c in CIDADES if c not in perfil["cidades"])
    categoria_nova = next(c for c in CATEGORIAS if c not in perfil["categorias"])

    def transacao(nome: str, i: int = 0, **mudancas) -> dict:
        base = dict(id_transacao=f"EX-{nome}-{i + 1}", cliente=cliente, data_hora=quando, valor=round(mediana, 2),
                    cidade=cidade, pais=PAIS_HABITUAL, categoria=categoria)
        return base | mudancas

    return {
        "normal": [transacao("normal")],
        "valor_alto": [transacao("valor_alto", valor=round(mediana * 10, 2))],
        "pais_estranho": [transacao("pais_estranho", pais="AE")],
        "madrugada": [transacao("madrugada", data_hora=dia + pd.Timedelta(hours=3, minutes=10))],
        "conta_tomada": [transacao("conta_tomada", cidade=cidade_nova, categoria=categoria_nova,
                                   valor=round(mediana * 3, 2))],
        "rajada": [transacao("rajada", i, valor=round(max(20.0, mediana * 0.1), 2),
                             data_hora=quando + pd.Timedelta(minutes=2 * i)) for i in range(5)],
    }
