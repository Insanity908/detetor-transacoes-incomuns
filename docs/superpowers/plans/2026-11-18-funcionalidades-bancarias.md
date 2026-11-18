# Funcionalidades bancárias — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Objetivo:** juntar ao detetor um banco de demonstração — fichas de clientes, contas com saldo, balcão, área do
cliente — em que toda a saída de dinheiro é avaliada pelo detetor **antes** de mexer no saldo e pode ficar retida
até um analista decidir.

**Arquitetura:** tudo na mesma aplicação Flask e na mesma base de dados (abordagem A do design). O banco vive em
módulos próprios (`clientes.py`, `contas_bancarias.py`, `web_balcao.py`, `web_cliente.py`, `acesso_cliente.py`) e
só fala com o detetor através de `detector/autorizacao.py::autorizar()`. A decisão do analista liquida o
movimento retido na mesma transação de base de dados que fecha o alerta.

**Tecnologia:** Python 3.11+, Flask, SQLAlchemy 2, Alembic, SQLite, pandas, pytest. Sem dependências novas.

**Design:** `docs/superpowers/specs/2026-11-18-funcionalidades-bancarias-design.md` — ler antes de cada tarefa;
as secções citadas (ex.: "design 5.3") têm o porquê.

## Restrições globais
- Português de Portugal em toda a interface, mensagens, docstrings e documentação ("utilizador", "ecrã").
- Moeda: metical; mostrar como `1 234,50 MT` (o filtro `mt` existente já formata — reutilizar).
- **Dinheiro em cêntimos inteiros** em todas as colunas e cálculos do banco; nunca `float` para saldos.
- Migração nova `migracoes/versions/0006_banco.py` (`revision = "0006"`, `down_revision = "0005"`), com `downgrade`.
- `detector/banco.py` é o módulo da **base de dados** (nome antigo); `detector/contas.py` são as **contas da
  equipa**. Os módulos novos não reutilizam estes nomes.
- Toda a ação que mexe em dinheiro ou em dados de clientes chama `auditar(...)` com as ações do design 5.9.
- Os 339 testes atuais continuam a passar; `instalar.bat` e `iniciar.bat` não mudam de uso.
- Correr os testes com `.venv\Scripts\python -m pytest -q` (Windows). Commits pequenos, um por tarefa.

## Foco da revisão
1. **Gasto duplo:** dois débitos seguidos que juntos excedem o saldo disponível — o segundo é recusado por saldo
   (teste na Tarefa 9).
2. **Valores escritos à mão:** `"1.234,50"`, `"1234.5"`, `"0"`, `"-10"`, `"abc"`, `"10,555"` — os três primeiros
   são aceites/recusados conforme a Tarefa 3; nunca dão erro 500 (teste na Tarefa 3).
3. **Transferência para a própria conta, para conta inexistente ou bloqueada** — recusada com motivo, sem tocar no
   saldo (teste na Tarefa 9 e 16).
4. **Decidir alerta sem movimento** (transação de ficheiro ou de cliente sem conta) — funciona como hoje (teste na
   Tarefa 10).
5. **Cliente a ver dados de outro** mexendo no URL (`/cliente/movimentos/<id>`) — 404 (teste na Tarefa 16).

---

## Estrutura de ficheiros

| Ficheiro | Responsabilidade |
|---|---|
| `migracoes/versions/0006_banco.py` | tabelas `clientes`, `contas_bancarias`, `movimentos`, `acessos_cliente` |
| `detector/banco.py` (mod.) | classes ORM `Cliente`, `ContaBancaria`, `Movimento`, `AcessoCliente`; `PERFIS` com `balcao`; `AREAS_PERFIL`; estado de alerta `recusado_auto` |
| `detector/dinheiro.py` | `para_centavos`, `de_centavos` |
| `detector/clientes.py` | fichas: validar, criar (com conta), editar, pesquisar, sintéticos |
| `detector/contas_bancarias.py` | saldo, depósito, débito, reserva, liquidação, extrato, bloqueio, verificação |
| `detector/autorizacao.py` | `autorizar()`: saldo → modelo → autorizado/recusado/retido |
| `detector/acesso_cliente.py` | login, sessão e senha dos clientes |
| `detector/web_balcao.py` | rotas `/balcao/...` |
| `detector/web_cliente.py` | rotas `/cliente/...` |
| `detector/acesso.py` (mod.) | `exige_area`, barreira do perfil Balcão |
| `detector/alertas.py` (mod.) | `decidir()` liquida o movimento; `indicadores()` com valor retido/protegido |
| `detector/api.py`, `detector/openapi.py` (mod.) | débito nas transações de clientes com conta; `POST /api/v1/depositos` |
| `detector/gerador.py`, `detector/explicacao.py` (mod.) | transferências, levantamentos, fraude `conta_esvaziada` |
| `detector/limiares.py`, `templates/limiares.html` (mod.) | corte `recusa` |
| `detector/simulacao.py`, `simulador.py` (mod.) | salário, contadores de recusadas/retidas, `conta_esvaziada` |
| `gerir.py` (mod.) | `preparar` cria clientes sintéticos; comando `verificar-saldos` |
| `templates/balcao_*.html`, `templates/cliente_*.html`, `templates/base_cliente.html` | ecrãs |
| `tests/test_banco_contas.py`, `tests/test_autorizacao.py`, `tests/test_balcao.py`, `tests/test_area_cliente.py` | testes novos |

