# Detector de transações incomuns: design

**Data:** 2026-11-07
**Estado:** aguardando revisão

## 1. Objetivo

Trabalho da disciplina de Inteligência Artificial (INAR) da Universidade Católica de Moçambique. O sistema
deteta transações incomuns e avalia o risco de cada uma a partir do comportamento habitual do cliente,
com modelos de aprendizado de máquina e uma interface web.

O objetivo, definido pelo autor, é o sistema funcionar da melhor forma possível. O trabalho também deve
mostrar, de forma que o professor reconheça, os conceitos das aulas: divisão em treino e teste,
normalização, classificação supervisionada, matriz de confusão, importância das features, previsão de
dados novos, exportação dos modelos e aprendizado não supervisionado.

### Critérios de sucesso

1. `python treinar.py` gera os dados, treina os modelos e grava tudo em menos de 1 minuto.
2. `python app.py` abre a interface em `http://127.0.0.1:5000` com as quatro páginas da seção 7.
3. Cada alerta explica, em linguagem simples e com números, por que a transação é estranha para aquele
   cliente.
4. Nenhuma fraude do gerador pode ser reconhecida por uma única coluna bruta (seção 4).
5. A avaliação não usa o teste para escolher nada, e o relatório mostra os resultados como saírem.
6. `python -m pytest` passa.

### Restrições

- Sem base de dados e sem Docker. Dados, modelos e resultados ficam em ficheiros (CSV, joblib, JSON, PNG).
- Python com Flask, scikit-learn, pandas, numpy, matplotlib e joblib. Testes com pytest.
- A interface só lê os ficheiros gerados pelo treino; não treina modelos.
- Textos da interface e dos documentos em português de Moçambique (norma europeia: "ficheiro",
  "telemóvel", "eletrónica").

### Fora do âmbito

Autenticação, base de dados, Docker, deploy, retreino pela interface, dados reais e conjuntos públicos.

## 2. Origem das decisões

Decididas pelo autor em 2026-11-07, depois de ler as apostilas das aulas 1, 3, 4, 5 e 6:

- Trabalho da disciplina, não peça de portfólio.
- Dois tipos de modelo juntos: classificação supervisionada e deteção de anomalias.
- Gerador sintético próprio, no contexto de Moçambique.
- Interface em Flask com páginas HTML.
- Quatro funções na interface: testar transação, analisar ficheiro, perfil do cliente e modelos.
- Abordagem com features de comportamento por cliente, divisão por tempo e métricas da classe fraude.
- Só os modelos que levam ao melhor resultado, em vez dos 10 da aula 4.

Lições trazidas do projeto anterior (detetor de fraudes no TabFormer):

- Dados sintéticos fáceis inflam os resultados: 92% de precisão no próprio gerador viraram 1,3% noutro
  conjunto. Por isso a seção 4 proíbe fraudes reconhecíveis por uma única coluna.
- Escolher modelos e limiares olhando o teste invalida a avaliação. Por isso existem três períodos.

## 3. Arquitetura

O treino roda uma vez no terminal e grava ficheiros. A interface só os carrega.

```
python treinar.py
  gerador ──> data/transacoes.csv
          ──> features de comportamento (só com o passado de cada cliente)
          ──> 3 classificadores + Isolation Forest
          ──> modelos/  (.joblib, resultados.json, gráficos .png) e RELATORIO.md

python app.py
  carrega modelos/ ──> Testar · Analisar ficheiro · Perfil do cliente · Modelos
```

| Ficheiro | Responsabilidade |
|---|---|
| `detector/gerador.py` | Clientes de Moçambique com hábitos e fraudes rotuladas |
| `detector/perfis.py` | Normal de cada cliente e features de desvio |
| `detector/modelos.py` | Treino dos 3 classificadores e do Isolation Forest, limiares e escolha |
| `detector/avaliacao.py` | Métricas, matriz de confusão, divisões por tempo e aleatória |
| `detector/explicacao.py` | Motivos em linguagem simples |
| `detector/artefatos.py` | Gravar e carregar modelos (joblib) e resultados (JSON) |
| `treinar.py` | Executa o treino completo e gera `RELATORIO.md` |
| `app.py`, `templates/`, `static/` | Interface Flask |
| `tests/` | Testes |

Se `modelos/` não existir ou estiver incompleta, todas as páginas mostram a instrução
"Execute `python treinar.py` para gerar os modelos" em vez de um erro.

## 4. Dados

### Gerador

- Semente fixa (`42`), cerca de 300 clientes, 120 dias a partir de 2026-01-01.
- Cada cliente tem: cidade (Maputo, Matola, Beira, Nampula, Quelimane, Tete), valor típico em meticais
  (MT), horário de pico, 3 ou 4 categorias preferidas entre mercearia, combustível, restaurante,
  farmácia, recarga de telemóvel, transporte, vestuário, eletrónica e viagem, e frequência própria.
- Ruído legítimo: viagens ocasionais à África do Sul (ZA), compras grandes ocasionais, compras noturnas
  ocasionais e mudança de cidade de alguns clientes.
