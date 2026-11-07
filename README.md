# Detetor de transações incomuns

Trabalho da disciplina de Inteligência Artificial (INAR) da Universidade Católica de Moçambique.

O sistema avalia o risco de cada transação a partir do **comportamento habitual do cliente**. Uma compra de
15.000 MT pode ser normal para um cliente e muito estranha para outro: por isso, o sistema aprende o normal de
cada cliente (valores, horários, cidades, países, categorias e frequência) e mede o quanto cada compra foge dele.

Dois tipos de modelo trabalham juntos:

- **Classificador supervisionado** (Random Forest, escolhido entre três), que aprendeu com fraudes rotuladas.
- **Detetor de anomalias** (Isolation Forest), que aprendeu sem rótulos e serve de segunda opinião.

Cada alerta vem com os motivos, em linguagem simples, por exemplo: "Valor 8,2 vezes o habitual deste cliente
(habitual: 1.450 MT)".

**O que o sistema não faz:** foi treinado e avaliado só com dados sintéticos, e só conhece os padrões de fraude
que o gerador cria. Os resultados não valem automaticamente para fraudes reais.

## Como executar

Precisa de Python 3.11 ou mais recente (exigência das versões fixadas em `requirements.txt`).

```bash
pip install -r requirements.txt
python treinar.py      # gera os dados, treina e avalia; grava modelos/ e RELATORIO.md (1 a 2 minutos)
python app.py          # abre a interface em http://127.0.0.1:5000
python -m pytest       # testes automáticos
```

Não há base de dados: os dados ficam em `data/transacoes.csv`, os modelos em `modelos/` e os resultados em
`modelos/resultados.json`. Para começar do zero, apague `data/` e `modelos/` e execute `python treinar.py` de novo.

## A interface

| Página | O que faz |
|---|---|
| **Testar transação** | Formulário e seis exemplos prontos (compra normal, valor alto, país estranho, madrugada, conta tomada e rajada). Mostra o nível (Alto risco, Atenção ou Normal), o risco, a anomalia e os motivos. |
| **Analisar ficheiro** | Envio de um CSV (até 5 MB, também do Excel em português), tabela ordenada por risco, filtro "só alertas" e download do resultado. |
| **Perfil do cliente** | O comportamento normal de um cliente e as transações dele que os modelos marcaram no período de teste. |
| **Modelos** | Comparação dos modelos, matriz de confusão, recall por tipo de fraude, importância das features e divisão aleatória contra divisão por tempo. |

O CSV precisa das colunas `cliente, data_hora, valor, cidade, pais, categoria`; `id_transacao` é opcional. As
datas podem estar em `aaaa-mm-dd hh:mm` ou `dd/mm/aaaa hh:mm`.

## Conceitos da disciplina e onde aparecem

| Conceito (aulas) | Onde está no código |
|---|---|
| Divisão em treino e teste | `dividir_periodos` em `detector/avaliacao.py`: treino, validação e teste por tempo. A divisão aleatória das aulas (`train_test_split`) é comparada em `treinar.py` |
| Normalização (`StandardScaler`) | `criar_classificadores` em `detector/modelos.py`, dentro de cada `Pipeline` |
| Classificação supervisionada | Regressão logística, Random Forest e Gradient Boosting em `detector/modelos.py` |
| Aprendizagem não supervisionada | Isolation Forest em `detector/modelos.py`, treinado sem os rótulos |
| Métricas e matriz de confusão | `metricas` em `detector/avaliacao.py`; página Modelos e `RELATORIO.md` |
| Importância das features | `permutation_importance` em `treinar.py`; gráfico na página Modelos |
| Previsão de dados novos | `avaliar_transacoes` em `detector/servico.py`; páginas Testar e Analisar |
| Exportação dos modelos | `gravar` e `carregar` em `detector/artefatos.py`, com joblib |

Duas decisões vão além das aulas, e o relatório explica as duas:

- **Divisão por tempo:** com transações, treinar com o passado e avaliar o futuro corresponde ao uso real.
- **Métricas da classe fraude:** com cerca de 1% de fraudes, dizer "legítima" para tudo já dá 99% de
  acurácia. Por isso os modelos são comparados pela precisão, pelo recall e pelo F1 da classe fraude.

## Resultados

Com 70.972 transações de 300 clientes e 713 fraudes, o modelo escolhido na validação foi o **Random Forest**. No
teste, encontrou **70,0%** das fraudes, com **99,0%** dos alertas corretos (F1 de 0,820). O detalhe, com os
gráficos, o recall de cada tipo de fraude e a medida dos clientes novos, está em [`RELATORIO.md`](RELATORIO.md).

**Clientes novos.** Cerca de 25% dos clientes aderem depois dos primeiros 30 dias, para o modelo ver compras de
clientes sem histórico no treino. Sem histórico não há comportamento habitual com que comparar, por isso o Isolation
Forest não dá alerta nesses casos, e a interface avisa que a avaliação é menos fiável. Esta correção foi feita depois
de uma primeira versão em que todos os clientes começavam no dia 1: aí o modelo nunca via um cliente novo e marcava
como "Alto risco" as primeiras compras de madrugada. Com a mudança dos dados, os resultados também mudaram (antes:
84,6% das fraudes apanhadas, 90,9% de alertas corretos).

## Estrutura

| Ficheiro | Função |
|---|---|
| `detector/gerador.py` | Clientes de Moçambique com hábitos próprios e cinco tipos de fraude rotulados |
| `detector/perfis.py` | Normal de cada cliente e features de comportamento, só com o passado |
| `detector/modelos.py` | Os três classificadores, o Isolation Forest, limiares e escolha do modelo |
| `detector/avaliacao.py` | Períodos e métricas |
| `detector/explicacao.py` | Motivos em linguagem simples |
| `detector/entrada.py` | Leitura e validação de CSV e do formulário |
| `detector/servico.py` | Avaliação de transações novas |
| `detector/artefatos.py` | Gravar e carregar os modelos |
| `detector/exemplos.py` | Exemplos da página de teste |
| `treinar.py` | Treino completo, gráficos e relatório |
| `app.py`, `templates/`, `static/` | Interface Flask |
| `tests/` | Testes automáticos |

## Limitações

- Os dados são sintéticos e de um único gerador.
- O sistema só conhece os padrões de fraude que o gerador cria.
- Clientes com menos de 5 compras são avaliados com pouca informação. O treino tem poucas fraudes de clientes
  novos (2 no período de treino), por isso o modelo quase não alerta nesse grupo: uma compra muito alta ou num país
  estranho de um cliente novo tende a sair como "Normal", só com o aviso de pouco histórico.
- O perfil de cada cliente só muda quando o treino é executado de novo.
- O Random Forest usa 100 árvores (o valor das aulas) em vez das 300 previstas no design, para o treino ser
  mais rápido. A mudança foi feita depois de ver o resultado do teste, por causa do tempo e não do desempenho
  (na primeira versão dos dados, F1 de 0,870 com 300 árvores e 0,876 com 100).
- Os ficheiros de modelo usam joblib (pickle): carregue apenas modelos gerados por si.