---

# Fase 7 — Clientes, contas e balcão

### Task 1: Tabelas do banco (migração 0006 e ORM)

**Ficheiros:**
- Criar: `migracoes/versions/0006_banco.py`
- Modificar: `detector/banco.py`
- Teste: `tests/test_migracoes.py`

**Interfaces — produz** (em `detector/banco.py`):
- `Cliente`: `id`, `codigo: str(10) unique` (`C0001`), `nome: str(120)`, `data_nascimento: date`,
  `tipo_documento: str(4)` (`BI`|`NUIT`), `numero_documento: str(20)` (único com o tipo), `telefone: str(20)`,
  `email: str(120) | None`, `cidade: str(100)`, `morada: str(255)`, `sintetico: bool`, `criado_em: datetime`,
  `criado_por: str(50)`; relação `conta` (1:1).
- `ContaBancaria` (`__tablename__ = "contas_bancarias"`): `id`, `numero: str(20) unique`, `cliente_id` (FK único),
  `saldo: int` (cêntimos), `saldo_retido: int`, `estado: str(10)` (`ativa`|`bloqueada`), `aberta_em: datetime`;
  propriedade `disponivel -> int` = `saldo - saldo_retido`.
- `Movimento`: `id`, `conta_id` (FK, index), `tipo: str(30)` ∈ `TIPOS_MOVIMENTO = ("deposito", "levantamento",
  "compra", "transferencia_enviada", "transferencia_recebida")`, `valor: int` (>0), `estado: str(12)` ∈
  `ESTADOS_MOVIMENTO = ("autorizado", "recusado", "retido", "cancelado")`, `motivo: str(255) | None`,
  `contraparte_id: int | None` (FK `contas_bancarias`), `canal: str(12)` ∈ `("balcao", "cliente", "api",
  "simulador")`, `momento: datetime` (tempo da transação), `registado_em: datetime` (hora real),
  `transacao_id: int | None` (FK `transacoes_avaliadas`, `SET NULL`, index), `id_transacao: str(100) | None`
  (index), `descricao: str(140) | None`, `saldo_apos: int | None`, `criado_por: str(50)`.
- `AcessoCliente` (`acessos_cliente`): `cliente_id` (PK/FK), `senha_hash`, `trocar_senha`, `tentativas_falhadas`,
  `bloqueado_ate`, `versao_sessao`, `ultimo_acesso` — mesmos tipos que em `Utilizador`.
- `ESTADOS_ALERTA` ganha `"recusado_auto"` (não entra em `ESTADOS_ABERTOS`).

- [ ] **Passo 1: Teste que falha** — em `tests/test_migracoes.py`, `test_migracao_0006_cria_e_remove_o_banco`:
  `upgrade("0006")` cria as quatro tabelas; `downgrade("0005")` remove-as e os dados das tabelas antigas ficam
  (siga o padrão de `test_migracao_0002_desfaz_e_refaz` em `tests/test_seguranca.py`). Acrescente também ao teste
  existente `test_cada_migracao_mantem_os_dados` a nova revisão, se ele enumerar revisões.
- [ ] **Passo 2:** `.venv\Scripts\python -m pytest tests/test_migracoes.py -q` → FAIL (tabela inexistente).
- [ ] **Passo 3:** escrever a migração (padrão de `0005_versoes_modelo.py`, com `CHECK (valor > 0)` e
  `CHECK (saldo_retido >= 0)`) e as classes ORM.
- [ ] **Passo 4:** `.venv\Scripts\python -m pytest -q` → tudo passa.
- [ ] **Passo 5:** commit `"Fase 7: tabelas de clientes, contas e movimentos"`.

### Task 2: Perfil Balcão e permissões por área

**Ficheiros:**
- Modificar: `detector/banco.py`, `detector/acesso.py`, `detector/contas.py` (`NOMES_PERFIL`), `templates/base.html`,
  `templates/utilizador.html`, `templates/utilizadores.html`
- Teste: `tests/test_balcao.py` (novo)

**Interfaces — produz:**
- `PERFIS = ("balcao", "analista", "supervisor", "administrador")`.
- `AREAS_PERFIL = {"balcao": {"balcao"}, "analista": {"alertas"}, "supervisor": {"alertas", "supervisao"},
  "administrador": {"balcao", "alertas", "supervisao", "administracao"}}`.
- `Utilizador.tem_area(area: str) -> bool`. `Utilizador.pode(perfil_minimo)` mantém-se, mas devolve **False** para
  o Balcão em qualquer perfil que não seja `"balcao"`.
- `acesso.exige_area(area: str)` — decorador como `exige`, 403 + auditoria `acesso_negado`.
- `acesso.ENDPOINTS_BALCAO: set[str]` preenchido por `web_balcao` (Tarefa 5). Barreira em `_identificar`: um
  utilizador Balcão só passa em `PUBLICOS ∪ LIVRES_DURANTE_TROCA ∪ ENDPOINTS_BALCAO`; a página `/` redireciona-o
  para `/balcao/clientes`; tudo o resto → 403.
- `contas.NOMES_PERFIL["balcao"] = "Balcão"`.