- Colunas: `id_transacao, cliente, data_hora, valor, cidade, pais, categoria, fraude, tipo_fraude`.

### Fraudes

Cerca de 1% das transações, em cinco tipos:

| Tipo | Como aparece |
|---|---|
| `valor_alto` | 3 a 15 vezes o valor típico do cliente |
| `pais_estranho` | País que o cliente nunca usou (por exemplo NG, AE, CN, BR), às vezes vizinho |
| `horario_estranho` | Entre 1h e 5h, fora do padrão do cliente |
| `rajada` | Várias compras pequenas em poucos minutos |
| `conta_tomada` | Cidade nova, categoria nova e valor médio-alto ao mesmo tempo |

**Regra contra atalhos:** as categorias, cidades e valores absolutos das fraudes seguem as mesmas
distribuições das compras legítimas. Nenhuma coluna bruta (`valor`, hora, `pais`, `categoria`, `cidade`)
pode, sozinha, ter PR AUC acima de metade da PR AUC do modelo escolhido no teste. Um teste automático
verifica isso.

### Features

Cada feature de uma transação usa só as transações anteriores do mesmo cliente.

| Feature | Definição |
|---|---|
| `valor_vs_habitual` | Valor dividido pela mediana dos valores anteriores do cliente |
| `hora_incomum` | Fração das compras anteriores feitas a mais de 3 horas desse horário |
| `pais_novo` | 1 se o cliente nunca usou esse país |
| `cidade_nova` | 1 se o cliente nunca usou essa cidade |
| `categoria_nova` | 1 se o cliente nunca usou essa categoria |
| `compras_ultima_hora` | Compras do cliente na hora anterior |
| `compras_24h` | Compras do cliente nas 24 horas anteriores |
| `minutos_desde_anterior` | Minutos desde a compra anterior (limitado a 10.080, uma semana) |
| `historico_curto` | 1 se o cliente tem menos de 5 compras anteriores |
| `valor` | Valor bruto em MT |
| `hora` | Hora do dia |

Com histórico curto, as features de desvio ficam neutras: `valor_vs_habitual = 1`, `hora_incomum = 0` e
`pais_novo`, `cidade_nova`, `categoria_nova` iguais a 0.

País, cidade e categoria são comparados depois de remover espaços e converter para maiúsculas.

### Períodos

| Período | Dias | Uso |
|---|---|---|
| Aquecimento | 1 a 30 | Só formam o histórico; não entram no treino |
| Treino | primeiros 64% dos dias 31 a 120 | Os modelos aprendem |
| Validação | os 16% seguintes | Limiares e escolha do modelo |
| Teste | os últimos 20% | Só medição, uma vez |

A divisão é por tempo e sem sobreposição. A divisão aleatória de 80/20 das aulas é usada apenas na
comparação da seção 6.

## 5. Modelos

### Classificadores

| Modelo | Papel |
|---|---|
| `HistGradientBoostingClassifier` | Candidato mais forte em dados de tabela; versão rápida do Gradient Boosting da aula 4 |
| `RandomForestClassifier` (`n_estimators=300`) | Segunda opinião robusta |
| `LogisticRegression` (`max_iter=1000`) | Referência simples e explicável |

- Cada modelo fica num `Pipeline` com `StandardScaler`, exportado junto.
- `random_state=42` em todos.
- **Limiar:** para cada modelo, o valor de probabilidade que maximiza o F1 da classe fraude na validação.
- **Escolha:** vence o maior F1 na validação. Se outros ficarem até 0,02 abaixo do melhor, vence o mais
  simples entre eles, nesta ordem: regressão logística, Random Forest, HistGradientBoosting. A regra está
  fixada antes do primeiro treino.
- Depois da escolha, os modelos não são retreinados com a validação.

### Detetor de anomalias

- `IsolationForest` (`random_state=42`), treinado com as features do período de treino, sem os rótulos.
- Limiar: o score que marca 2% das transações da validação.
- Os rótulos servem só para medir o detetor, na mesma tabela dos classificadores.

### Decisão mostrada ao utilizador

| Classificador escolhido | Isolation Forest | Nível |
|---|---|---|
| Alerta | qualquer | Alto risco |
| Normal | Alerta | Atenção |
| Normal | Normal | Normal |

- **Risco de 0 a 100:** `50 × p / limiar` abaixo do limiar e `50 + 50 × (p − limiar) / (1 − limiar)`
  acima dele, onde `p` é a probabilidade do classificador escolhido. Assim o limiar corresponde
  sempre a 50.
- **Anomalia de 0 a 100:** a mesma construção com `a = −score_samples(x)` do Isolation Forest (maior é
  mais anómalo, normalmente entre 0 e 1) no lugar de `p`, e o seu limiar.
- Os dois valores são limitados ao intervalo de 0 a 100.

## 6. Avaliação

- **Métricas, na validação e no teste, para os 3 classificadores e o Isolation Forest:** precisão,
  recall, F1 e PR AUC da classe fraude, e acurácia. A acurácia aparece com a nota de que, com cerca de 1%
  de fraudes, dizer "legítima" para tudo dá 99%.
