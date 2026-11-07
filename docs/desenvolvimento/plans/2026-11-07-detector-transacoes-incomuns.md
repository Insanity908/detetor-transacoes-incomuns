# Detetor de transações incomuns. Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir o sistema da disciplina INAR que deteta transações incomuns com três classificadores e um Isolation Forest sobre features de comportamento por cliente, com interface Flask de quatro páginas.

**Architecture:** O pacote `detector/` gera os dados, calcula as features só com o passado de cada cliente, treina e avalia os modelos por tempo e explica cada alerta. `treinar.py` grava tudo em `modelos/`; `app.py` (Flask) só carrega esses ficheiros. Nada é guardado em base de dados.

**Tech Stack:** Python 3.14, pandas, numpy, scikit-learn, matplotlib, joblib, Flask, pytest.

**Spec:** `docs/desenvolvimento/specs/2026-11-07-detector-transacoes-incomuns-design.md`

## Global Constraints

- Sem base de dados e sem Docker. Ficheiros: `data/transacoes.csv`, `modelos/*.joblib`, `modelos/resultados.json`, `modelos/graficos/*.png`, `RELATORIO.md`.
- `random_state=42` / semente `42` em tudo o que for aleatório.
- Textos ao utilizador em português de Moçambique (norma europeia: "ficheiro", "telemóvel", "eletrónica", "utilizador"). Números com vírgula decimal e ponto nos milhares ("1.450 MT", "8,2").
- Moeda: meticais, escrita "MT". País habitual: `MZ`.
- O teste nunca escolhe nada: limiares e modelo vêm da validação.
- A interface corre só em `127.0.0.1:5000` e não guarda o que o utilizador envia.
- Comando de testes: `python -m pytest -q`, na raiz do projeto. Só fazer commit com a suíte verde.
- Mensagens de commit terminam com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Raiz do projeto: `C:\Users\sh\Python VSCode-projects\Detector de Transacoes Incomuns` (já tem git, `.gitignore` e a spec).

## Review Focus

- CSV exportado do Excel em Moçambique, com `;` e vírgula decimal: tem de ser lido, não recusado (teste na Tarefa 4).
- Datas em `dd/mm/aaaa hh:mm`, além de ISO: aceites; outros formatos recusados indicando a linha (teste na Tarefa 4).
- Transação testada com data anterior ao fim do histórico: as features usam só as compras anteriores a essa data (teste na Tarefa 4).
- Cliente novo a enviar uma rajada: a frequência conta as compras da própria sequência, mesmo sem histórico (teste na Tarefa 4).
- Valor muito grande (1.000.000.000 MT) ou cidade/categoria nunca vista: sem erro, com risco calculado (teste na Tarefa 4).

---

### Task 1: Projeto e gerador de transações

**Files:**
- Create: `requirements.txt`, `detector/__init__.py`, `detector/gerador.py`, `tests/test_gerador.py`

**Interfaces:**
- Produces (`detector/gerador.py`):
  - `INICIO = "2026-01-01"`, `DIAS = 120`, `PAIS_HABITUAL = "MZ"`.
  - `CIDADES = ("MAPUTO", "MATOLA", "BEIRA", "NAMPULA", "QUELIMANE", "TETE")`.
  - `CATEGORIAS = ("MERCEARIA", "COMBUSTIVEL", "RESTAURANTE", "FARMACIA", "RECARGA_TELEMOVEL", "TRANSPORTE", "VESTUARIO", "ELETRONICA", "VIAGEM")`.
  - `TIPOS_FRAUDE = ("valor_alto", "pais_estranho", "horario_estranho", "rajada", "conta_tomada")`.
  - `gerar_transacoes(n_clientes: int = 300, dias: int = DIAS, semente: int = 42, inicio: str = INICIO) -> pd.DataFrame` com as colunas `id_transacao, cliente, data_hora, valor, cidade, pais, categoria, fraude, tipo_fraude`, ordenado por `data_hora` e `id_transacao`. `id_transacao` no formato `T000001`; `cliente` no formato `C0001`; `fraude` 0/1; `tipo_fraude` vazio (`""`) nas legítimas; `valor` em MT com 2 casas.

**Regras do gerador (spec, seção 4):**
- Hábitos por cliente: cidade, valor típico (lognormal, mediana entre cerca de 300 e 5.000 MT), hora de pico, 3 ou 4 categorias preferidas, 0,5 a 4 compras por dia.
- Ruído legítimo: cerca de 1% das compras na África do Sul (`ZA`), cerca de 1% de compras grandes (3 a 8 vezes o típico), cerca de 2% de compras entre 0h e 5h, e cerca de 5% dos clientes mudam de cidade a meio do período.
- Fraudes: cerca de 1% das transações, com os cinco tipos. Rajada = 4 a 7 compras pequenas em menos de 15 minutos (todas rotuladas). Conta tomada = cidade nova + categoria nova + 2 a 5 vezes o típico.
- Contra atalhos: a categoria de cada fraude é sorteada com as mesmas proporções globais das compras legítimas; a cidade das fraudes que não são `conta_tomada` é a do cliente; o valor absoluto das fraudes `valor_alto` depende do típico do cliente, não de uma faixa fixa.

- [ ] **Step 1: Criar `requirements.txt` e instalar**

Conteúdo: `numpy`, `pandas`, `scikit-learn`, `matplotlib`, `joblib`, `flask`, `pytest`, cada um com `==` na versão instalada (`pip install flask`, depois `pip freeze` para fixar). Versões já instaladas: numpy 2.3.4, pandas 2.3.3, scikit-learn 1.7.2, matplotlib 3.10.7, joblib 1.5.2, pytest 9.2.0.

