# Histórico importado, pagamentos com categoria e novo estilo

Data: 2026-11-19 · Estado: proposta para aprovação

Três melhorias independentes ao banco de demonstração, feitas por esta ordem: (1) histórico anterior para
clientes novos, (2) pagamentos com categoria e local na área do cliente, (3) novo estilo visual. Cada uma é entregue
e testada sozinha.

## 1. Intenção

Tornar a demonstração **mais convincente** e o detetor **mais justo com clientes novos**:
- Um cliente que chega de outro banco não tem passado neste sistema, por isso o detetor trata-o com desconfiança
  (`historico_curto`). Com o extrato antigo importado no balcão, o detetor conhece os seus hábitos desde o dia 1.
- Na área do cliente só há transferências (categoria fixa, hora = agora). Não se consegue mostrar ao vivo os sinais
  com que o modelo foi treinado — **categoria nova, hora invulgar, valor fora do habitual, país estranho**. Um
  pagamento com categoria, local e hora simulada torna isso possível.
- A interface é "muito simples". O utilizador gosta das cores atuais e escolheu a direção visual **C** (abaixo).

### Objetivos
- Balcão: importar, opcionalmente, um extrato anterior (CSV) de um cliente; ou gerar um histórico de exemplo.
- Área do cliente: nova operação **Pagamento** com categoria, comerciante, valor e (no modo demonstração) local,
  data e hora.
- Novo estilo C em todo o sistema (equipa, balcão, área do cliente), com as mesmas cores.

### Não-objetivos
- Variáveis novas no modelo, re-treino ou alterações às features (decisão do utilizador: usar as que existem).
- Pagamentos com categoria no balcão (fica só na área do cliente).
- Mostrar o histórico importado ao cliente.
- Bibliotecas de CSS/JS externas.

### Anti-objetivos (falharia mesmo "funcionando")
- O histórico importado mexer no saldo, aparecer no extrato do cliente, criar alertas, ou contaminar a
  monitorização, as métricas ou o re-treino.
- Um pagamento escapar ao detetor ou ao controlo de saldo (`autorizar()` é a única porta, como hoje).
- Um local incoerente (cidade de Moçambique com país estrangeiro).
- O novo estilo partir a política de segurança (CSP `default-src 'self'`), a acessibilidade (foco, contraste,
  `role=alert`) ou o uso em ecrãs pequenos.

## 2. Restrições
- Mesmo stack (Flask, SQLAlchemy, SQLite, Alembic, Jinja); português de Portugal; dinheiro em cêntimos inteiros.
- Os 536 testes atuais continuam a passar; cada parte traz os seus testes.
- O detetor só usa os **últimos 90 dias** do cliente ao avaliar um movimento do banco
  (`autorizacao.DIAS_HISTORICO = 90`).
- Nada de ficheiros carregados de fora (CSP); sem scripts inline.
- Deploy no Render continua a funcionar (migração aplica-se sozinha).

## 3. Abordagens

**Histórico importado** — escolhida a **A**:

| | Abordagem | Troca |
|---|---|---|
| **A** | Tabela própria `historico_importado` (migração 0008); o detetor junta-a ao histórico do cliente ao avaliar | Separado das avaliações: não aparece em análises, alertas, monitorização nem re-treino. Mais uma tabela e um ponto de leitura. |
| B | Reaproveitar `transacoes_avaliadas` com origem "historico" | Sem tabela nova, mas linhas nunca avaliadas contaminam métricas e re-treino, salvo exclusões em vários sítios. |

**Pagamento** — uma forma sensata: operação nova na área do cliente sobre `autorizar()` (tipo `compra`).
**Estilo** — uma forma sensata: reescrever `static/estilo.css` e ajustar os templates; um framework teria de ser
copiado para o projeto (CSP) e pesa muito para o ganho.

## 4. O que foi decidido (pelo utilizador)

### 4.1 Histórico importado
- Opcional, na **ficha do cliente no balcão**: secção "Histórico anterior".
- Ficheiro CSV com as colunas do sistema **sem** `cliente` (é o da ficha): `data_hora, valor, cidade, pais,
  categoria` (aceitar também o CSV com `cliente` se coincidir com o da ficha — ver 5.1).
- Só para **balcão e detetor**: não mexe no saldo, não aparece no extrato nem na área do cliente, não é avaliado,
  não cria alertas.
- Ficha mostra um resumo ("Histórico importado: 87 transações, de 03/08 a 15/11") e permite **gerar um histórico
  de exemplo** com um clique.
- Só datas passadas, **no máximo 90 dias** antes de hoje (o detetor não usa mais do que isso).
- Ficheiro com erros recusado por inteiro, com a lista de erros (como em "Analisar ficheiro").
- Pode importar-se mais de uma vez; linhas repetidas são ignoradas.

