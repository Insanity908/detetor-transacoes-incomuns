# Task 13 - Simulador com salário e contadores

## Implementado
- `Simulador.avancar` devolve `(transacoes, depositos)`; salários no dia 25 às 08:00 simuladas (uma vez por cliente e mês,
  incluindo o primeiro mês se o início for antes), valor `round(tipico*taxa*30*uniform(1.1,1.4), 2)`,
  `id_deposito = f"{prefixo}-SAL-{AAAAMM}-{cliente}"`. Usa um gerador aleatório próprio (semente derivada) para não
  alterar a sequência de compras/fraudes por semente.
- `simular()` envia depósitos (lotes de 500) a /api/v1/depositos antes das transações do passo; em 422 avisa uma vez
  ("Os clientes do simulador não têm conta no banco: corra gerir.py preparar.") e continua.
- `ErroFatal` ganhou `estado`. `Estatisticas.recusadas_saldo`/`retidas`; `resumo()` tem linha extra
  "recusadas por saldo N · retidas N". `--tipos conta_esvaziada` já vinha de TIPOS_FRAUDE.
- Chamadores de `avancar` atualizados (carga.py, tests/test_api.py, tests/test_ciclo_modelo.py).

## TDD
RED: 5 testes novos falharam (AttributeError/tuplo). GREEN após implementação. Suite completa: 473 passed.

## Ficheiros
detector/simulacao.py, simulador.py, carga.py, tests/test_api.py, tests/test_ciclo_modelo.py

## Concerns
Nos testes ponta a ponta existentes, se o relógio simulado atravessar o dia 25 os depósitos recebem 422 (sem contas) e
só geram o aviso. Depois de um 422 o aviso não repete, mas o envio mensal seguinte continua a ser tentado.