- [ ] **Step 2: Escrever os testes que falham**

```python
def test_mesma_semente_gera_os_mesmos_dados():
    a, b = gerar_transacoes(80, 60), gerar_transacoes(80, 60)
    pd.testing.assert_frame_equal(a, b)

def test_colunas_e_valores():
    df = gerar_transacoes(80, 60)
    assert list(df.columns) == ["id_transacao", "cliente", "data_hora", "valor", "cidade", "pais",
                                "categoria", "fraude", "tipo_fraude"]
    assert (df.valor > 0).all() and df.data_hora.is_monotonic_increasing and df.id_transacao.is_unique
    assert set(df.cidade) <= set(CIDADES)   # conta_tomada também só usa CIDADES
    assert (df.loc[df.fraude == 0, "tipo_fraude"] == "").all()

def test_taxa_e_tipos_de_fraude():
    df = gerar_transacoes()      # tamanho completo
    assert 0.005 <= df.fraude.mean() <= 0.02
    assert set(df.loc[df.fraude == 1, "tipo_fraude"]) == set(TIPOS_FRAUDE)

def test_ruido_legitimo_existe():
    df = gerar_transacoes()
    legit = df[df.fraude == 0]
    assert (legit.pais == "ZA").any() and (legit.data_hora.dt.hour < 5).any()

def test_categorias_das_fraudes_seguem_as_legitimas():
    df = gerar_transacoes()
    p_legit = df[df.fraude == 0].categoria.value_counts(normalize=True)
    p_fraude = df[(df.fraude == 1) & (df.tipo_fraude != "conta_tomada")].categoria.value_counts(normalize=True)
    assert (p_fraude.reindex(p_legit.index, fill_value=0) - p_legit).abs().max() < 0.10
```

- [ ] **Step 3: Confirmar a falha**

Run: `python -m pytest tests/test_gerador.py -q`
Expected: FAIL com `ModuleNotFoundError: detector.gerador`.

- [ ] **Step 4: Implementar `gerar_transacoes` em `detector/gerador.py`**

Um `np.random.default_rng(semente)` para tudo. Gerar cliente a cliente, juntar, ordenar e numerar os IDs no fim.

- [ ] **Step 5: Confirmar que passa**

Run: `python -m pytest -q`
Expected: todos passam.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt detector tests
git commit -m "Adiciona o gerador de transacoes de Mocambique"
```

---

### Task 2: Features de comportamento e perfil do cliente

**Files:**
- Create: `detector/perfis.py`, `tests/test_perfis.py`

**Interfaces:**
- Consumes: o formato de `gerar_transacoes` (Tarefa 1).
- Produces (`detector/perfis.py`):
  - `FEATURES = ["valor_vs_habitual", "hora_incomum", "pais_novo", "cidade_nova", "categoria_nova", "compras_ultima_hora", "compras_24h", "minutos_desde_anterior", "historico_curto", "valor", "hora"]`.
  - `MIN_HISTORICO = 5`, `MINUTOS_MAX = 10_080`.
  - `normalizar(df: pd.DataFrame) -> pd.DataFrame`: cópia com `cliente` sem espaços nas pontas, `cidade`, `pais`, `categoria` sem espaços e em maiúsculas, `data_hora` como datetime.
  - `calcular_features(df: pd.DataFrame, historico: pd.DataFrame | None = None) -> pd.DataFrame`: uma linha por linha de `df`, com o mesmo índice e as colunas `FEATURES`. Para cada linha usa só as transações do mesmo cliente estritamente anteriores (por `data_hora`; empates pela ordem em `df`), vindas de `historico` e das linhas anteriores de `df`. As linhas de `historico` não aparecem no resultado.
  - `perfil_cliente(historico: pd.DataFrame, cliente: str) -> dict | None`: `None` se o cliente não existir; senão `{"n_compras", "valor_mediana", "valor_p10", "valor_p90", "compras_por_dia", "horas" (lista de 24 contagens), "cidades", "paises", "categorias" (dicts nome→contagem, por ordem decrescente)}`, só com transações legítimas (`fraude == 0` quando a coluna existir).

**Definições (spec, seção 4):**
- `valor_vs_habitual` = valor / mediana dos valores anteriores.
- `hora_incomum` = fração das compras anteriores cuja distância circular de hora (em horas, de 0 a 12) passa de 3.
- `pais_novo`, `cidade_nova`, `categoria_nova` = 1 se nunca usado antes.
- `compras_ultima_hora` / `compras_24h` = compras anteriores nos 60 minutos / 24 horas antes.
- `minutos_desde_anterior` = minutos desde a anterior, limitado a `MINUTOS_MAX`; sem anterior, `MINUTOS_MAX`.
- `historico_curto` = 1 se houver menos de `MIN_HISTORICO` compras anteriores; nesse caso `valor_vs_habitual = 1`, `hora_incomum = 0`, `pais_novo = cidade_nova = categoria_nova = 0`. As features de frequência continuam calculadas.
- `hora` = hora do dia (0 a 23).

**Algoritmo:** juntar histórico e `df` (marcando a origem), ordenar de forma estável por `cliente`, `data_hora`, percorrer cada cliente uma vez mantendo listas de valores, horas e conjuntos vistos, e para as janelas de 1 h e 24 h usar `np.searchsorted` sobre os tempos anteriores. Precisa de correr 60 mil transações em menos de 15 segundos.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_valor_vs_habitual_e_novidades():
    # cliente C1 com 5 compras de 100 MT em MAPUTO/MZ/MERCEARIA, depois 800 MT em BEIRA/ZA/VIAGEM
    f = calcular_features(df).iloc[-1]
    assert f.valor_vs_habitual == 8.0 and f.pais_novo == f.cidade_nova == f.categoria_nova == 1
    assert f.historico_curto == 0

def test_historico_curto_fica_neutro():
    # 2 compras anteriores apenas
    f = calcular_features(df).iloc[-1]
    assert f.historico_curto == 1 and f.valor_vs_habitual == 1 and f.pais_novo == 0 and f.hora_incomum == 0

def test_frequencia_recente():
    # 4 compras em 30 minutos
    assert calcular_features(df).compras_ultima_hora.tolist() == [0, 1, 2, 3]

def test_hora_incomum():
    # 5 compras às 14h, depois uma às 3h e outra às 15h
    f = calcular_features(df)
    assert f.hora_incomum.iloc[-2] == 1.0 and f.hora_incomum.iloc[-1] == 0.0

def test_futuro_nao_muda_o_passado():
    df = gerar_transacoes(60, 60)
    a = calcular_features(df)
    alterado = df.copy(); alterado.loc[alterado.index[-50:], "valor"] *= 100
    pd.testing.assert_frame_equal(a.iloc[:-50], calcular_features(alterado).iloc[:-50])

def test_historico_externo_conta_como_passado():
    hist, novas = df.iloc[:-1], df.iloc[-1:]
    pd.testing.assert_frame_equal(calcular_features(df).iloc[-1:], calcular_features(novas, historico=hist))

def test_grafia_nao_muda_o_resultado():
    # mesma sequência com " mz ", "Maputo", "mercearia" na última linha
    assert calcular_features(a).iloc[-1].equals(calcular_features(b).iloc[-1])

def test_perfil_do_cliente():
    p = perfil_cliente(hist, "C1")
    assert p["n_compras"] == 5 and p["valor_mediana"] == 100 and len(p["horas"]) == 24
    assert perfil_cliente(hist, "NAO_EXISTE") is None
```

