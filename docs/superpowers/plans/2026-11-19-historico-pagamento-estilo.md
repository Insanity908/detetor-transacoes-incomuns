# Histórico importado, pagamentos e estilo C — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** dar histórico opcional a clientes novos (só para o detetor), permitir pagamentos com categoria, local e
hora simulada na área do cliente, e aplicar o estilo visual C a todo o sistema.

**Architecture:** o histórico importado vive numa tabela própria (`historico_importado`, migração 0008) gerida por
`detector/historico.py` e é lido só por `autorizacao.autorizar()`. O pagamento é uma rota nova da área do cliente
sobre `autorizar()`; o campo Local e a data/hora simulada são funções puras partilhadas com a transferência. O
estilo é um `estilo.css` reescrito mais ajustes de templates, sem dependências externas.

**Tech Stack:** Python 3.11+, Flask/Jinja, SQLAlchemy 2, Alembic, SQLite, pandas, pytest.

**Spec:** `docs/superpowers/specs/2026-11-19-historico-pagamento-estilo-design.md` (ler §4 e §5 antes de cada tarefa).

## Global Constraints
- Português de Portugal em interface, mensagens, docstrings e docs.
- Dinheiro em cêntimos inteiros (`dinheiro.para_centavos`); apresentação com o filtro `mtc`.
- `autorizar()` é a única porta para movimentos de saída; o histórico importado nunca cria movimentos,
  `transacoes_avaliadas` nem alertas.
- Histórico importado: datas em `[agora − 90 dias, agora[`, máx. **2 000** linhas por ficheiro.
- Data/hora simulada: `agora − 30 dias ≤ x ≤ agora`, só no modo demonstração.
- Sem scripts inline nem ficheiros externos (CSP `default-src 'self'`); sem bibliotecas CSS/JS novas.
- Mesmas cores: `--tinta #17232e`, `--acao #1f5f8b`, `--alto #b3261e`, `--atencao #8a5a00`, `--normal #2e7d4f`.
- Testes: `.venv/Scripts/python -m pytest -q` (≈ 12 min, 536 testes no início); todos continuam a passar.
- Commits pequenos, terminados com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus
1. CSV do histórico com `;` e vírgula decimal (formato do Excel em português) — aceite como em "Analisar ficheiro"
   (teste na Tarefa 1).
2. Apagar o histórico importado repõe o comportamento de cliente novo e não toca em saldos (teste na Tarefa 2).
3. Valor de `local` adulterado (`EXT:US`, `MZ:LISBOA`, vazio) → 400 com mensagem, nunca 500 (teste na Tarefa 3).
4. Data/hora simulada futura, com mais de 30 dias ou mal formada → 400 com mensagem (teste na Tarefa 3).
5. Pagamento sem saldo ou com conta bloqueada → "Recusada — <motivo>", saldo intacto (teste na Tarefa 4).

---

## Estrutura de ficheiros

| Ficheiro | Responsabilidade |
|---|---|
| `migracoes/versions/0008_historico_importado.py` | tabela `historico_importado` |
| `detector/banco.py` (mod.) | ORM `HistoricoImportado` |
| `detector/historico.py` (novo) | importar CSV, gerar exemplo, resumo, apagar, ler para o detetor |
| `detector/autorizacao.py` (mod.) | junta o histórico importado ao `historico_extra` |
| `detector/web_balcao.py`, `templates/balcao_cliente.html` (mod.) | secção "Histórico anterior" |
| `detector/locais.py` (novo) | opções de Local, nomes de países, `ler_local`, `ler_momento` |
| `detector/web_cliente.py` (mod.) | `/cliente/pagar`; transferência usa `local` |
| `templates/cliente_pagar.html` (novo), `cliente_transferir.html`, `cliente_inicio.html`, `cliente_movimento.html` (mod.) | ecrãs |
| `static/estilo.css` (reescrito), `templates/base.html`, `templates/base_cliente.html` + páginas principais | estilo C |
| `docs/*.md`, `README.md` | documentação |

---

# Parte 1 — Histórico importado

### Task 1: Tabela e módulo do histórico importado

**Files:**
- Create: `migracoes/versions/0008_historico_importado.py`, `detector/historico.py`, `tests/test_historico.py`
- Modify: `detector/banco.py`, `tests/test_migracoes.py`