- [ ] **Passo 1: Testes que falham** em `tests/test_balcao.py`:
  ```python
  @pytest.mark.parametrize("caminho", ["/alertas", "/analisar", "/perfil", "/painel", "/modelos", "/utilizadores"])
  def test_balcao_nao_abre_paginas_do_detetor(cliente_balcao, caminho):
      assert cliente_balcao.get(caminho).status_code == 403

  def test_balcao_na_raiz_vai_para_clientes(cliente_balcao):
      r = cliente_balcao.get("/")
      assert r.status_code == 302 and r.headers["Location"].endswith("/balcao/clientes")

  def test_analista_nao_abre_o_balcao(cliente):  # usar criar_cliente(app, "analista")
      assert analista.get("/balcao/clientes").status_code == 403

  def test_administrador_cria_utilizador_balcao(cliente): ...  # POST /utilizadores com perfil=balcao → 200
  ```
  Fixture `cliente_balcao` = `criar_cliente(criar_app(pasta_pequena), "balcao")` (definir no próprio ficheiro).
  O teste da raiz e o 403 do analista dependem da rota da Tarefa 5: marque-os com
  `pytest.mark.xfail(strict=True)` aqui e retire a marca na Tarefa 5.
- [ ] **Passo 2:** correr → FAIL.
- [ ] **Passo 3:** implementar. O menu em `base.html` passa a usar `utilizador.tem_area(...)`; o Balcão vê só
  "Clientes", "Senha" e "Sair".
- [ ] **Passo 4:** `.venv\Scripts\python -m pytest -q` → tudo passa (os perfis antigos não mudam de acesso).
- [ ] **Passo 5:** commit.

### Task 3: Dinheiro, fichas de clientes e contas

**Ficheiros:**
- Criar: `detector/dinheiro.py`, `detector/clientes.py`, `detector/contas_bancarias.py`
- Teste: `tests/test_banco_contas.py`

**Interfaces — produz:**
- `dinheiro.para_centavos(texto: str | int | float) -> int` — aceita `"1234,50"`, `"1 234,50"`, `"1.234,50"`,
  `"1234.5"`, `1234.5`; recusa (ValueError com mensagem em português) zero, negativos, mais de duas casas, texto
  e valores acima de `MAXIMO_CENTAVOS = 10_000_000_000` (100 milhões MT). `de_centavos(c: int) -> float`.
- `clientes.DadosCliente` (dataclass): `nome, data_nascimento: date, tipo_documento, numero_documento, telefone,
  email, cidade, morada`. `clientes.validar(form: Mapping[str, str]) -> DadosCliente` (NUIT: 9 dígitos; BI: 12
  dígitos + 1 letra; telefone: 9 dígitos começados por 8; idade ≥ 18; cidade ∈ `gerador.CIDADES`).
- `clientes.criar(sessoes, dados: DadosCliente, por: str, sintetico=False, codigo: str | None = None) -> Cliente` —
  cria cliente **e** conta (saldo 0) na mesma transação. Código seguinte: maior `C####` existente + 1 (começa em
  `C0001`). Número de conta: `"0001" + código sem o C, com 7 dígitos + dígito de controlo mod 11` — formato à
  escolha, desde que único e estável.
- `clientes.obter(sessoes, cliente_id) -> Cliente | None`, `obter_por_codigo(sessoes, codigo)`,
  `pesquisar(sessoes, texto: str, limite=50) -> list[Cliente]` (nome, código, documento),
  `editar(sessoes, cliente_id, telefone, email, morada, cidade, por) -> list[str]` (mudanças, para a auditoria).
- `contas_bancarias.depositar(sessoes, conta_id, valor: int, canal, por, momento=None, descricao=None) -> Movimento`
  (recusa conta bloqueada), `extrato(sessoes, conta_id, limite=100) -> list[Movimento]` (mais recentes primeiro),
  `bloquear(sessoes, conta_id, bloquear: bool, por)`, `verificar_saldos(sessoes) -> list[str]` (uma linha por
  conta em que `saldo != soma(autorizados de entrada) − soma(autorizados de saída)` ou `saldo_retido != soma dos
  retidos`; lista vazia = tudo certo).

- [ ] **Passo 1: Testes que falham** em `tests/test_banco_contas.py`:
  - `para_centavos`: parametrizado — `("1234,50", 123450)`, `("1 234,50", 123450)`, `("1.234,50", 123450)`,
    `("1234.5", 123450)`, `(10, 1000)`; e erro para `"0"`, `"-10"`, `"abc"`, `"10,555"`, `""`.
  - `test_criar_cliente_abre_conta_com_saldo_zero`: código `C0001`, `conta.saldo == 0`, `conta.estado == "ativa"`.
  - `test_documento_repetido_e_recusado` (mesmo tipo + número → ValueError "Já existe").
  - `test_validar_recusa_menor_de_idade_e_nuit_invalido`.
  - `test_deposito_soma_ao_saldo_e_fica_no_extrato`: depósito de 50 000 → `saldo == 50000`,
    `extrato[0].saldo_apos == 50000`, `verificar_saldos(...) == []`.
  - `test_deposito_em_conta_bloqueada_e_recusado`.
- [ ] **Passo 2:** correr → FAIL (módulos inexistentes).
- [ ] **Passo 3:** implementar. O saldo muda com `UPDATE ... SET saldo = saldo + :v` na mesma transação que insere
  o movimento; `saldo_apos` é o valor lido depois do UPDATE.