- [ ] **Step 2: Confirmar a falha**

Run: `python -m pytest tests/test_perfis.py -q`
Expected: FAIL com `ModuleNotFoundError: detector.perfis`.

- [ ] **Step 3: Implementar `detector/perfis.py`**

- [ ] **Step 4: Confirmar que passa e medir o tempo**

Run: `python -m pytest -q` e `python -c "import time; from detector.gerador import gerar_transacoes; from detector.perfis import calcular_features; d=gerar_transacoes(); t=time.time(); calcular_features(d); print(len(d), round(time.time()-t,1))"`
Expected: suíte verde; menos de 15 s.

- [ ] **Step 5: Commit**

```bash
git add detector/perfis.py tests/test_perfis.py
git commit -m "Calcula as features de comportamento por cliente"
```

---

### Task 3: Períodos, métricas e modelos

**Files:**
- Create: `detector/avaliacao.py`, `detector/modelos.py`, `tests/test_modelos.py`

**Interfaces:**
- Consumes: `FEATURES` e `calcular_features` (Tarefa 2), `gerar_transacoes` (Tarefa 1).
- Produces (`detector/avaliacao.py`):
  - `DIAS_AQUECIMENTO = 30`.
  - `dividir_periodos(datas: pd.Series, inicio: str = INICIO) -> dict[str, np.ndarray]`: máscaras booleanas `"aquecimento"`, `"treino"`, `"validacao"`, `"teste"`. Dia `d = (data.normalize() − inicio).days + 1`. Com `n` = número de dias depois do aquecimento (90 para 120 dias): treino = os primeiros `round(0,64·n)` dias, validação = os `round(0,16·n)` seguintes, teste = o resto. Para 120 dias: treino 31–88, validação 89–102, teste 103–120.
  - `melhor_limiar(y: np.ndarray, pontuacao: np.ndarray) -> float`: o limiar de `precision_recall_curve` que maximiza o F1; se `y` não tiver fraude, devolve `1.0`.
  - `metricas(y, pontuacao, limiar) -> dict` com `precisao`, `recall`, `f1`, `pr_auc` (`average_precision_score`), `acuracia`, `alertas`; alerta = `pontuacao >= limiar`; sem fraude, `recall` e `pr_auc` são `None`.