### 4.2 Pagamento
- Nova operação **Pagamento** na área do cliente: **categoria** (Mercearia, Combustível, Restaurante, Farmácia,
  Recargas, Transporte, Vestuário, Eletrónica, Viagem), **comerciante**, **valor**.
- Campo único **Local** (substitui Cidade + País, também na transferência): as 6 cidades de Moçambique e, num grupo
  "Estrangeiro", os países de `gerador.PAISES_ESTRANHOS`. Cidade ⇒ país MZ; país estrangeiro ⇒ "país estranho".
- No **modo demonstração**: Local, **data** e **hora** simuladas (pagamento e transferência). Data/hora não podem
  ser futuras e no máximo **30 dias** atrás. Fora do modo demonstração: local = cidade do cliente, hora = agora.
- Só na área do cliente (o balcão mantém depósitos e levantamentos).

### 4.3 Estilo C
- Mesmas cores (`--tinta #17232e`, `--acao #1f5f8b`, `--alto #b3261e`, `--atencao #8a5a00`, `--normal #2e7d4f`).
- Faixa de cabeçalho com gradiente (tinta → ação) e navegação em "pílulas"; o conteúdo começa com **cartões que
  sobrepõem a faixa**; cartões com cantos arredondados e sombra suave; **listas com ícone** em vez de tabelas onde
  a informação é uma lista de itens (alertas, movimentos, clientes); tabelas mantêm-se onde há colunas a comparar
  (análises, auditoria), em cartão.
- Aplica-se a equipa, balcão e área do cliente (a área do cliente mantém o nome "Banco Demonstração").

## 5. Propostas minhas (o construtor pode afinar)

### 5.1 Histórico importado
- Tabela `historico_importado`: `id`, `cliente_id` (FK clientes, index), `data_hora`, `valor` (cêntimos),
  `cidade`, `pais`, `categoria`, `importado_em`, `importado_por`, `origem` ("ficheiro" | "exemplo"); `UNIQUE
  (cliente_id, data_hora, valor, cidade, pais, categoria)` para ignorar repetidas (INSERT … ON CONFLICT DO NOTHING
  ou filtro prévio). Migração **0008** com downgrade.
- Validação reutiliza `detector.entrada` (normalização de colunas, valores, datas, categorias e países como em
  "Analisar ficheiro"); limites: máx. **2 000 linhas** por ficheiro; datas em `[hoje − 90 dias, agora[`;
  categorias de compra **e** TRANSFERENCIA/LEVANTAMENTO aceites.
- O detetor: `autorizacao.autorizar()` e o caminho da API para clientes com conta juntam ao `historico_extra` as
  linhas importadas do cliente (convertidas para o formato do histórico: `cliente`=código, `valor` em MT,
  `fraude`=0, `id_transacao` = "HIST-<id>"). As linhas importadas **não** entram em `historico_api` (que alimenta
  monitorização/re-treino indiretamente) — leitura própria.
- **Histórico de exemplo:** gera ~60 dias de compras legítimas coerentes com a ficha (cidade do cliente, 3–4
  categorias, valor típico ~1 200 MT, 0,5–3 compras/dia, horas à volta de um pico diurno), reutilizando as funções
  do gerador para um perfil criado a partir da ficha; origem "exemplo"; determinístico por cliente.
- Ficha: resumo (n.º de linhas, primeira e última data, categorias mais usadas), botões "Importar" e "Gerar exemplo",
  e "Apagar histórico importado" (para refazer a demonstração). Auditoria: `historico_importado`,
  `historico_apagado`.
- `historico_curto` deixa naturalmente de se aplicar quando há ≥ 5 transações no histórico (regra atual do
  modelo).

### 5.2 Pagamento
- Rota `GET|POST /cliente/pagar`; `PedidoDebito(tipo="compra", categoria=<escolhida>, canal="cliente",
  descricao=<comerciante>)`; mesmo padrão da transferência: 503 sem modelos, PRG (303 para o movimento),
  `data-envio-unico`, auditoria `pagamento_cliente`.
- Local: um `<select>` com `<optgroup>` "Moçambique" (cidades) e "Estrangeiro" (países). Valor submetido `MZ:TETE`
  ou `EXT:NG`; o servidor converte em (cidade, país) — para estrangeiro, cidade = cidade do cliente (o modelo só
  usa o país para "país estranho"; evita inventar cidades estrangeiras) — e rejeita valores fora da lista.
- Data/hora simuladas: `<input type="date">` + `<input type="time">`; servidor valida `agora − 30 dias ≤ x ≤ agora`;
  `momento` do movimento = valor simulado. Ignoradas fora do modo demonstração (mesmo se enviadas).