- [ ] **Passo 4:** `.venv\Scripts\python -m pytest tests/test_banco_contas.py -q` → PASS.
- [ ] **Passo 5:** commit.

### Task 4: Clientes sintéticos e comandos de gestão

**Ficheiros:**
- Modificar: `detector/clientes.py`, `gerir.py`
- Teste: `tests/test_banco_contas.py`

**Interfaces — produz:**
- `clientes.criar_sinteticos(sessoes, perfis: list[dict], semente: int = 42) -> int` — `perfis` vem de
  `simulacao.clientes_do_treino(n)`; cria ficha + conta com o **mesmo código** do gerador (`C0001`…), nome e
  documento fictícios determinísticos (listas pequenas de nomes/apelidos moçambicanos comuns, combinados pela
  semente), cidade = `perfil["cidade"]`, `sintetico=True`, e um depósito inicial de
  `round(perfil["tipico"] * perfil["taxa"] * 90 * 100)` cêntimos, canal `"balcao"`, descrição `"Saldo inicial"`.
  **Idempotente**: códigos já existentes são saltados; devolve quantos criou.
- `gerir.py preparar` chama `criar_sinteticos` depois de treinar (com `n` = número de clientes do treino).
- `gerir.py verificar-saldos` → imprime as linhas de `verificar_saldos`; código de saída 1 se houver diferenças.

- [ ] **Passo 1: Testes que falham:** `test_sinteticos_sao_idempotentes` (duas chamadas com 5 perfis → 5 e 0;
  códigos `C0001..C0005`; saldo > 0); `test_cliente_novo_continua_a_numeracao` (depois de 5 sintéticos,
  `criar(...)` dá `C0006`); `test_verificar_saldos_pelo_gerir` (`gerir.main(["verificar-saldos"]) == 0`).
- [ ] **Passo 2:** correr → FAIL.
- [ ] **Passo 3:** implementar.
- [ ] **Passo 4:** `.venv\Scripts\python -m pytest -q` → tudo passa.
- [ ] **Passo 5:** commit.

### Task 5: Ecrãs do balcão (clientes, ficha, depósito)

**Ficheiros:**
- Criar: `detector/web_balcao.py`, `templates/balcao_clientes.html`, `templates/balcao_cliente_novo.html`,
  `templates/balcao_cliente.html`
- Modificar: `app.py` (instalar; endpoints em `SEM_MODELOS`), `tests/test_balcao.py` (retirar `xfail`)

**Interfaces — produz** (todas com `@exige_area("balcao")`; nomes de endpoint em `acesso.ENDPOINTS_BALCAO`):
- `GET /balcao/clientes?q=` (lista + pesquisa), `GET|POST /balcao/clientes/novo`,
  `GET /balcao/clientes/<int:cliente_id>` (ficha, conta, saldo, disponível, retido, extrato),
  `POST /balcao/clientes/<id>/editar`, `POST /balcao/clientes/<id>/deposito` (campos `valor`, `descricao`),
  `POST /balcao/clientes/<id>/bloquear` (`bloquear=1|0`).
- Auditoria: `cliente_criado`, `conta_aberta`, `cliente_alterado`, `deposito`, `conta_bloqueada` (alvo = código).

- [ ] **Passo 1: Testes que falham** em `tests/test_balcao.py`: criar cliente pelo formulário → redireciona para a
  ficha e a auditoria tem `cliente_criado`; formulário inválido → 400 com a mensagem do `validar`; depósito
  `"1 500,00"` → saldo `1 500,00 MT` na ficha; pesquisa por parte do nome encontra; retirar os `xfail` da Tarefa 2.
- [ ] **Passo 2:** correr → FAIL.
- [ ] **Passo 3:** implementar (reutilizar `estilo.css`, a classe `pilha` dos formulários e o filtro `mt`
  convertendo cêntimos com `de_centavos`).
- [ ] **Passo 4:** `.venv\Scripts\python -m pytest -q` → tudo passa.
- [ ] **Passo 5:** commit `"Fase 7: balcão"`.

---

# Fase 8 — Autorização antes do movimento

### Task 6: Gerador com transferências, levantamentos e "conta esvaziada"

**Ficheiros:**
- Modificar: `detector/gerador.py`, `detector/explicacao.py`, `detector/simulacao.py` (`TRANSACOES_POR_EVENTO`
  não muda; `_fraude` ganha o tipo novo)
- Teste: `tests/test_gerador.py`

**Interfaces — produz:**
- `gerador.CATEGORIA_TRANSFERENCIA = "TRANSFERENCIA"`, `CATEGORIA_LEVANTAMENTO = "LEVANTAMENTO"`. **Não** entram em
  `CATEGORIAS` (as compras e a `conta_tomada` continuam a escolher só categorias de compra).
- `TIPOS_FRAUDE` ganha `"conta_esvaziada"`; `NOMES_TIPO_FRAUDE["conta_esvaziada"] = "Conta esvaziada"`.
- Legítimas: cada cliente faz, em média, 2 transferências e 2 levantamentos por mês, na sua cidade, nas horas
  habituais; transferência = `tipico × lognormal(1.0, 0.5)`, levantamento = `tipico × lognormal(0.7, 0.4)`
  arredondado a 100 MT.