**Interfaces — produz:**
- ORM `HistoricoImportado` (`historico_importado`): `id`, `cliente_id` (FK `clientes.id`, `ondelete=CASCADE`, index),
  `data_hora: datetime`, `valor: int` (cêntimos, `CHECK > 0`), `cidade: str(100)`, `pais: str(100)`,
  `categoria: str(100)`, `origem: str(10)` (`ficheiro`|`exemplo`), `importado_em: datetime`, `importado_por: str(50)`;
  `UniqueConstraint(cliente_id, data_hora, valor, cidade, pais, categoria)`. Migração `revision="0008"`,
  `down_revision="0007"`, com downgrade.
- `historico.MAX_LINHAS = 2000`, `historico.DIAS_MAXIMOS = 90`.
- `@dataclass ResultadoImportacao: inseridas: int, repetidas: int`.
- `historico.importar(sessoes, cliente_id: int, conteudo: bytes, por: str, agora: datetime | None = None) -> ResultadoImportacao`
  — `entrada.ler_csv`; a coluna `cliente` é opcional (se existir e diferir do código da ficha → `ValueError`);
  valida com as regras de `entrada.validar` (valores, datas, cidades, países, categorias de compra **e**
  `TRANSFERENCIA`/`LEVANTAMENTO`); recusa o ficheiro inteiro com `ErroValidacao`-style lista de erros por linha
  (reutilizar a exceção que `entrada` já usa); limite de linhas antes de retirar repetidas; datas fora de
  `[agora − 90 dias, agora[` são erro; repetidas (no ficheiro ou já guardadas) contam em `repetidas`.
- `historico.gerar_exemplo(sessoes, cliente_id: int, por: str, agora: datetime | None = None) -> int` — ~60 dias de
  compras legítimas coerentes com a ficha (spec §5.4), determinístico pela semente derivada do código; devolve
  inseridas.
- `@dataclass ResumoHistorico: total: int, primeira: datetime, ultima: datetime, categorias: list[str]` (até 3 mais
  usadas); `historico.resumo(sessoes, cliente_id) -> ResumoHistorico | None`.
- `historico.apagar(sessoes, cliente_id) -> int`.
- `historico.para_detetor(sessoes, codigos: set[str], desde: datetime) -> pd.DataFrame` — colunas
  `id_transacao` (`"HIST-<id>"`), `cliente` (código), `data_hora`, `valor` (MT, float), `cidade`, `pais`,
  `categoria`, `fraude` (0); vazio com as mesmas colunas se não houver linhas.

- [ ] **Step 1: Testes que falham** em `tests/test_historico.py` (cliente criado com `clientes.criar`):
  - `test_importar_csv_simples_e_resumo`: CSV com 3 linhas válidas (sem coluna `cliente`) → `inseridas == 3`;
    `resumo(...).total == 3`, `primeira`/`ultima` certas.
  - `test_importar_formato_excel_pt`: `;` como separador e `1.234,50` como valor → aceite, valor 123450 cêntimos
    (**foco 1**).
  - `test_repetidas_sao_ignoradas`: importar o mesmo ficheiro duas vezes → segunda vez `inseridas == 0,
    repetidas == 3`.
  - `test_ficheiro_com_erros_e_recusado_por_inteiro`: uma linha com data futura e outra com 100 dias → erro que
    menciona as duas linhas; nada gravado.
  - `test_coluna_cliente_de_outro_cliente_recusa`.
  - `test_limite_de_linhas`: 2 001 linhas → erro.
  - `test_gerar_exemplo_coerente_e_deterministico`: insere > 30 linhas, todas em `[agora − 90 d, agora[`, cidade da
    ficha, país `MZ`; segunda chamada noutra BD com o mesmo cliente dá as mesmas linhas.
  - `test_para_detetor_formato` e `test_apagar`.
  - Migração: em `tests/test_migracoes.py`, `test_migracao_0008_cria_e_remove` (padrão da 0006).
- [ ] **Step 2:** `.venv/Scripts/python -m pytest tests/test_historico.py tests/test_migracoes.py -q` → FAIL.
- [ ] **Step 3:** implementar (migração, ORM, módulo).
- [ ] **Step 4:** testes focados → PASS; suite completa → PASS.
- [ ] **Step 5:** commit `"Historico importado: tabela, importacao CSV, exemplo e leitura para o detetor"`.

### Task 2: Detetor e balcão usam o histórico importado