- Produces (`detector/modelos.py`):
  - `ORDEM_SIMPLICIDADE = ("regressao_logistica", "random_forest", "gradient_boosting")`, `MARGEM_EMPATE = 0.02`, `FRACAO_ANOMALIAS = 0.02`.
  - `criar_classificadores() -> dict[str, Pipeline]`: cada um `Pipeline([("escala", StandardScaler()), ("modelo", ...)])` com `LogisticRegression(max_iter=1000, random_state=42)`, `RandomForestClassifier(n_estimators=300, random_state=42, n_jobs=-1)`, `HistGradientBoostingClassifier(random_state=42)`.
  - `escolher_modelo(f1_validacao: dict[str, float]) -> str`: o mais simples (pela `ORDEM_SIMPLICIDADE`) entre os que ficam até `MARGEM_EMPATE` abaixo do maior F1.
  - `para_escala(valor: np.ndarray, limiar: float) -> np.ndarray`: `50·v/limiar` abaixo do limiar, `50 + 50·(v − limiar)/(1 − limiar)` a partir dele, limitado a [0, 100].
  - `@dataclass Detector`: `classificadores: dict[str, Pipeline]`, `limiares: dict[str, float]`, `escolhido: str`, `isolation: IsolationForest`, `limiar_anomalia: float`.
    - `Detector.pontuacoes(X: pd.DataFrame) -> pd.DataFrame` com colunas `prob` (do escolhido), `anomalia_bruta` (`−score_samples`).
    - `Detector.avaliar(X: pd.DataFrame) -> pd.DataFrame` com `prob`, `risco`, `anomalia`, `alerta_classificador`, `alerta_anomalia`, `nivel` (`"Alto risco"`, `"Atenção"`, `"Normal"`, pela tabela da spec, seção 5).
  - `treinar(X_treino, y_treino, X_val, y_val) -> tuple[Detector, dict]`: treina os 3 e o `IsolationForest(random_state=42)` (este só com `X_treino`, sem rótulos), escolhe limiares e o modelo só com a validação, e devolve também `{"validacao": {nome: metricas}}` incluindo `"isolation_forest"`. O limiar do Isolation Forest é o quantil `1 − FRACAO_ANOMALIAS` de `anomalia_bruta` na validação. Recebe só treino e validação: o teste não entra.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_periodos_de_120_dias():
    datas = pd.Series(pd.date_range("2026-01-01", periods=120 * 24, freq="h"))
    p = dividir_periodos(datas)
    dias = lambda m: sorted({d.day_of_year for d in datas[m]})
    assert dias(p["treino"])[0] == 31 and dias(p["treino"])[-1] == 88
    assert dias(p["validacao"]) == list(range(89, 103)) and dias(p["teste"])[-1] == 120
    assert not (p["treino"] & p["teste"]).any() and sum(m.sum() for m in p.values()) == len(datas)

@pytest.mark.parametrize("f1,esperado", [
    ({"regressao_logistica": .70, "random_forest": .71, "gradient_boosting": .80}, "gradient_boosting"),
    ({"regressao_logistica": .79, "random_forest": .80, "gradient_boosting": .81}, "regressao_logistica"),
    ({"regressao_logistica": .50, "random_forest": .795, "gradient_boosting": .81}, "random_forest"),
])
def test_regra_de_escolha(f1, esperado):
    assert escolher_modelo(f1) == esperado

def test_escala_poe_o_limiar_em_50():
    assert para_escala(np.array([0, .2, .6, 1, 3]), .2).tolist() == [0, 50, 75, 100, 100]

def test_niveis_seguem_a_tabela():
    # Detector com pontuações forçadas: (alerta, normal) → Alto risco; (normal, alerta) → Atenção; (normal, normal) → Normal

def test_limiar_maximiza_f1():
    y = np.array([0, 0, 1, 1]); s = np.array([.1, .4, .35, .8])
    assert melhor_limiar(y, s) == .35

def test_isolation_forest_nao_usa_rotulos():
    a, _ = treinar(X_tr, y_tr, X_val, y_val); b, _ = treinar(X_tr, 1 - y_tr, X_val, y_val)
    np.testing.assert_allclose(a.pontuacoes(X_val).anomalia_bruta, b.pontuacoes(X_val).anomalia_bruta)

def test_treino_pequeno_de_ponta_a_ponta():
    det, res = treinar(...)   # gerar_transacoes(80, 120) → features → períodos
    assert det.escolhido in ORDEM_SIMPLICIDADE and set(res["validacao"]) == {*ORDEM_SIMPLICIDADE, "isolation_forest"}
    assert set(det.avaliar(X_val).nivel) <= {"Alto risco", "Atenção", "Normal"}