- Fraude `conta_esvaziada`: uma transferência de `tipico × uniform(15, 40)`, cidade diferente da habitual,
  país `MZ`.
- `explicacao.NOMES_CATEGORIA` ganha `"TRANSFERENCIA": "transferência"`, `"LEVANTAMENTO": "levantamento"`.
- Página de versões (`templates/versao.html`/`modelos.html`): ao publicar ou voltar a uma versão treinada antes desta
  tarefa (sem as categorias novas no histórico), aviso "Esta versão não conhece transferências nem levantamentos:
  vai dar mais alertas nesses movimentos." (design 5.10).

- [ ] **Passo 1: Testes que falham:** `gerar_transacoes(40, 60)` contém as duas categorias novas e pelo menos uma
  `conta_esvaziada`; nenhuma linha `conta_tomada` usa as categorias novas; a proporção de fraude continua entre
  0,5% e 2%.
- [ ] **Passo 2:** correr → FAIL.
- [ ] **Passo 3:** implementar; no simulador, `_fraude(..., "conta_esvaziada", ...)` produz a mesma transferência.
- [ ] **Passo 4:** `.venv\Scripts\python -m pytest -q` → tudo passa. Se um teste de métricas de `test_modelos.py`
  ou `test_treinar.py` cair, ajustar os parâmetros do gerador (não os limiares dos testes) até voltarem a passar.
- [ ] **Passo 5:** commit. Nota para a instalação: `gerir.py treinar --forcar` re-treina com os dados novos.

### Task 7: Corte de recusa automática

**Ficheiros:** Modificar `detector/limiares.py`, `detector/web_modelos.py` (rota dos limiares),
`templates/limiares.html`, `detector/banco.py`/`alertas.py` (`NOMES_ESTADO["recusado_auto"] = "Recusado
automaticamente"`). Teste: `tests/test_ciclo_modelo.py`.

**Interfaces — produz:** `Cortes(risco=50.0, anomalia=50.0, recusa=95.0)`; validação extra: `recusa >= risco`
(ValueError "O limite de recusa tem de ser maior ou igual ao de alto risco."). `Cortes` gravados antes (sem
`recusa`) lêem-se com 95.

- [ ] **Passo 1: Testes que falham:** ajuste antigo sem `recusa` lê-se com 95; gravar `recusa=40, risco=50` →
  erro; página de limiares mostra e grava o campo.
- [ ] **Passo 2–4:** FAIL → implementar → `.venv\Scripts\python -m pytest -q` passa.
- [ ] **Passo 5:** commit.

### Task 8: Débito, reserva e liquidação nas contas

**Ficheiros:** Modificar `detector/contas_bancarias.py`. Teste: `tests/test_banco_contas.py`.

**Interfaces — produz** (todas recebem uma `Session` aberta — quem chama controla a transação):
- `debitar(sessao, conta: ContaBancaria, tipo, valor, canal, por, momento, estado: str, motivo=None,
  contraparte_id=None, transacao_id=None, id_transacao=None, descricao=None) -> Movimento`:
  - `estado="autorizado"`: `UPDATE ... SET saldo = saldo - :v WHERE id = :id AND saldo - saldo_retido >= :v`; se não
    atualizou nenhuma linha → grava como `recusado`, motivo `"Saldo insuficiente."`. Se for transferência, credita
    a contraparte (`transferencia_recebida`, autorizado).
  - `estado="retido"`: o mesmo controlo, mas soma a `saldo_retido` (reserva).
  - `estado="recusado"`: só grava o movimento, sem mexer no saldo.
- `liquidar(sessao, movimento_id: int, executar: bool, por: str) -> Movimento` — só para `retido`; liberta a reserva;
  se `executar`, debita (e credita a contraparte; se esta estiver bloqueada → `cancelado`, motivo
  `"A conta de destino foi bloqueada entretanto."`); senão `cancelado`, motivo `"Fraude confirmada."`.
- `movimento_da_transacao(sessao, transacao_id: int) -> Movimento | None`.

- [ ] **Passo 1: Testes que falham:** saldo 100 → débito 60 autorizado → saldo 40; segundo débito 60 → recusado
  "Saldo insuficiente" (**foco 1**); retido de 30 com saldo 100 → disponível 70, saldo 100; depois débito de 80 →
  recusado (**foco da reserva**); `liquidar(executar=True)` → saldo 70, retido 0; `liquidar(executar=False)` →
  saldo 100, retido 0; transferência autorizada credita o destino; `verificar_saldos == []` no fim de cada teste.
- [ ] **Passo 2–4:** FAIL → implementar → PASS.
- [ ] **Passo 5:** commit.

### Task 9: `autorizar()`

**Ficheiros:**
- Criar: `detector/autorizacao.py`
- Modificar: `detector/repositorio.py` (extrair `gravar_analise(sessao, resultado, origem, modelo, ...) -> Analise`
  que corre dentro de uma sessão aberta; `guardar_analise` passa a usá-la), `detector/alertas.py`
  (`criar_para_analise` aceita `estados: dict[int, str]` opcional, `ordem → estado`, para criar alertas já em
  `recusado_auto`); `repositorio.ORIGENS` ganha `"banco"` (balcão e área do cliente; a API usa `"api"`)
- Teste: `tests/test_autorizacao.py`