- **Matriz de confusão** do modelo escolhido no teste.
- **Recall por tipo de fraude** no teste, para o modelo escolhido e para o Isolation Forest.
- **Ganho do Isolation Forest:** fraudes do teste que só ele apanhou.
- **Importância das features** do modelo escolhido, por permutação na validação
  (`sklearn.inspection.permutation_importance`).
- **Divisão aleatória contra divisão por tempo:** o modelo escolhido treinado também com
  `train_test_split(test_size=0.2, random_state=42)` sobre os dias 31 a 120, com os dois F1 lado a lado.
- **Aviso de gerador fácil:** se o F1 do modelo escolhido no teste passar de 0,98, o relatório diz que o
  gerador provavelmente ficou fácil demais.
- Não há nota alvo fixada antes: o relatório mostra os resultados como saírem.

`resultados.json` guarda tudo isto, e `RELATORIO.md` apresenta-o com os gráficos e as limitações.

## 7. Interface

Flask com templates Jinja (escape automático), um único ficheiro CSS e sem framework de JavaScript.
Corre em `127.0.0.1:5000`. Cabeçalho com as quatro páginas e o aviso fixo: "Dados sintéticos, gerados
para a disciplina; os resultados não valem para fraudes reais".

### Testar transação

- Formulário: cliente (lista dos clientes conhecidos ou "cliente novo"), valor em MT, data e hora,
  cidade, país e categoria.
- Botões de exemplo: compra normal, valor alto, país estranho, madrugada, conta tomada e rajada. A
  rajada envia uma sequência de 5 compras, e as compras da mesma sequência contam para a frequência.
- O histórico usado é o do cliente nos dados gerados. Nada do que o utilizador envia é guardado.
- Resultado: nível, risco, anomalia e motivos com números, por exemplo:
  - "Valor 8,2 vezes o habitual deste cliente (habitual: 1.450 MT)"
  - "Primeira compra deste cliente fora de Moçambique (AE)"
  - "4 compras na última hora; o normal deste cliente é menos de 1 por dia"

### Analisar ficheiro

- Envio de CSV com `cliente, data_hora, valor, cidade, pais, categoria`, até 5 MB. `id_transacao` é
  opcional.
- O histórico de cada cliente vem dos dados gerados e das linhas anteriores do próprio ficheiro, por
  ordem de tempo.
- Tabela ordenada por risco, filtro "só alertas" e download do resultado em CSV. Textos que começam por
  `=`, `+`, `-` ou `@` são neutralizados no download.

### Perfil do cliente

- Escolha de um cliente: valor típico e faixa, horários em barras por hora, cidades, países, categorias e
  compras por dia.
- Ao lado, as transações desse cliente no período de teste que os modelos marcaram, com os motivos.

### Modelos

- Tabela da seção 6, modelo escolhido e porquê, matriz de confusão, importância das features, recall por
  tipo e comparação entre as divisões.
- Os gráficos são PNG gerados pelo `treinar.py` com matplotlib; a página só os exibe.

## 8. Erros

| Situação | Comportamento |
|---|---|
| `modelos/` em falta ou incompleta | Página com a instrução para executar `python treinar.py` |
| Valor zero ou negativo, data inválida, campo vazio | Recusado, indicando o campo ou a linha |
| Cliente desconhecido | Avaliado como cliente novo, com o aviso de histórico curto |
| CSV acima de 5 MB ou sem colunas obrigatórias | Recusado, com a lista do que falta |
| Grafias diferentes (`mz`, ` MZ `) | Tratadas como o mesmo valor |

## 9. Testes

- Gerador reprodutível com a mesma semente, cerca de 1% de fraudes e os 5 tipos presentes.
- Regra contra atalhos da seção 4.
- Sem vazamento: alterar uma transação futura não muda as features das anteriores; os períodos não se
  sobrepõem.
- Limiar e escolha usam só a validação; a regra de empate escolhe o mais simples.
- Conversão do risco: o limiar corresponde a 50.
- Motivos com os números certos.
- Interface: as quatro páginas abrem, o teste de transação e o envio de CSV válido e inválido funcionam,
  o download neutraliza fórmulas, e a ausência de modelos mostra a instrução.
- No fim, verificação no Chrome das quatro páginas, também em largura de telemóvel.

## 10. Entrega

- `README.md`: como executar, o que cada ficheiro faz e a tabela "conceito da aula e onde aparece no
  código".
- `RELATORIO.md`: gerado pelo `treinar.py`, com resultados, gráficos e limitações.
- `requirements.txt` com as versões fixas.
- Repositório git próprio, com commits por etapa.

## 11. Limitações a declarar

- Os dados são sintéticos e de um único gerador; os resultados não valem automaticamente para fraudes
  reais.
- O sistema só conhece os padrões de fraude que o gerador cria.
- Clientes com menos de 5 compras são avaliados com pouca informação.
- O perfil de cada cliente só muda quando o treino é executado de novo.