```

- [ ] **Step 2: Confirmar a falha**

Run: `python -m pytest tests/test_modelos.py -q`
Expected: FAIL com `ModuleNotFoundError`.

- [ ] **Step 3: Implementar `detector/avaliacao.py` e `detector/modelos.py`**

- [ ] **Step 4: Confirmar que passa**

Run: `python -m pytest -q`
Expected: todos passam.

- [ ] **Step 5: Commit**

```bash
git add detector/avaliacao.py detector/modelos.py tests/test_modelos.py
git commit -m "Treina os classificadores e o Isolation Forest com validacao por tempo"
```

---

### Task 4: Motivos, entrada, artefactos e avaliação de transações novas

**Files:**
- Create: `detector/explicacao.py`, `detector/entrada.py`, `detector/artefatos.py`, `detector/servico.py`, `tests/test_servico.py`

**Interfaces:**
- Consumes: Tarefas 1 a 3.
- Produces (`detector/explicacao.py`):
  - `formatar_mt(valor: float) -> str` (`1450.0` → `"1.450 MT"`; `1450.5` → `"1.450,50 MT"`), `formatar_decimal(x: float, casas: int = 1) -> str` (`8.2` → `"8,2"`).
  - `motivos(transacao: dict, features: dict, perfil: dict | None) -> list[str]`, por esta ordem e só quando a condição vale:

| Condição | Texto |
|---|---|
| `valor_vs_habitual >= 3` | `"Valor {x} vezes o habitual deste cliente (habitual: {mediana})"` |
| `pais_novo` e `pais != "MZ"` e o cliente nunca saiu de MZ | `"Primeira compra deste cliente fora de Moçambique ({pais})"` |
| `pais_novo` noutros casos | `"Primeira compra deste cliente em {pais}"` |
| `cidade_nova` | `"Primeira compra deste cliente em {cidade}"` (cidade com só a inicial maiúscula) |
| `categoria_nova` | `"Primeira compra deste cliente na categoria {categoria}"` (minúsculas, `_` → espaço) |
| `hora_incomum >= 0.9` | `"Compra às {h}h; este cliente quase nunca compra a esta hora"` |
| `compras_ultima_hora >= 3` | `"{n+1} compras na última hora; o normal deste cliente é {f} por dia"` (sem perfil: só `"{n+1} compras na última hora"`) |
| `historico_curto` | `"Cliente com pouco histórico: a avaliação é menos confiável"` |

- Produces (`detector/entrada.py`):
  - `COLUNAS = ["cliente", "data_hora", "valor", "cidade", "pais", "categoria"]`, `MAX_BYTES = 5 * 1024 * 1024`.
  - `ler_csv(conteudo: bytes) -> pd.DataFrame`: aceita `,` ou `;` (deteção pela primeira linha), UTF-8 com ou sem BOM, vírgula decimal quando o separador é `;`; `id_transacao` opcional (gerado como `L1`, `L2`… quando falta). Lança `ValueError` em português: ficheiro acima de `MAX_BYTES`, colunas em falta (lista), ou linha inválida (`"Linha {n}: ..."`, contando o cabeçalho como linha 1).
  - `validar(df: pd.DataFrame) -> pd.DataFrame`: aceita datas ISO e `dd/mm/aaaa hh:mm`; recusa valor ≤ 0 ou não numérico, data inválida e campos vazios, com `ValueError("Linha {n}: ...")`. Usada também pelo formulário (com uma linha).
- Produces (`detector/artefatos.py`):
  - `@dataclass Artefatos`: `detector: Detector`, `historico: pd.DataFrame`, `teste_avaliado: pd.DataFrame`, `resultados: dict`, `pasta: Path`.
  - `FICHEIROS = ("detector.joblib", "historico.joblib", "teste_avaliado.joblib", "resultados.json")`.
  - `gravar(pasta: Path, artefatos: Artefatos) -> None` e `carregar(pasta: Path) -> Artefatos | None` (`None` se faltar algum ficheiro de `FICHEIROS` ou se algum não puder ser lido).
- Produces (`detector/servico.py`):
  - `avaliar_transacoes(artefatos: Artefatos, transacoes: pd.DataFrame) -> pd.DataFrame`: recebe transações já validadas, calcula as features com `historico=artefatos.historico`, aplica `Detector.avaliar` e devolve as colunas de entrada mais `risco`, `anomalia`, `nivel`, `motivos` (lista) e `historico_curto`, na ordem original.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_formatos_em_portugues():
    assert formatar_mt(1450) == "1.450 MT" and formatar_mt(1450.5) == "1.450,50 MT" and formatar_decimal(8.24) == "8,2"

def test_motivo_de_valor_alto():
    m = motivos({"pais": "MZ", ...}, {"valor_vs_habitual": 8.2, ...}, {"valor_mediana": 1450, ...})
    assert m[0] == "Valor 8,2 vezes o habitual deste cliente (habitual: 1.450 MT)"

def test_motivo_de_primeira_compra_no_estrangeiro():
    assert "Primeira compra deste cliente fora de Moçambique (AE)" in motivos(...)

def test_csv_do_excel_com_ponto_e_virgula_e_virgula_decimal():
    bruto = "cliente;data_hora;valor;cidade;pais;categoria\nC0001;01/05/2026 14:30;1.450,50;Maputo;MZ;Mercearia\n"
    df = validar(ler_csv(bruto.encode("utf-8-sig")))
    assert df.valor.iloc[0] == 1450.5 and df.data_hora.iloc[0] == pd.Timestamp("2026-05-01 14:30")

def test_erros_indicam_a_linha_e_as_colunas():
    with pytest.raises(ValueError, match="cidade"): ler_csv(b"cliente,data_hora,valor,pais,categoria\n...")
    with pytest.raises(ValueError, match="Linha 3"): validar(ler_csv(csv_com_valor_negativo_na_segunda_linha))
    with pytest.raises(ValueError, match="Linha 2"): validar(ler_csv(csv_com_data_ontem))

def test_artefatos_incompletos_devolvem_none(tmp_path):
    assert carregar(tmp_path) is None

def test_ida_e_volta_dos_artefatos(tmp_path, artefatos_pequenos):
    gravar(tmp_path, artefatos_pequenos)
    assert carregar(tmp_path).detector.escolhido == artefatos_pequenos.detector.escolhido

def test_data_anterior_ao_fim_do_historico_usa_so_o_passado(artefatos_pequenos):
    # transação de C0001 a meio do histórico: igual às features calculadas só com as compras anteriores

def test_cliente_novo_em_rajada_conta_a_propria_sequencia(artefatos_pequenos):
    r = avaliar_transacoes(art, cinco_compras_em_10_minutos_de_cliente_novo)
    assert r.historico_curto.all() and "5 compras na última hora" in r.motivos.iloc[-1]

def test_valor_enorme_e_cidade_nunca_vista_nao_quebram(artefatos_pequenos):
    r = avaliar_transacoes(art, transacao(valor=1e9, cidade="LICHINGA", categoria="JOIAS"))
    assert 0 <= r.risco.iloc[0] <= 100
```

`artefatos_pequenos` é uma fixture de `tests/conftest.py` (`scope="session"`) que gera `gerar_transacoes(80, 120)`, calcula features, treina com `treinar` e monta `Artefatos` com `teste_avaliado` vazio e `resultados = {}`.

- [ ] **Step 2: Confirmar a falha**

Run: `python -m pytest tests/test_servico.py -q`
Expected: FAIL com `ModuleNotFoundError`.