- Início da área do cliente ganha os dois botões: **Pagar** e **Transferir**. Movimentos de compra mostram
  categoria e comerciante.

### 5.3 Estilo C
- `static/estilo.css` reescrito por camadas: tokens (cores, raios, sombras, espaçamentos), base, cabeçalho/faixa,
  navegação em pílulas, cartões, KPIs, listas com ícone, tabelas em cartão, formulários, avisos/flash, etiquetas de
  nível, responsivo (≤ 720 px: navegação em linha que rola; cartões em coluna).
- Templates ajustados: `base.html`, `base_cliente.html` e as páginas principais (alertas, alerta, painel, balcão:
  clientes/ficha, área do cliente: início/pagar/transferir/movimento, entrar). As restantes herdam o novo aspeto sem
  reestruturação.
- Ícones: caracteres/Unicode simples ou SVG inline nos templates (sem ficheiros externos, sem fontes de ícones).
- Contraste AA mantido; foco visível; respeitar `prefers-reduced-motion` em transições.

### 5.4 Pontos de pormenor (decididos na revisão do documento)
- **Datas simuladas no passado:** as features do modelo são causais — cada uma usa só as transações *anteriores*
  do mesmo cliente (`detector/perfis.py`). Um pagamento datado de há 10 dias não "vê" o que veio depois. Não é
  preciso corte extra; um teste confirma (pagamento retroativo não conta compras posteriores na rajada).
- **Onde entra o histórico importado:** só em `autorizacao.autorizar()` (janela de 90 dias). A API debita contas
  através de `autorizar()`, por isso fica coberta; clientes sem conta não têm ficha nem histórico importado. O
  `DIAS_HISTORICO = 30` de `api.py` (clientes sem conta) não muda.
- **Sem contaminação:** o `historico_extra` nunca é gravado (`gravar_analise` só grava as linhas avaliadas).
  Um teste confirma que importar histórico não cria `transacoes_avaliadas`, alertas nem movimentos.
- **Local estrangeiro:** internamente, cidade = cidade do cliente e país = o estrangeiro — é exatamente assim que o
  gerador cria a fraude "país estranho", por isso o modelo reage como no treino. O ecrã mostra só o país (ex.:
  "Nigéria"), nunca "Maputo, Nigéria". O anti-objetivo refere-se ao que o utilizador escolhe e vê.
- **Campos antigos `cidade`/`pais`** da transferência são substituídos por `local`; os testes que os usam são
  reescritos (sem compatibilidade com o formato antigo).
- **CSV do histórico:** duplicada = mesmo cliente, `data_hora` ao segundo, valor em cêntimos, cidade, país e
  categoria. Coluna `cliente` presente e diferente da ficha → ficheiro recusado por inteiro. Limite de 2 000 linhas
  aplicado ao ficheiro (antes de retirar repetidas). `TRANSFERENCIA` e `LEVANTAMENTO` aceites como categoria.
- **Histórico de exemplo:** perfil temporário criado a partir da ficha (cidade, valor típico ~1 200 MT, 3–4
  categorias, pico diurno), passado às funções do gerador se aceitarem um perfil avulso; senão, um gerador pequeno
  próprio com a mesma distribuição. Semente derivada do código do cliente.
- **Pagamento fora do modo demonstração:** categoria e comerciante continuam a ser escolhidos pelo cliente; só o
  local (cidade do cliente) e a hora (agora) são fixos. Comerciante até 140 caracteres; extrato mostra
  "Categoria · Comerciante".
- **Testes e estilo:** é permitido mudar asserções que dependem da marcação HTML (classes, tabelas → listas), nunca
  as que verificam comportamento (estados, saldos, permissões, mensagens).

## 6. Ordem e critérios de conclusão
1. **Histórico importado.** *Concluído quando:* o balcão importa um CSV (ou gera exemplo) para um cliente novo, a
   ficha mostra o resumo, e um pagamento normal desse cliente deixa de vir marcado como `historico_curto`; saldo,
   extrato do cliente, alertas e monitorização inalterados.
2. **Pagamento.** *Concluído quando:* o cliente faz um pagamento normal (autorizado) e, no modo demonstração, um
   pagamento de Eletrónica às 03:00 noutra cidade ou no estrangeiro fica retido/alerta com o motivo certo.
3. **Estilo C.** *Concluído quando:* as páginas principais seguem a direção C, sem scripts inline, sem ficheiros
   externos, e o guião da demonstração corre igual; testes de interface ajustados.

## 7. Deixado ao construtor
- Nomes exatos de módulos/rotas/templates; layout fino de cada página dentro da direção C.
- Distribuição exata do histórico de exemplo, desde que coerente com a ficha e determinística.
- Mensagens de erro de validação (português, no estilo das existentes).