**Interfaces — produz:**
```python
@dataclass(frozen=True)
class PedidoDebito:
    conta_id: int
    tipo: str              # "compra" | "transferencia_enviada" | "levantamento"
    valor: int             # cêntimos
    momento: datetime
    cidade: str
    pais: str
    categoria: str         # para compras, a do pedido; transferência/levantamento: as constantes da Tarefa 6
    canal: str
    por: str
    id_transacao: str | None = None      # gerado ("BNC-" + token) se None
    contraparte_id: int | None = None
    descricao: str | None = None
    fraude_simulada: bool | None = None
    tipo_fraude_simulada: str | None = None

@dataclass(frozen=True)
class Decisao:
    movimento_id: int
    estado: str            # "autorizado" | "recusado" | "retido"
    motivo: str | None
    nivel: str | None      # None quando recusado antes do modelo
    risco: float | None
    alerta_id: int | None

def autorizar(sessoes, artefatos, pedidos: list[PedidoDebito], cortes, origem: str,
              chave_api_id: int | None = None) -> list[Decisao]  # mesma ordem dos pedidos
```
Algoritmo (design 5.3; único bloco que o construtor não deduz sozinho):
1. Validação sem modelo: conta inexistente/bloqueada, contraparte inexistente/bloqueada/igual à própria →
   `recusado` com motivo, sem avaliação.
2. Avaliar os restantes de uma vez com `avaliar_transacoes(artefatos, df, historico_extra=historico_api(...),
   cortes=cortes)` (o mesmo caminho de `api.receber_transacoes`); cliente = código do titular.
3. Numa só `sessoes.begin()`, por ordem de `(momento, índice)`:
   - sem saldo disponível → `recusado` "Saldo insuficiente." **e a avaliação não é gravada** (design 5.3 passo 1);
   - `risco >= cortes.recusa` → `recusado` "Risco muito alto: recusado automaticamente.", alerta `recusado_auto`;
   - `nivel == "Alto risco"` → `retido`, alerta `novo`;
   - `nivel == "Atenção"` → `autorizado`, alerta `novo`;
   - `Normal` → `autorizado`.
   Grava a análise (`gravar_analise`, só com as linhas que passaram o saldo), os alertas e os movimentos
   (`debitar`) e liga `Movimento.transacao_id`.
4. `registo.info` com o resumo; auditoria fica para quem chama (rota), não aqui.

- [ ] **Passo 1: Testes que falham** (`artefatos_pequenos`; para forçar níveis, `monkeypatch` de
  `autorizacao.avaliar_transacoes` devolvendo o nível/risco pretendidos):
  - `test_normal_autoriza_e_debita`; `test_alto_risco_retem_e_cria_alerta_novo` (saldo inalterado, disponível
    menor, alerta `novo`); `test_atencao_autoriza_com_alerta`; `test_risco_acima_da_recusa_recusa_com_alerta_fechado`;
  - `test_sem_saldo_recusa_sem_avaliacao_gravada` (nenhuma `TransacaoAvaliada` nova);
  - `test_dois_debitos_que_excedem_o_saldo` — dois pedidos de 60 com saldo 100 na mesma chamada: o segundo
    recusado (**foco 1**);
  - `test_transferencia_para_a_propria_conta_inexistente_ou_bloqueada` — parametrizado, recusado com motivo,
    saldos iguais (**foco 3**);
  - em todos: `verificar_saldos(sessoes) == []`.
- [ ] **Passo 2–4:** FAIL → implementar → `.venv\Scripts\python -m pytest -q` passa (o refactor de
  `guardar_analise` não pode partir os testes da API e da análise).
- [ ] **Passo 5:** commit.

### Task 10: Decisão do analista liquida o movimento

**Ficheiros:** Modificar `detector/alertas.py` (`decidir`), `detector/web_alertas.py`, `templates/alerta.html`.
Teste: `tests/test_alertas.py`.

**Interfaces — consome:** `contas_bancarias.movimento_da_transacao`, `liquidar` (Tarefa 8).
Em `decidir`, dentro da mesma `sessoes.begin()`: se a transação do alerta tem movimento `retido` →
`liquidar(executar = decisao == "falso_alarme")`. A rota audita `movimento_executado` ou `movimento_cancelado`.
A ficha do alerta mostra conta, titular, saldo disponível e estado do movimento; com movimento já liquidado, o
botão "Reabrir" vem acompanhado de "Reabrir não devolve nem volta a reter o dinheiro."

- [ ] **Passo 1: Testes que falham:** alerta retido → decidir `falso_alarme` → movimento `autorizado`, saldo
  debitado; `fraude` → `cancelado`, saldo intacto, retido 0; reabrir não muda o movimento; **decidir um alerta de
  uma análise de ficheiro (sem movimento) continua a funcionar** (**foco 4**); se `liquidar` falhar, o alerta
  continua `em_analise` (mesma transação).
- [ ] **Passo 2–4:** FAIL → implementar → PASS.
- [ ] **Passo 5:** commit.

### Task 11: Levantamento ao balcão

**Ficheiros:** Modificar `detector/web_balcao.py`, `templates/balcao_cliente.html`. Teste: `tests/test_balcao.py`.

