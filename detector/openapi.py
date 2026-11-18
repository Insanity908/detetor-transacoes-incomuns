"""Especificação OpenAPI 3.0 da API v1 (servida em /api/v1/openapi.json e mostrada em /api/docs).

Pode ser aberta em qualquer ferramenta compatível (Swagger Editor, Swagger UI, Postman, Insomnia).
"""
from __future__ import annotations

from .gerador import TIPOS_FRAUDE

MAX_LOTE = 500


def _erro(descricao: str) -> dict:
    return {"description": descricao, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Erro"}}}}


def especificacao(servidor: str = "") -> dict:
    transacao = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id_transacao", "cliente", "data_hora", "valor", "cidade", "pais", "categoria"],
        "properties": {
            "id_transacao": {"type": "string", "pattern": "^[A-Za-z0-9._:\\-]{1,100}$",
                             "description": "Identificador único no sistema de origem. Reenviar o mesmo id não "
                                            "avalia de novo: devolve o resultado já gravado.",
                             "example": "BANCO-2026-000123"},
            "cliente": {"type": "string", "maxLength": 100, "example": "C0001"},
            "data_hora": {"type": "string", "format": "date-time", "example": "2026-05-01T14:30:00",
                          "description": "ISO 8601. Sem fuso, é a hora local; com fuso, é convertida. "
                                         "No máximo um dia no futuro."},
            "valor": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e9, "example": 1450.5,
                      "description": "Em meticais, com ponto decimal."},
            "cidade": {"type": "string", "maxLength": 100, "example": "Maputo"},
            "pais": {"type": "string", "pattern": "^[A-Za-z]{2,3}$", "example": "MZ"},
            "categoria": {"type": "string", "maxLength": 50, "example": "MERCEARIA"},
            "simulacao": {
                "type": "object", "additionalProperties": False, "required": ["fraude"],
                "description": "Só para o simulador: o que o gerador marcou. Nunca é mostrado aos analistas.",
                "properties": {"fraude": {"type": "boolean"},
                               "tipo": {"type": "string", "enum": ["", *TIPOS_FRAUDE]}},
            },
        },
    }
    resultado = {
        "type": "object",
        "properties": {
            "id_transacao": {"type": "string"},
            "risco": {"type": "number", "description": "0 a 100, do classificador. 50 é o limite de alerta."},
            "anomalia": {"type": "number", "description": "0 a 100, do Isolation Forest."},
            "nivel": {"type": "string", "enum": ["Normal", "Atenção", "Alto risco"]},
            "alerta": {"type": "boolean"},
            "motivos": {"type": "array", "items": {"type": "string"}},
            "alerta_id": {"type": "integer", "nullable": True, "description": "Número na fila de alertas."},
            "estado_alerta": {"type": "string", "nullable": True,
                              "enum": ["novo", "em_analise", "fraude", "falso_alarme", None]},
            "duplicada": {"type": "boolean", "description": "O id já tinha sido recebido antes."},
        },
    }
    auth = [{"chave": []}]
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Detetor de Fraudes Financeiras — API",
            "version": "1.0.0",
            "description": "Envio de transações para avaliação em tempo real. As transações com alerta entram "
                           "na fila de alertas da interface. Crie uma chave em Chaves da API (administrador).",
        },
        "servers": [{"url": servidor or "/"}],
        "components": {
            "securitySchemes": {"chave": {"type": "http", "scheme": "bearer",
                                          "description": "Chave da API (começa por dtf_). "
                                                         "Também aceite no cabeçalho X-Chave-API."}},
            "schemas": {
                "Transacao": transacao,
                "Lote": {"type": "object", "additionalProperties": False, "required": ["transacoes"],
                         "properties": {"transacoes": {"type": "array", "minItems": 1, "maxItems": MAX_LOTE,
                                                       "items": {"$ref": "#/components/schemas/Transacao"}}}},
                "Resultado": resultado,
                "Resposta": {"type": "object", "properties": {
                    "recebidas": {"type": "integer"}, "avaliadas": {"type": "integer"},
                    "duplicadas": {"type": "integer"}, "alertas": {"type": "integer"},
                    "resultados": {"type": "array", "items": {"$ref": "#/components/schemas/Resultado"}}}},
                "Erro": {"type": "object", "properties": {
                    "erro": {"type": "string"}, "mensagem": {"type": "string"},
                    "erros": {"type": "array", "items": {"type": "object", "properties": {
                        "indice": {"type": "integer"}, "campo": {"type": "string"}, "erro": {"type": "string"}}}},
                    "tentar_de_novo_em": {"type": "integer"}}},
            },
        },
        "paths": {
            "/api/v1/transacoes": {"post": {
                "summary": "Avaliar uma ou várias transações",
                "description": f"Envie uma transação, ou {{\"transacoes\": [...]}} com até {MAX_LOTE}. Se alguma "
                               "for inválida, nada é gravado e a resposta lista todos os problemas.",
                "security": auth,
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"oneOf": [
                    {"$ref": "#/components/schemas/Transacao"}, {"$ref": "#/components/schemas/Lote"}]}}}},
                "responses": {
                    "200": {"description": "Avaliadas", "content": {"application/json": {
                        "schema": {"$ref": "#/components/schemas/Resposta"}}}},
                    "400": _erro("O corpo não é JSON válido"),
                    "401": _erro("Chave em falta, inválida ou revogada"),
                    "413": _erro("Pedido maior que o permitido"),
                    "415": _erro("O corpo não é application/json"),
                    "422": _erro("Dados inválidos (lista em 'erros')"),
                    "429": _erro("Limite de pedidos por minuto da chave atingido (ver Retry-After)"),
                    "503": _erro("Modelos não treinados"),
                }}},
            "/api/v1/transacoes/{id_transacao}": {"get": {
                "summary": "Consultar o resultado e o estado do alerta de uma transação já enviada",
                "security": auth,
                "parameters": [{"name": "id_transacao", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "Encontrada", "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Resultado"}}}},
                    "401": _erro("Chave inválida"), "404": _erro("Não encontrada")}}},
            "/api/v1/estado": {"get": {
                "summary": "Verificar a chave e o estado do serviço",
                "security": auth,
                "responses": {"200": {"description": "Serviço disponível"}, "401": _erro("Chave inválida"),
                              "503": _erro("Modelos não treinados")}}},
            "/api/v1/openapi.json": {"get": {"summary": "Esta especificação", "security": [],
                                             "responses": {"200": {"description": "OpenAPI 3.0"}}}},
        },
    }
