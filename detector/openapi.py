"""Especificação OpenAPI 3.0 da API v1 (servida em /api/v1/openapi.json e mostrada em /api/docs).

Pode ser aberta em qualquer ferramenta compatível (Swagger Editor, Swagger UI, Postman, Insomnia).
"""
from __future__ import annotations

from .simulacao import TIPOS_SIMULACAO

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
                               "tipo": {"type": "string", "enum": ["", *TIPOS_SIMULACAO]}},
            },
        },
    }
    resultado = {
        "type": "object",
        "properties": {
            "id_transacao": {"type": "string"},
            "risco": {"type": "number", "nullable": True,
                      "description": "0 a 100, do classificador. 50 é o limite de alerta. Nulo quando o banco "
                                     "recusou o pedido antes da avaliação (saldo insuficiente, conta bloqueada)."},
            "anomalia": {"type": "number", "nullable": True, "description": "0 a 100, do Isolation Forest. "
                                                                           "Nulo como o risco."},
            "nivel": {"type": "string", "nullable": True, "enum": ["Normal", "Atenção", "Alto risco", None],
                      "description": "Nulo quando o banco recusou o pedido antes da avaliação."},
            "alerta": {"type": "boolean", "description": "Falso quando não houve avaliação."},
            "motivos": {"type": "array", "items": {"type": "string"}},
            "alerta_id": {"type": "integer", "nullable": True, "description": "Número na fila de alertas."},
            "estado_alerta": {"type": "string", "nullable": True,
                              "enum": ["novo", "em_analise", "fraude", "falso_alarme", None]},
            "duplicada": {"type": "boolean", "description": "O id já tinha sido recebido antes."},
            "movimento": {
                "type": "object", "nullable": True,
                "description": "O que aconteceu ao dinheiro, para clientes com conta bancária no sistema "
                               "(nulo para os restantes). Uma transação repetida devolve o movimento já gravado, "
                               "sem debitar outra vez. A categoria TRANSFERENCIA é uma saída para fora do banco.",
                "properties": {
                    "estado": {"type": "string", "enum": ["autorizado", "retido", "recusado", "cancelado"]},
                    "motivo": {"type": "string", "nullable": True,
                               "description": "Porque foi recusado (por exemplo, saldo insuficiente)."}},
            },
        },
    }
    deposito = {
        "type": "object", "additionalProperties": False,
        "required": ["id_deposito", "cliente", "valor", "data_hora"],
        "properties": {
            "id_deposito": {"type": "string", "pattern": "^[A-Za-z0-9._:\\-]{1,96}$", "example": "DEP-000123",
                            "description": "Identificador único do depósito. Reenviar o mesmo id não soma outra "
                                           "vez ao saldo."},
            "cliente": {"type": "string", "maxLength": 100, "example": "C0001",
                        "description": "Código do cliente; tem de ter conta bancária."},
            "valor": {"type": "number", "exclusiveMinimum": 0, "example": 2500.5,
                      "description": "Em meticais, com no máximo duas casas decimais."},
            "data_hora": {"type": "string", "format": "date-time", "example": "2026-05-01T14:30:00"},
            "descricao": {"type": "string", "maxLength": 140},
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
                "Deposito": deposito,
                "LoteDepositos": {"type": "object", "additionalProperties": False, "required": ["depositos"],
                                  "properties": {"depositos": {"type": "array", "minItems": 1, "maxItems": MAX_LOTE,
                                                               "items": {"$ref": "#/components/schemas/Deposito"}}}},
                "RespostaDepositos": {"type": "object", "properties": {"resultados": {
                    "type": "array", "items": {"type": "object", "properties": {
                        "id_deposito": {"type": "string"},
                        "estado": {"type": "string", "enum": ["autorizado", "recusado"]},
                        "motivo": {"type": "string", "description": "Só quando recusado (conta bloqueada)."},
                        "saldo": {"type": "number", "description": "Saldo da conta depois do depósito, em meticais."}}}}}},
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
            "/api/v1/depositos": {"post": {
                "summary": "Depositar dinheiro em contas do banco",
                "description": f"Envie {{\"depositos\": [...]}} com até {MAX_LOTE}. Idempotente por id_deposito. "
                               "Se algum for inválido (por exemplo, cliente sem conta), nada é gravado e a resposta "
                               "lista todos os problemas. Uma conta bloqueada recusa o depósito sem invalidar o lote.",
                "security": auth,
                "requestBody": {"required": True, "content": {"application/json": {"schema": {
                    "$ref": "#/components/schemas/LoteDepositos"}}}},
                "responses": {
                    "200": {"description": "Processados", "content": {"application/json": {
                        "schema": {"$ref": "#/components/schemas/RespostaDepositos"}}}},
                    "400": _erro("O corpo não é JSON válido"),
                    "401": _erro("Chave em falta, inválida ou revogada"),
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