`POST /balcao/clientes/<id>/levantamento` (`valor`, `cidade` — por omissão a do cliente) →
`autorizar([PedidoDebito(tipo="levantamento", categoria=CATEGORIA_LEVANTAMENTO, canal="balcao", pais="MZ", ...)])`.
Mensagens: autorizado → "Levantamento autorizado. Entregue o dinheiro."; retido → "Em verificação. **Não entregue o
dinheiro** até a equipa de fraude decidir."; recusado → o motivo. Auditoria `levantamento`.

- [ ] **Passos 1–5:** testes para as três mensagens (níveis forçados por `monkeypatch`), FAIL → implementar → PASS →
  commit.

### Task 12: API — débitos e depósitos

**Ficheiros:** Modificar `detector/api.py`, `detector/openapi.py`. Teste: `tests/test_api.py`.

**Interfaces — produz:**
- `POST /api/v1/transacoes`: transações de clientes **com conta** passam por `autorizar(..., origem="api")`
  como `compra` (canal `"api"`, ou `"simulador"` quando vem o campo `simulacao`); transações com categoria
  `TRANSFERENCIA` são `transferencia_enviada` **sem contraparte** (saída para fora — só usada pelo simulador).
  Clientes **sem conta** seguem o caminho atual. Cada resultado ganha `"movimento": {"estado", "motivo"}` ou
  `"movimento": null`. Repetidas devolvem o estado do movimento já gravado.
- `POST /api/v1/depositos`: corpo `{"depositos": [{"id_deposito", "cliente", "valor", "data_hora", "descricao"}]}`,
  máx. 500; resposta `{"resultados": [{"id_deposito", "estado", "saldo"}]}`; idempotente pelo `id_deposito`
  (guardado em `Movimento.id_transacao`); cliente sem conta → 422 com a lista de erros, nada gravado.
- OpenAPI atualizada (os testes já verificam que a especificação é válida).

- [ ] **Passo 1: Testes que falham:** cliente com conta e saldo → `movimento.estado == "autorizado"` e saldo
  desce; sem saldo → `"recusado"`; cliente sem conta → `movimento is None` e resposta igual à de hoje;
  depósito repetido não soma duas vezes; `carga.py` continua a passar (`tests/test_carga.py`).
- [ ] **Passo 2–4:** FAIL → implementar → `.venv\Scripts\python -m pytest -q` passa.
- [ ] **Passo 5:** commit.

### Task 13: Simulador com salário e contadores

**Ficheiros:** Modificar `detector/simulacao.py`, `simulador.py`. Teste: `tests/test_api.py` ou ficheiro de testes
do simulador existente (procurar `Estatisticas` em `tests/`).

- `Simulador.avancar` emite também depósitos de salário no dia 25 às 08:00 simuladas:
  `round(tipico × taxa × 30 × uniform(1.1, 1.4), 2)` MT por cliente, `id_deposito = f"{prefixo}-SAL-{AAAAMM}-{cliente}"`.
  Devolve `(transacoes, depositos)`.
- `simular()` envia os depósitos para `/api/v1/depositos` antes das transações do mesmo passo.
- `Estatisticas` ganha `recusadas_saldo`, `retidas` (a partir de `resultado["movimento"]`); `resumo()` mostra
  `"recusadas por saldo N · retidas N"`.
- `--tipos` aceita `conta_esvaziada` (vem de `TIPOS_FRAUDE`).

- [ ] **Passos 1–5:** testes com o transporte falso já usado nos testes do simulador (salário no dia 25, uma vez
  por cliente e por mês; contadores); FAIL → implementar → PASS → commit.

### Task 14: Painel — valor retido e valor protegido

**Ficheiros:** Modificar `detector/alertas.py` (`Indicadores`, `indicadores`), `templates/painel.html`.
Teste: `tests/test_alertas.py`.

`Indicadores` ganha `valor_retido: int` (soma dos movimentos `retido`, agora) e `valor_protegido: int` (soma dos
`cancelado` com motivo "Fraude confirmada." + `recusado` por risco, no período). Mostrados como cartões ao lado dos
atuais.

- [ ] **Passos 1–5:** teste com um retido e um cancelado → valores certos; FAIL → implementar → PASS → commit
  `"Fase 8: autorização antes do movimento"`.

---

# Fase 9 — Área do cliente e cena de demonstração

### Task 15: Acesso do cliente

**Ficheiros:**
- Criar: `detector/acesso_cliente.py`, `templates/base_cliente.html`, `templates/cliente_entrar.html`,
  `templates/cliente_senha.html`
- Modificar: `detector/acesso.py` (o `_identificar` da equipa ignora caminhos `/cliente/`),
  `detector/web_balcao.py` (criar/redefinir acesso), `templates/balcao_cliente.html`, `app.py`
- Teste: `tests/test_area_cliente.py`

**Interfaces — produz:**
- `acesso_cliente.criar_ou_redefinir(sessoes, cliente_id, senha) -> None` (`trocar_senha=True`, `versao_sessao += 1`),
  `entrar(sessoes, codigo, senha) -> Cliente` (ValueError com as mesmas mensagens e o mesmo bloqueio de 5
  tentativas / 15 min de `contas.py` — reutilizar `MAX_TENTATIVAS`, `TEMPO_BLOQUEIO`), `mudar_senha(...)`.
