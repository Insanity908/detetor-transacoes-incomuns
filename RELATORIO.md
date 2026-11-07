# Relatório do treino

Gerado por `python treinar.py` em 2026-11-07T14:29:39, em 57,7 s. Dados sintéticos, gerados para a disciplina; os resultados não valem para fraudes reais.

## Dados

83.511 transações de 300 clientes, com 823 fraudes (1,0%).

| Tipo de fraude | Transações |
|---|---|
| Valor alto | 88 |
| País estranho | 88 |
| Horário estranho | 88 |
| Rajada | 472 |
| Conta tomada | 87 |

## Períodos

A divisão é por tempo: cada período só vem depois do anterior. As features usam só o passado de cada cliente.

| Período | Início | Fim | Transações | Fraudes |
|---|---|---|---|---|
| Aquecimento | 2026-01-01 | 2026-01-30 | 20.986 | 183 |
| Treino | 2026-01-31 | 2026-03-29 | 40.194 | 399 |
| Validação | 2026-03-30 | 2026-04-12 | 9.842 | 111 |
| Teste | 2026-04-13 | 2026-04-30 | 12.489 | 130 |

## Modelos

Métricas da classe fraude. A acurácia engana: com cerca de 1% de fraudes, dizer "legítima" para tudo já dá 99% de acurácia.

| Modelo | Período | Precisão | Recall | F1 | PR AUC | Acurácia |
|---|---|---|---|---|---|---|
| Regressão logística | Validação | 81,0% | 61,3% | 0,697 | 0,707 | 99,4% |
| Regressão logística | Teste | 87,8% | 55,4% | 0,679 | 0,714 | 99,5% |
| Random Forest | Validação | 99,0% | 87,4% | 0,928 | 0,916 | 99,8% |
| Random Forest | Teste | 90,9% | 84,6% | 0,876 | 0,929 | 99,8% |
| Gradient Boosting (HistGradientBoosting) | Validação | 92,7% | 80,2% | 0,860 | 0,904 | 99,7% |
| Gradient Boosting (HistGradientBoosting) | Teste | 90,0% | 76,2% | 0,825 | 0,885 | 99,7% |
| Isolation Forest | Validação | 28,4% | 50,5% | 0,364 | 0,277 | 98,0% |
| Isolation Forest | Teste | 27,2% | 42,3% | 0,331 | 0,218 | 98,2% |

![F1 por modelo](modelos/graficos/comparacao.png)

## Modelo escolhido

**Random Forest.** Maior F1 na validação (0,928). A regra foi fixada antes do treino: o limiar e o modelo vêm só da validação. No teste, o F1 foi 0,876.

## Matriz de confusão

No teste: 110 fraudes apanhadas, 20 deixadas passar, 11 alertas falsos e 12.348 legítimas aceites.

![Matriz de confusão](modelos/graficos/matriz_confusao.png)

## Recall por tipo de fraude

| Tipo | Random Forest | Isolation Forest |
|---|---|---|
| Valor alto | 75,0% | 75,0% |
| País estranho | 57,1% | 7,1% |
| Horário estranho | 50,0% | 60,0% |
| Rajada | 92,6% | 40,7% |
| Conta tomada | 100,0% | 46,2% |

## Isolation Forest

Treinado sem os rótulos (aprendizado não supervisionado), com o limiar que marca 2% da validação. No teste, apanhou 3 fraudes que o classificador deixou passar; essas transações aparecem com o nível "Atenção".

## Importância das features

Calculada por permutação na validação: quanto a PR AUC cai quando os valores de uma feature são embaralhados.

![Importância das features](modelos/graficos/importancia.png)

## Divisão aleatória contra divisão por tempo

As aulas usam `train_test_split` com 80/20 aleatório. Com transações, isso mistura o futuro e o passado de cada cliente no treino.

| Divisão | F1 no teste |
|---|---|
| Por tempo (usada no sistema) | 0,876 |
| Aleatória 80/20 | 0,849 |

Neste conjunto, a divisão aleatória não inflou o resultado. As features já usam só o passado de cada cliente, o que evita o vazamento mais comum; mesmo assim, a divisão por tempo é a que corresponde ao uso real: treinar com o passado e avaliar o futuro.

## Atalhos

PR AUC de cada coluna bruta usada sozinha no teste. Precisa de ficar bem abaixo da do modelo (0,929); senão, o gerador deixou uma pista fácil.

| Coluna | PR AUC |
|---|---|
| valor | 0,191 |
| hora | 0,020 |
| pais | 0,117 |
| categoria | 0,011 |
| cidade | 0,010 |

## Limitações

- Os dados são sintéticos e de um único gerador; os resultados não valem automaticamente para fraudes reais.
- O sistema só conhece os padrões de fraude que o gerador cria.
- Clientes com menos de 5 compras são avaliados com pouca informação.
- O perfil de cada cliente só muda quando o treino é executado de novo.