**Files:**
- Modify: `detector/autorizacao.py:~127`, `detector/web_balcao.py`, `templates/balcao_cliente.html`,
  `detector/auditoria.py` (ações `historico_importado`, `historico_apagado` com rótulos)
- Test: `tests/test_historico.py`, `tests/test_balcao.py`

**Interfaces — consome:** Task 1 (`importar`, `gerar_exemplo`, `resumo`, `apagar`, `para_detetor`).
**Produz:** rotas `POST /balcao/clientes/<id>/historico` (campo de ficheiro `ficheiro`),
`POST /balcao/clientes/<id>/historico/exemplo`, `POST /balcao/clientes/<id>/historico/apagar`, todas com
`exige_area("balcao")`, registadas em `ENDPOINTS` e `SEM_MODELOS`; auditoria com `alvo` = código e `detalhe` com as
contagens.

- [ ] **Step 1: Testes que falham:**
  - `test_autorizar_usa_historico_importado`: cliente novo com `gerar_exemplo` → um pagamento normal pela
    `autorizar()` (modelo real de `pasta_pequena`) tem `TransacaoAvaliada.historico_curto is False`; sem histórico
    é `True`.
  - `test_historico_nao_contamina`: depois de importar, contagens de `TransacaoAvaliada`, `Alerta` e `Movimento`
    inalteradas; `verificar_saldos == []`; o extrato do cliente (`/cliente/`) não mostra linhas importadas.
  - `test_apagar_repoe_cliente_novo` (**foco 2**): após `apagar`, o pagamento seguinte volta a ter
    `historico_curto is True`; saldo igual ao de antes.
  - Balcão (`tests/test_balcao.py`): importar pelo formulário → 302 e a ficha mostra "Histórico importado: N
    transações"; ficheiro inválido → 400 com a lista de erros; "Gerar exemplo" e "Apagar" funcionam; analista → 403.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** em `autorizar()`, concatenar `historico.para_detetor(sessoes, set(df["cliente"]), desde)` ao
  `historico_api(...)` antes de `avaliar_transacoes`; secção "Histórico anterior" na ficha (resumo ou "Sem histórico
  importado — o detetor trata-o como cliente novo.", formulário `enctype="multipart/form-data"` com CSRF, botões).
- [ ] **Step 4:** focados → PASS; suite completa → PASS.
- [ ] **Step 5:** commit `"Historico importado: usado pelo detetor e gerido no balcao"`.

---

# Parte 2 — Pagamento com categoria e local

### Task 3: Local e data/hora simulada (transferência)

**Files:**
- Create: `detector/locais.py`, `tests/test_locais.py`
- Modify: `detector/web_cliente.py` (transferência), `templates/cliente_transferir.html`,
  `tests/test_area_cliente.py`, `tests/test_guiao.py` (passam a enviar `local`)

**Interfaces — produz** (em `detector/locais.py`):
- `NOMES_PAIS = {"MZ": "Moçambique", "NG": "Nigéria", "AE": "Emirados Árabes Unidos", "CN": "China", "BR": "Brasil",
  "IN": "Índia", "ZW": "Zimbabwe", "MW": "Malawi", "TZ": "Tanzânia"}`.
- `opcoes_local() -> list[tuple[str, list[tuple[str, str]]]]` — `[("Moçambique", [("MZ:MAPUTO", "Maputo"), …]),
  ("Estrangeiro", [("EXT:NG", "Nigéria"), …])]` (cidades de `gerador.CIDADES`, países de `PAISES_ESTRANHOS`).
- `ler_local(valor: str, cidade_cliente: str) -> tuple[str, str]` — `"MZ:<CIDADE>"` → `(CIDADE, "MZ")`;
  `"EXT:<PAÍS>"` → `(cidade_cliente, PAÍS)`; outro → `ValueError("Escolha um local da lista.")`.
- `descrever_local(cidade: str, pais: str) -> str` — `"Tete"` para MZ, `"Nigéria"` para estrangeiro.
- `DIAS_SIMULACAO = 30`; `ler_momento(data: str, hora: str, agora: datetime) -> datetime` — ambos vazios → `agora`;
  formato `AAAA-MM-DD` + `HH:MM`; fora de `[agora − 30 dias, agora]` → `ValueError` com mensagem
  ("A data simulada não pode ser futura." / "… no máximo 30 dias atrás.").
- Transferência: campos `local`, `data`, `hora` (só lidos com `modo_demonstracao`); remove `cidade`/`pais`.

- [ ] **Step 1: Testes que falham:**
  - `tests/test_locais.py`: `ler_local("MZ:TETE", "MAPUTO") == ("TETE", "MZ")`; `("EXT:NG", "MAPUTO") ==
    ("MAPUTO", "NG")`; `"EXT:US"`, `"MZ:LISBOA"`, `""` → ValueError; `ler_momento` aceita agora e agora−29d,
    recusa futuro, agora−31d e `"31/12/2026"`.
  - `tests/test_area_cliente.py`: transferência com `local="EXT:NG"` em modo demonstração grava movimento com
    `pais == "NG"`; `local="EXT:US"` → 400 com "Escolha um local da lista." (**foco 3**); `data` futura → 400
    (**foco 4**); fora do modo demonstração `local`/`data` são ignorados (cidade do cliente, momento ≈ agora).
  - Atualizar os testes existentes que enviavam `cidade`/`pais` para `local` (incluindo `tests/test_guiao.py`).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** implementar `locais.py` e adaptar a transferência (um `<select name="local">` com `<optgroup>`,
  `<input type="date" name="data">`, `<input type="time" name="hora">`, dentro do quadro "Simular").
- [ ] **Step 4:** focados → PASS; suite completa → PASS.
- [ ] **Step 5:** commit `"Area do cliente: campo Local unico e data/hora simulada"`.

### Task 4: Pagamento

**Files:**
- Create: `templates/cliente_pagar.html`
- Modify: `detector/web_cliente.py`, `templates/cliente_inicio.html`, `templates/cliente_movimento.html`,
  `detector/auditoria.py` (ação `pagamento_cliente`), `app.py` (endpoint em `SEM_MODELOS` se aplicável)
- Test: `tests/test_area_cliente.py`, `tests/test_perfis.py`

**Interfaces — consome:** Task 3 (`opcoes_local`, `ler_local`, `ler_momento`, `descrever_local`); `autorizar`,
`PedidoDebito`. **Produz:** `GET|POST /cliente/pagar`, campos `categoria` (∈ `gerador.CATEGORIAS`), `comerciante`
(1–140 caracteres), `valor`, e no modo demonstração `local`, `data`, `hora`;
`PedidoDebito(tipo="compra", categoria=<escolhida>, canal="cliente", descricao=<comerciante>, ...)`; sem modelos →
503; PRG (303 para `/cliente/movimentos/<id>`); `data-envio-unico`; auditoria `pagamento_cliente`
(alvo = código; detalhe: categoria, comerciante, valor, estado).

- [ ] **Step 1: Testes que falham:**
  - `test_pagamento_normal_autorizado`: nível forçado Normal → 303, "Concluída", saldo desce, movimento
    `tipo == "compra"`, categoria e comerciante no detalhe ("Eletrónica · Loja X"); `verificar_saldos == []`.
  - `test_pagamento_suspeito_fica_retido_com_motivo`: modo demonstração, Eletrónica, `local="EXT:NG"`, 03:00 de
    ontem, nível forçado Alto risco → "Em verificação"; o movimento tem `momento` = data/hora simulada e `pais == "NG"`.
  - `test_pagamento_sem_saldo_e_conta_bloqueada` (**foco 5**): "Recusada — Saldo insuficiente." e
    "Recusada — A conta está bloqueada."; saldo intacto.
  - `test_pagamento_categoria_invalida_400` e `test_comerciante_vazio_ou_longo_400`.
  - `test_inicio_tem_botoes_pagar_e_transferir`.
  - `tests/test_perfis.py::test_features_ignoram_transacoes_posteriores`: com `calcular_features`, uma transação
    datada antes de outras três seguidas não as conta em `compras_ultima_hora`/`compras_24h` (spec §5.4).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** implementar a rota (mesmo padrão de `/cliente/transferir`), o template e os botões no início.
- [ ] **Step 4:** focados → PASS; suite completa → PASS.
- [ ] **Step 5:** commit `"Area do cliente: pagamentos com categoria, comerciante, local e hora"`.

---

# Parte 3 — Estilo C

### Task 5: Base do estilo C

**Files:**
- Modify: `static/estilo.css` (reescrito), `templates/base.html`, `templates/base_cliente.html`
- Test: `tests/test_app.py` (ou novo `tests/test_estilo.py`)

**Interfaces — produz** (classes que a Tarefa 6 usa): `.faixa` (cabeçalho com gradiente `--tinta → --acao`),
`.nav-pilulas` (+ `[aria-current=page]`), `.conteudo-sobreposto` (margem negativa sobre a faixa), `.cartao`,
`.kpis` / `.kpi` (`.kpi-alto`, `.kpi-normal`), `.lista` / `.lista-item` / `.lista-icone` (`.icone-alto`,
`.icone-atencao`, `.icone-normal`), `.tabela-cartao`, `.etiqueta` (`.nivel-alto|atencao|normal`), avisos
existentes (`.aviso-*`, `.erro`) restilizados. Tokens em `:root` com as cores dos Global Constraints, mais
`--raio`, `--sombra`, `--sombra-forte`. Responsivo ≤ 720 px; `prefers-reduced-motion`.

- [ ] **Step 1: Testes que falham** (`tests/test_estilo.py`):
  - `test_css_tem_tokens_e_classes`: `estilo.css` contém as 5 cores exatas e as classes acima.
  - `test_paginas_sem_scripts_inline_nem_recursos_externos`: para `/entrar`, `/alertas`, `/painel`,
    `/balcao/clientes`, `/cliente/entrar`, `/cliente/`: nenhum `<script>` sem `src`, nenhum `src`/`href` que comece
    por `http`, e a resposta mantém o cabeçalho CSP atual.
  - `test_base_usa_faixa_e_pilulas`: `/alertas` (admin) e `/cliente/` contêm `class="faixa"` e `nav-pilulas`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** reescrever o CSS por camadas (tokens, base, faixa/navegação, cartões, KPIs, listas, tabelas,
  formulários, avisos, etiquetas, responsivo) e aplicar a faixa/pílulas nos dois `base*.html`; ícones por caracteres
  Unicode ou SVG inline (sem ficheiros externos).
- [ ] **Step 4:** focados → PASS; suite completa → PASS (ajustar só asserções de marcação, nunca de comportamento).
- [ ] **Step 5:** commit `"Estilo C: tokens, faixa, navegacao em pilulas e componentes base"`.

### Task 6: Páginas principais no estilo C

**Files:**
- Modify: `templates/alertas.html`, `alerta.html`, `painel.html`, `balcao_clientes.html`, `balcao_cliente.html`,
  `cliente_inicio.html`, `cliente_pagar.html`, `cliente_transferir.html`, `cliente_movimento.html`, `entrar.html`,
  `cliente_entrar.html`
- Test: `tests/test_estilo.py`, testes existentes de interface

**Interfaces — consome:** classes da Tarefa 5.

- [ ] **Step 1: Testes que falham:**
  - `/painel` mostra `.kpis` com os cartões existentes (novos, alto risco, valor retido, valor protegido…).
  - `/alertas` lista alertas como `.lista-item` com `.lista-icone` e etiqueta de nível; ainda tem os filtros.
  - `/cliente/` mostra o saldo num `.kpi` e os movimentos como `.lista-item`; `/balcao/clientes` como lista.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** reestruturar só a marcação/classes destas páginas (mesmos dados, mesmos formulários e rotas);
  tabelas mantêm-se em análises/auditoria dentro de `.tabela-cartao`.
- [ ] **Step 4:** suite completa → PASS; `tests/test_guiao.py` passa sem alterações de comportamento.
- [ ] **Step 5:** commit `"Estilo C: paginas de alertas, painel, balcao e area do cliente"`.

### Task 7: Documentação

**Files:** Modify `docs/manual_utilizador.md` (histórico anterior no balcão; pagamento e Local na área do cliente),
`docs/manual_administracao.md` (tabela 0008; limites 90 dias / 2 000 linhas / 30 dias), `docs/guiao_demonstracao.md`
(passo opcional: gerar histórico de exemplo antes; mostrar um pagamento de Eletrónica às 03:00 no estrangeiro),
`README.md` (resumo).

- [ ] **Step 1:** escrever, conferindo cada afirmação com o código (rotas, rótulos, limites).
- [ ] **Step 2:** suite completa → PASS.
- [ ] **Step 3:** commit `"Documentacao: historico anterior, pagamentos e novo estilo"`.