- Sessão: chaves `cliente_id`, `cliente_versao`, `cliente_conta` (impressão como `acesso.impressao`, com o
  `senha_hash` do acesso). `g.cliente` preenchido por um `before_request` só para `/cliente/`; páginas
  `/cliente/*` sem `g.cliente` → `/cliente/entrar`; com `trocar_senha` → `/cliente/senha`.
- `g.utilizador` (equipa) **não** dá acesso a `/cliente/*`, e `g.cliente` não dá acesso a nada fora de `/cliente/`
  (já garantido: o `_identificar` da equipa exige `uid`).
- CSRF: o `_verificar_csrf` existente já cobre `/cliente/` — confirmar com teste.
- Rotas: `GET|POST /cliente/entrar`, `POST /cliente/sair`, `GET|POST /cliente/senha`.
- Balcão: `POST /balcao/clientes/<id>/acesso` (`senha`), auditoria `acesso_cliente_criado` /
  `senha_cliente_redefinida`. Entradas e saídas de clientes auditadas (`entrada_cliente`, `saida_cliente`, alvo =
  código, utilizador nulo).
- `base_cliente.html`: mesmo `estilo.css`, cabeçalho com o nome fictício `"Banco Demonstração"` e sem o menu interno.

- [ ] **Passo 1: Testes que falham:** balcão cria acesso → cliente entra e é mandado mudar a senha; 5 senhas erradas
  → bloqueado; sessão de equipa a abrir `/cliente/` → redireciona para `/cliente/entrar`; sessão de cliente a abrir
  `/alertas` → redireciona para `/entrar`; POST sem CSRF → 400; redefinir o acesso termina a sessão aberta do
  cliente.
- [ ] **Passo 2–4:** FAIL → implementar → `.venv\Scripts\python -m pytest -q` passa.
- [ ] **Passo 5:** commit.

### Task 16: Saldo, movimentos e transferências do cliente

**Ficheiros:**
- Criar: `detector/web_cliente.py`, `templates/cliente_inicio.html`, `templates/cliente_transferir.html`,
  `templates/cliente_movimento.html`
- Modificar: `detector/configuracao.py` (`modo_demonstracao: bool` — `MODO_DEMONSTRACAO` no `.env`; por omissão
  `True` fora de produção, `False` em produção), `app.py`
- Teste: `tests/test_area_cliente.py`

**Interfaces — produz:**
- `GET /cliente/` — saldo, disponível, valor em verificação e os últimos 50 movimentos com estado em texto:
  autorizado → "Concluída", recusado → "Recusada — <motivo>", retido → "Em verificação", cancelado → "Cancelada".
- `GET /cliente/movimentos/<int:movimento_id>` — 404 se o movimento não for de uma conta do cliente (**foco 5**).
- `GET|POST /cliente/transferir` — `conta_destino` (número), `valor`, `descricao`; com `modo_demonstracao`, campos
  `cidade` e `pais` ("A partir de", por omissão a cidade do cliente e `MZ`); sem ele, sempre a cidade do cliente e
  `MZ`. Chama `autorizar([PedidoDebito(tipo="transferencia_enviada", categoria=CATEGORIA_TRANSFERENCIA,
  canal="cliente", ...)], origem="banco")`, e mostra o resultado com o mesmo texto.

- [ ] **Passo 1: Testes que falham:** transferência normal → "Concluída" e saldos das duas contas certos;
  retida (nível forçado) → "Em verificação"; para a própria conta / conta inexistente → "Recusada" com motivo
  (**foco 3**); valor `"abc"` → 400 com mensagem (**foco 2**); movimento de outro cliente → 404 (**foco 5**);
  sem modo demonstração os campos "A partir de" não aparecem e são ignorados se enviados.
- [ ] **Passo 2–4:** FAIL → implementar → PASS.
- [ ] **Passo 5:** commit.

### Task 17: Documentação e guião da demonstração

**Ficheiros:** Modificar `docs/manual_utilizador.md` (Balcão; área do cliente), `docs/manual_administracao.md`
(perfil Balcão, `MODO_DEMONSTRACAO`, `verificar-saldos`, re-treino com as categorias novas, corte de recusa,
endpoint de depósitos), `docs/manual_instalacao.md` (o que `preparar` cria), `PLANO_SISTEMA_COMPLETO.md`
(Fases 7–9 com os critérios de conclusão do design §6), `README.md`. Criar `docs/guiao_demonstracao.md`.

Guião (passos e o que se vê em cada um): (1) balcão cria cliente novo e deposita 20 000 MT; (2) balcão cria o
acesso; (3) cliente entra, muda a senha, faz duas transferências pequenas → "Concluída"; (4) com "A partir de:
Tete", transfere quase todo o saldo → "Em verificação", saldo disponível baixa, saldo total intacto; (5) analista vê
o alerta, assume, confirma fraude → movimento cancelado, saldo disponível reposto; (6) painel mostra o valor
protegido. Inclui também `C0001` (cliente sintético) com o simulador ligado.

- [ ] **Passo 1:** escrever.
- [ ] **Passo 2:** executar o guião à mão numa instalação de teste (`gerir.py preparar` + `iniciar.bat` +
  `simulador.py`) e corrigir o que não bater; `gerir.py verificar-saldos` → sem diferenças.
- [ ] **Passo 3:** `.venv\Scripts\python -m pytest -q` → tudo passa.
- [ ] **Passo 4:** commit `"Fase 9: área do cliente e guião da demonstração"`.