- [ ] **Step 3: Implementar os quatro módulos**

- [ ] **Step 4: Confirmar que passa**

Run: `python -m pytest -q`
Expected: todos passam.

- [ ] **Step 5: Commit**

```bash
git add detector tests
git commit -m "Explica os alertas e avalia transacoes novas"
```

---

### Task 5: Treino completo, resultados e relatório

**Files:**
- Create: `treinar.py`, `tests/test_treinar.py`
- Output (ignorados pelo git): `data/transacoes.csv`, `modelos/`. Versionado: `RELATORIO.md`.

**Interfaces:**
- Consumes: Tarefas 1 a 4.
- Produces (`treinar.py`):
  - `executar(pasta_modelos: Path = Path("modelos"), pasta_dados: Path = Path("data"), relatorio: Path | None = Path("RELATORIO.md"), n_clientes: int = 300, dias: int = 120, semente: int = 42) -> dict` que devolve os resultados e grava os `Artefatos` (Tarefa 4), `data/transacoes.csv`, os gráficos e o relatório.
  - `resultados.json` com as chaves: `gerado_em`, `tempo_segundos`, `dados` (`transacoes`, `clientes`, `fraudes`, `por_tipo`), `periodos` (por período: `inicio`, `fim`, `transacoes`, `fraudes`), `modelos` (por nome dos 3 e `isolation_forest`: `limiar`, `validacao`, `teste`), `escolhido`, `motivo_escolha` (frase), `matriz_confusao` (`[[vn, fp], [fn, vp]]` do escolhido no teste), `recall_por_tipo` (`{"escolhido": {...}, "isolation_forest": {...}}`), `ganho_isolation` (fraudes do teste apanhadas só pelo Isolation Forest), `importancia` (lista de `{"feature", "valor"}` por ordem decrescente), `divisao_aleatoria` (`f1_tempo`, `f1_aleatoria`), `aviso_gerador_facil` (bool), `atalhos` (PR AUC no teste de cada coluna bruta `valor`, `hora`, `pais`, `categoria`, `cidade`).
  - Gráficos em `modelos/graficos/`: `matriz_confusao.png`, `importancia.png`, `comparacao.png` (F1 na validação e no teste por modelo), com `matplotlib.use("Agg")`, títulos e eixos em português.
  - `teste_avaliado`: as transações do teste com `risco`, `anomalia`, `nivel` e `motivos`, para a página do perfil.

**Cálculos (spec, seção 6):**
- **Importância:** `permutation_importance(pipeline, X_val, y_val, scoring="average_precision", n_repeats=5, random_state=42)`.
- **Divisão aleatória:** a mesma classe do escolhido, treinada com `train_test_split(..., test_size=0.2, random_state=42)` sobre as linhas dos dias 31 a 120; o limiar vem de um segundo `train_test_split(test_size=0.2, random_state=42)` dentro do treino; F1 medido no 20% de teste.
- **PR AUC das colunas brutas:** para `valor` e `hora`, a própria coluna como pontuação (e o seu inverso, ficando com a maior); para `pais`, `categoria`, `cidade`, a taxa de fraude da categoria no treino aplicada ao teste.
- **Aviso de gerador fácil:** F1 do escolhido no teste acima de 0,98.

**`RELATORIO.md`:** seções Dados, Períodos, Modelos (tabela validação e teste), Modelo escolhido e porquê, Matriz de confusão, Recall por tipo de fraude, Isolation Forest, Importância das features, Divisão aleatória contra divisão por tempo, Limitações (spec, seção 11), com os gráficos por caminho relativo (`modelos/graficos/...`) e a nota da acurácia.

- [ ] **Step 1: Escrever os testes que falham**

```python
@pytest.fixture(scope="module")
def resultado(tmp_path_factory):
    raiz = tmp_path_factory.mktemp("treino")
    return executar(raiz / "modelos", raiz / "data", raiz / "RELATORIO.md", n_clientes=120), raiz

def test_grava_tudo_o_que_a_interface_precisa(resultado):
    res, raiz = resultado
    assert carregar(raiz / "modelos") is not None
    assert {p.name for p in (raiz / "modelos" / "graficos").iterdir()} == {"matriz_confusao.png", "importancia.png", "comparacao.png"}

def test_teste_nao_escolhe_nada(resultado):
    res, _ = resultado
    f1_val = {n: res["modelos"][n]["validacao"]["f1"] for n in ORDEM_SIMPLICIDADE}
    assert res["escolhido"] == escolher_modelo(f1_val)

def test_nenhuma_coluna_bruta_e_um_atalho(resultado):
    res, _ = resultado
    pr_modelo = res["modelos"][res["escolhido"]]["teste"]["pr_auc"]
    assert all(v < pr_modelo / 2 for v in res["atalhos"].values()), res["atalhos"]

def test_relatorio_tem_as_secoes(resultado):
    texto = (resultado[1] / "RELATORIO.md").read_text(encoding="utf-8")
    for titulo in ("Modelo escolhido", "Matriz de confusão", "Recall por tipo de fraude", "Divisão aleatória", "Limitações"):
        assert f"## {titulo}" in texto
    assert "acurácia" in texto.lower()
```

- [ ] **Step 2: Confirmar a falha**

Run: `python -m pytest tests/test_treinar.py -q`
Expected: FAIL com `ModuleNotFoundError: treinar`.

- [ ] **Step 3: Implementar `treinar.py`**

`main()` chama `executar()` e imprime o modelo escolhido, o F1 no teste e o tempo total.

