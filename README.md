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

```bash
pip install -r requirements.txt
python treinar.py      # gera os dados, treina e avalia; grava modelos/ e RELATORIO.md (cerca de 1 minuto)
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

Com 83.511 transações de 300 clientes e 823 fraudes, o modelo escolhido na validação foi o **Random Forest**. No
teste, encontrou **84,6%** das fraudes, com **90,9%** dos alertas corretos (F1 de 0,876). O detalhe, com os
gráficos e o recall de cada tipo de fraude, está em [`RELATORIO.md`](RELATORIO.md).

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
- Clientes com menos de 5 compras são avaliados com pouca informação. Além disso, o modelo nunca viu um cliente
  assim no treino (os 30 primeiros dias só formam o histórico), por isso apoia-se no horário e no valor brutos:
  as primeiras compras de madrugada de um cliente novo tendem a sair como "Alto risco".
- O perfil de cada cliente só muda quando o treino é executado de novo.
- O Random Forest usa 100 árvores (o valor das aulas) em vez das 300 previstas no design, para o treino caber
  em menos de 1 minuto. A mudança foi feita depois de ver o resultado do teste, por causa do tempo e não do
  desempenho (F1 de 0,870 com 300 árvores e 0,876 com 100).
- Os ficheiros de modelo usam joblib (pickle): carregue apenas modelos gerados por si.