- [ ] **Step 4: Confirmar que passa**

Run: `python -m pytest -q`
Expected: todos passam. Se `test_nenhuma_coluna_bruta_e_um_atalho` falhar, ajustar o gerador (Tarefa 1) até passar, sem mexer nos modelos, e registar no ledger qual coluna era o atalho e o que mudou.

- [ ] **Step 5: Executar com o tamanho completo**

Run: `python treinar.py`
Expected: termina em menos de 60 s; imprime o escolhido e o F1. Ler o `RELATORIO.md` inteiro e conferir os números com `modelos/resultados.json`. Se `aviso_gerador_facil` for verdadeiro, o relatório já o declara; não ajustar o gerador olhando o teste.

- [ ] **Step 6: Commit**

```bash
git add treinar.py tests/test_treinar.py RELATORIO.md detector
git commit -m "Treina, avalia e gera o relatorio"
```

---

### Task 6: Interface — base e Testar transação

**Files:**
- Create: `app.py`, `templates/base.html`, `templates/testar.html`, `templates/sem_modelos.html`, `static/estilo.css`, `detector/exemplos.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `carregar`, `avaliar_transacoes`, `validar`, `formatar_mt` (Tarefa 4).
- Produces:
  - `app.py`: `criar_app(pasta_modelos: Path = Path("modelos")) -> Flask`; `app = criar_app()`; `if __name__ == "__main__": app.run(host="127.0.0.1", port=5000)`. Os artefactos são carregados na criação; se `carregar` devolver `None`, todas as rotas respondem 503 com `sem_modelos.html` ("Execute `python treinar.py` para gerar os modelos").
  - Rotas: `GET /` (formulário), `POST /` (resultado). Campos: `cliente`, `data_hora` (`datetime-local`), `valor`, `cidade`, `pais`, `categoria`; botões `exemplo=<nome>`.
  - `detector/exemplos.py`: `NOMES = ("normal", "valor_alto", "pais_estranho", "madrugada", "conta_tomada", "rajada")`; `criar_exemplos(historico: pd.DataFrame) -> dict[str, list[dict]]`. Usa o cliente legítimo com mais compras; data = um dia depois da última transação do histórico, na hora de pico dele; `rajada` tem 5 compras pequenas com 2 minutos de intervalo; os outros têm uma compra.
  - `base.html`: cabeçalho com "Testar transação · Analisar ficheiro · Perfil do cliente · Modelos" e o aviso "Dados sintéticos, gerados para a disciplina; os resultados não valem para fraudes reais".
  - Resultado: nível (cor por nível), risco e anomalia de 0 a 100, motivos e, para a rajada, uma linha por compra.
  - CSS: largura máxima e layout que funciona a 390 px, cores com contraste, foco visível.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_sem_modelos_mostra_a_instrucao(tmp_path):
    r = criar_app(tmp_path).test_client().get("/")
    assert r.status_code == 503 and "python treinar.py" in r.get_data(as_text=True)

def test_pagina_inicial_tem_as_abas_e_o_aviso(cliente):
    html = cliente.get("/").get_data(as_text=True)
    for t in ("Testar transação", "Analisar ficheiro", "Perfil do cliente", "Modelos", "Dados sintéticos"):
        assert t in html

@pytest.mark.parametrize("nome", NOMES)
def test_exemplos_respondem(cliente, nome):
    assert cliente.post("/", data={"exemplo": nome}).status_code == 200

def test_valor_alto_gera_motivo_com_numero(cliente):
    assert "vezes o habitual" in cliente.post("/", data={"exemplo": "valor_alto"}).get_data(as_text=True)

def test_formulario_invalido_mostra_o_erro(cliente):
    r = cliente.post("/", data={... "valor": "-5" ...})
    assert r.status_code == 400 and "valor" in r.get_data(as_text=True).lower()

def test_texto_do_utilizador_e_escapado(cliente):
    r = cliente.post("/", data={... "cidade": "<script>x</script>" ...})
    assert "<script>x</script>" not in r.get_data(as_text=True)
```

`cliente` é uma fixture de `tests/conftest.py` que grava `artefatos_pequenos` em `tmp_path` e cria `criar_app(...)`.

- [ ] **Step 2: Confirmar a falha**

Run: `python -m pytest tests/test_app.py -q`
Expected: FAIL com `ModuleNotFoundError: app`.

- [ ] **Step 3: Implementar**

- [ ] **Step 4: Confirmar que passa**

Run: `python -m pytest -q`
Expected: todos passam.

- [ ] **Step 5: Commit**

```bash
git add app.py templates static detector/exemplos.py tests
git commit -m "Adiciona a interface e a pagina de testar transacao"
```

---

### Task 7: Interface — Analisar ficheiro, Perfil do cliente e Modelos

**Files:**
- Create: `templates/analisar.html`, `templates/perfil.html`, `templates/modelos.html`
- Modify: `app.py`, `static/estilo.css`, `tests/test_app.py`

**Interfaces:**
- Consumes: `ler_csv`, `validar`, `avaliar_transacoes`, `perfil_cliente`, `Artefatos.resultados`, `Artefatos.teste_avaliado`.
- Produces (rotas em `app.py`):
  - `GET /analisar` (formulário de envio); `POST /analisar` (ficheiro `ficheiro`): tabela ordenada por risco, com filtro "só alertas" (`?alertas=1` via campo escondido, ou JavaScript simples que esconde linhas) e link de download. Erros de `ler_csv`/`validar` aparecem na página com estado 400. `app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES + 64 * 1024`, e 413 mostra "O ficheiro passa de 5 MB".
  - Download: o resultado fica num dicionário em memória `app.config["RESULTADOS"]` (chave `secrets.token_urlsafe(16)`, até 20 entradas, a mais antiga sai primeiro); `GET /analisar/<chave>.csv` devolve CSV com `;`, vírgula decimal e UTF-8 com BOM; textos que começam por `=`, `+`, `-`, `@` recebem `'` à frente; chave desconhecida dá 404.
  - `GET /perfil?cliente=C0001`: lista de clientes (seletor), o perfil (`perfil_cliente`) com barras por hora feitas em HTML/CSS, e as transações desse cliente em `teste_avaliado` com nível diferente de "Normal", com os motivos. Cliente inexistente: mensagem "Cliente não encontrado" e estado 404.
  - `GET /modelos`: tabela dos 4 modelos (validação e teste), escolhido e `motivo_escolha`, nota da acurácia, recall por tipo, `ganho_isolation`, comparação das divisões, aviso de gerador fácil quando houver, e os três PNG servidos por `GET /graficos/<nome>.png` (só os três nomes conhecidos; outro nome dá 404).

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_analisar_csv_ordena_por_risco(cliente):
    r = cliente.post("/analisar", data={"ficheiro": (io.BytesIO(csv_valido), "t.csv")}, content_type="multipart/form-data")
    assert r.status_code == 200   # e os riscos na tabela estão em ordem decrescente

def test_csv_sem_colunas_mostra_quais_faltam(cliente):
    r = cliente.post(...csv_sem_cidade...)
    assert r.status_code == 400 and "cidade" in r.get_data(as_text=True)

def test_download_neutraliza_formulas(cliente):
    # cidade "=HYPERLINK(...)" → no CSV aparece "'=HYPERLINK"
    chave = re.search(r"/analisar/([\w-]+)\.csv", html).group(1)
    texto = cliente.get(f"/analisar/{chave}.csv").data.decode("utf-8-sig")
    assert "'=HYPERLINK" in texto and texto.splitlines()[0].count(";") >= 6

def test_chave_de_download_desconhecida_da_404(cliente):
    assert cliente.get("/analisar/naoexiste.csv").status_code == 404

def test_ficheiro_grande_demais_e_recusado(cliente):
    r = cliente.post(... 6 MB ...)
    assert r.status_code == 413 and "5 MB" in r.get_data(as_text=True)

def test_perfil_mostra_o_cliente(cliente):
    html = cliente.get("/perfil?cliente=C0001").get_data(as_text=True)
    assert "C0001" in html and "MT" in html
    assert cliente.get("/perfil?cliente=NAO").status_code == 404

def test_modelos_mostra_a_escolha_e_os_graficos(cliente_treinado):
    html = cliente_treinado.get("/modelos").get_data(as_text=True)
    assert "acurácia" in html.lower() and "graficos/matriz_confusao.png" in html
    assert cliente_treinado.get("/graficos/matriz_confusao.png").status_code == 200
    assert cliente_treinado.get("/graficos/../resultados.json").status_code == 404
```

`cliente_treinado` usa uma pasta gerada por `executar(..., n_clientes=120)` (fixture de sessão em `conftest.py`, reaproveitada da Tarefa 5), para existirem resultados e gráficos.

- [ ] **Step 2: Confirmar a falha**

Run: `python -m pytest tests/test_app.py -q`
Expected: FAIL nos testes novos (404 nas rotas).

- [ ] **Step 3: Implementar as rotas e os templates**

- [ ] **Step 4: Confirmar que passa**

Run: `python -m pytest -q`
Expected: todos passam.

- [ ] **Step 5: Commit**

```bash
git add app.py templates static tests
git commit -m "Adiciona as paginas de ficheiro, perfil e modelos"
```

---

### Task 8: README e verificação no navegador

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: tudo.

- [ ] **Step 1: Escrever o `README.md`**

Seções:
- O que o sistema faz e o que não faz.
- Como executar (os quatro comandos da spec, seção 10).
- Estrutura: tabela ficheiro → função.
- **Conceitos da disciplina e onde aparecem:** divisão treino/teste (`detector/avaliacao.py`, `dividir_periodos`, e a comparação com `train_test_split`), normalização (`StandardScaler` em `criar_classificadores`), classificação supervisionada (os 3 classificadores), aprendizado não supervisionado (Isolation Forest), matriz de confusão e métricas (`metricas`, página Modelos), importância das features (`permutation_importance`), previsão de dados novos (`avaliar_transacoes`, páginas Testar e Analisar), exportação dos modelos (`detector/artefatos.py`, joblib).
- Resultados: escolhido e F1 no teste, com link para `RELATORIO.md`.
- Limitações (spec, seção 11).

- [ ] **Step 2: Executar e verificar no Chrome**

Run: `python treinar.py` (se `modelos/` não existir) e `python app.py` em segundo plano.
Verificar no Chrome (skill `claude-in-chrome`): os seis exemplos de Testar, envio de um CSV de 20 linhas tirado de `data/transacoes.csv` com o download, o perfil de um cliente com alertas e a página Modelos com os três gráficos; depois as quatro páginas em 390 px de largura (iframe de 390 px se a janela não redimensionar), sem rolagem horizontal. Encerrar o servidor.
Expected: tudo funciona; anotar no ledger o que foi verificado.

- [ ] **Step 3: Suíte final**

Run: `python -m pytest -q`
Expected: todos passam.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Adiciona o README"
```
