# Funcionalidades bancárias: clientes, contas e autorização de movimentos

Data: 2026-11-18 · Estado: proposta para aprovação

## 1. Intenção

Tornar a **demonstração mais realista**. Hoje o sistema é só o detetor: recebe transações já feitas (ficheiro ou
API) e levanta alertas. O "cliente" é apenas um código (`C0001`) dentro de cada transação; não há pessoas, contas
nem saldos.

Depois desta mudança, quem assiste à demonstração vê um banco a funcionar: um funcionário regista um cliente
novo e abre-lhe uma conta, o dinheiro entra e sai, e o detetor **avalia cada saída de dinheiro antes de ela
acontecer** e pode retê-la. A cena central é: alguém tenta esvaziar a conta de um cliente e o sistema trava a
transferência até um analista decidir.

### Objetivos
- Ficha de cliente e conta à ordem com saldo, criadas por um membro da equipa.
- Área do cliente: entrar, ver saldo e movimentos, transferir para outra conta do banco.
- Toda a saída de dinheiro (compra com cartão, transferência, levantamento) passa pelo detetor **antes** de
  mexer no saldo; o resultado é autorizado, recusado ou retido.
- A decisão do analista num alerta retido executa ou cancela o movimento.
- O simulador passa a debitar contas reais.

### Não-objetivos
- Juros, crédito, poupança, vários tipos de conta, cartões como entidade própria, transferências para outros
  bancos, extratos em PDF, contabilidade (plano de contas, fecho do dia).
- Dados reais: tudo continua sintético.
- Um banco separado do detetor (servidores ou bases de dados distintos).

### Anti-objetivos (falharia mesmo "funcionando")
- **Saldo que não bate certo com os movimentos**, ou dinheiro "pendurado" (alerta decidido mas movimento ainda
  retido, ou o contrário). Numa demonstração, um saldo errado destrói a credibilidade de tudo o resto.
- Um cliente conseguir ver páginas internas, ou dados de outro cliente.
- Funcionário de balcão a ver ou decidir alertas.
- Partir o que já existe: análise de ficheiros, API para clientes sem conta, re-treino, monitorização,
  instalação com `instalar.bat` / `iniciar.bat`.

## 2. Restrições
- Mesmo stack: Flask, SQLAlchemy, SQLite, Alembic (migração `0006_…`), waitress, Windows sem Docker.
- Instalação e arranque inalterados para quem instala (dois scripts, uma porta).
- Interface e documentação em português de Portugal, como o resto do projeto. Moeda: metical (MT).
- Toda a ação que mexe em dinheiro ou dados de clientes fica na auditoria (`EventoAuditoria`).
- Os testes atuais continuam a passar.

## 3. Abordagens consideradas

| | Abordagem | Troca |
|---|---|---|
| **A (escolhida)** | **Banco dentro do sistema atual, como parte separada no código.** Tabelas novas na mesma base de dados; páginas de balcão e de cliente no mesmo servidor; o banco só fala com o detetor através de uma função `autorizar(movimento)`. | Uma instalação, uma porta; decisão do analista e movimento do dinheiro na **mesma transação da base de dados**, por isso o saldo nunca fica inconsistente. A separação é só no código. |
| B | Banco como aplicação à parte que chama a API REST do detetor. | Arquitetura mais fiel, mas duas instalações, um mecanismo de aviso de volta (com repetições) quando o analista decide, e risco de movimentos pendurados entre os dois lados. |

Escolhida pelo utilizador: **A**. Como o banco só usa `autorizar()`, separá-lo mais tarde continua possível.

Decisões anteriores, também do utilizador:
- **Avaliar antes e poder bloquear** (em vez de só alertar depois, ou de um modo configurável).
- **Equipa e área do cliente** (em vez de só balcão, ou só simulador).

## 4. O que foi decidido

### 4.1 Perfis da equipa
- Perfil novo **Balcão**: regista clientes, abre contas, cria o acesso do cliente, faz depósitos e levantamentos.
  **Não vê alertas** nem páginas do detetor.
- Analista, supervisor e administrador continuam como estão; o **administrador pode tudo**, incluindo o balcão.
- Hoje `PERFIS` é uma hierarquia (`analista < supervisor < administrador`, `Utilizador.pode`). O Balcão não cabe
  nela: passa a haver permissões por área (balcão / alertas / administração). Ver 5.1.

### 4.2 Ficha do cliente e conta
- Ficha: nome, data de nascimento, BI ou NUIT, telefone, e-mail, cidade, morada.
- Cada cliente tem um **código de cliente** no formato atual (`C0001`…) — é o que liga a ficha às transações
  avaliadas e ao histórico do modelo — e **uma conta à ordem** com número próprio.
- A conta abre com **saldo 0 MT**.
- A equipa cria o acesso do cliente com **senha temporária**; o cliente muda-a na primeira entrada (como os
  utilizadores da equipa hoje).

### 4.3 Área do cliente
- Entrar, ver saldo e movimentos, transferir para outra conta **do banco**.
- Cada transferência mostra o estado: **concluída**, **recusada** ou **em verificação** (retida).
- Login separado do da equipa; um cliente nunca vê páginas internas.

### 4.4 Autorização de movimentos
- **Saídas** (compra com cartão, transferência, levantamento ao balcão) passam por `autorizar()` antes de mexer
  no saldo:
  - **Autorizado** → o saldo muda logo.
  - **Recusado** → saldo insuficiente, ou risco muito alto.
  - **Retido** → risco alto; fica um alerta na fila. *Fraude confirmada* cancela o movimento; *falso alarme*
    executa-o.
- **Depósitos não passam pelo detetor** (o modelo foi treinado com compras, não com entradas).

### 4.5 Clientes que já existem
- Os **300 clientes sintéticos** do treino recebem ficha e conta, com saldo inicial gerado, para que o simulador
  tenha contas reais a debitar e o modelo continue a reconhecer os seus hábitos.
- Clientes novos, criados ao balcão, começam **sem histórico**; as primeiras compras ficam com `historico_curto`.

### 4.6 Simulador
- Continua a enviar compras pela API; cada compra de um cliente com conta **debita a conta**, e é recusada se
  não houver saldo.

## 5. Propostas minhas (o construtor pode afinar; o utilizador pode corrigir)

### 5.1 Permissões
- `PERFIS` passa a `("balcao", "analista", "supervisor", "administrador")`, mas `exige()` deixa de comparar
  posições e passa a verificar **áreas**: `balcao` → {balcão}; `analista` → {alertas}; `supervisor` →
  {alertas, supervisão}; `administrador` → todas. Os decoradores existentes (`@exige("analista")` etc.) mantêm o
  significado atual para os três perfis antigos; o balcão só passa em `@exige_area("balcao")`.
- O menu mostra só o que o perfil pode abrir. A página inicial do Balcão é a lista de clientes.

### 5.2 Modelo de dados (migração `0006`)
- `clientes`: código (`C0001`, único), nome, data de nascimento, documento (tipo BI/NUIT + número, único), telefone,
  e-mail, cidade, morada, criado_em, criado_por, sintético (bool).
- `contas`: número (único), cliente_id (1:1 por agora), saldo, saldo_retido, estado (ativa/bloqueada), aberta_em.
- `movimentos`: conta_id, tipo (deposito, levantamento, compra, transferencia_enviada, transferencia_recebida),
  valor, estado (autorizado, recusado, retido, cancelado), motivo da recusa, contraparte (conta de destino/origem),
  canal (balcão, cliente, api, simulador), momento, id_transacao (liga à `TransacaoAvaliada`, quando avaliado),
  saldo_apos, criado_por.
- `acessos_cliente`: cliente_id, senha_hash, trocar_senha, tentativas_falhadas, bloqueado_ate, versao_sessao,
  ultimo_acesso — mesmas regras de senha e bloqueio de `contas.py`.
- **Dinheiro em cêntimos (inteiro)**, nunca `float`. A interface mostra MT com duas casas.
- **Saldo guardado e verificável:** `saldo` é atualizado na mesma transação do movimento; um teste (e um comando
  `gerir.py verificar-saldos`) confirma que é igual à soma dos movimentos autorizados.
- **Saldo disponível = saldo − saldo_retido.** Um movimento retido **reserva** o valor (não pode ser gasto duas
  vezes enquanto espera); ao cancelar, a reserva é libertada; ao executar, passa a débito.

### 5.3 `autorizar(movimento)`
Uma função em `detector/` (ex.: `detector/autorizacao.py`), única porta entre o banco e o detetor:
1. Verifica saldo disponível. Sem saldo → **recusado** (não chega ao modelo, não cria alerta).
2. Avalia com o mesmo caminho da API (`avaliar_transacoes` com o histórico guardado do cliente) e grava a
   `TransacaoAvaliada`, como hoje.
3. Decide pelo nível:
   - `Normal` → **autorizado**.
   - `Atenção` (anomalia sem risco alto) → **autorizado com alerta** (como hoje: o analista vê depois).
   - `Alto risco` → **retido** com alerta.
   - Risco ≥ **corte de recusa automática** → **recusado** com alerta já fechado como "recusado
     automaticamente". Corte por omissão **95**, ajustável pelo supervisor na página de Limiares, junto dos
     cortes atuais.
4. Tudo (avaliação, alerta, movimento, saldo) numa só transação de base de dados.

Para o modelo, um movimento é uma transação com os campos de sempre:
- **Compra com cartão:** cidade, país e categoria vêm do pedido (como hoje na API).
- **Transferência:** categoria `TRANSFERENCIA`; cidade e país de onde o cliente está (ver 5.5).
- **Levantamento:** categoria `LEVANTAMENTO`; cidade do balcão (a do funcionário ou escolhida no ecrã).

Como o modelo atual nunca viu `TRANSFERENCIA` nem `LEVANTAMENTO`, o gerador sintético passa a criá-los
(transferências e levantamentos legítimos com taxa e valores plausíveis, e uma fraude nova **"conta esvaziada"**:
transferência de quase todo o saldo, de outra cidade, para uma conta com que o cliente nunca falou) e os modelos
são **re-treinados** na instalação. Sem features novas nesta fase: o modelo usa as que já tem (`categoria_nova`,
`cidade_nova`, `valor_vs_habitual`…). Features como "destinatário novo" e "fração do saldo" ficam para depois.

### 5.4 Decisão do analista
- `alertas.decidir()` passa a, na mesma transação, chamar o banco: `fraude` → movimento **cancelado** (liberta a
  reserva); `falso_alarme` → movimento **executado** (passa a débito; se for transferência, credita o destino).
- **Reabrir** um alerta já decidido **não mexe no dinheiro**: o movimento já foi executado ou cancelado; a ficha
  do alerta avisa disso. (Estornos ficam fora do âmbito.)
- Um movimento retido **não expira sozinho**; fica em verificação até alguém decidir. A ficha do alerta mostra a
  conta, o saldo e o estado do movimento.
- Se, ao executar um falso alarme, a conta de destino tiver sido entretanto bloqueada, o movimento é cancelado
  com motivo explícito.

### 5.5 Área do cliente
- URL própria (`/cliente/...`), login pelo **código de cliente** e senha, sessão com chave própria
  (`cliente_id`), separada da da equipa (`uid`); uma sessão da equipa não abre páginas de cliente e vice-versa.
  Mesmas proteções: CSRF, bloqueio após 5 tentativas, impressão da conta na sessão, fim de sessão por inatividade.
- Páginas: saldo e movimentos (com estado), transferir (conta de destino, valor, descrição), mudar senha.
- **Modo demonstração:** o formulário de transferência tem um campo "a partir de" (cidade e país) para o
  apresentador simular que quem transfere está noutro sítio — é assim que se mostra a "conta tomada" ao vivo.
  Visível só quando `AMBIENTE` não é `producao` (ou com uma opção no `.env`, `MODO_DEMONSTRACAO=1`).
- Aspeto: o mesmo CSS da aplicação, com cabeçalho diferente (nome do banco fictício) para se distinguir das
  páginas internas.

### 5.6 Balcão
- Clientes: lista com pesquisa (nome, código, documento), ficha, criar, editar contactos.
- Abrir conta (automático ao criar o cliente), bloquear/desbloquear conta, criar ou redefinir o acesso do cliente.
- Depósito (sem detetor) e levantamento (com detetor; se ficar retido, o ecrã diz "em verificação, não entregue o
  dinheiro").
- Extrato da conta no ecrã.

### 5.7 API e simulador
- `POST /api/v1/transacoes` mantém o formato. Para um `cliente` **com conta**, cada transação passa a ser um
  movimento de compra autorizado por `autorizar()`, e a resposta ganha, por transação, `movimento:
  {estado, motivo}`. Para clientes **sem conta**, comportamento igual ao de hoje (só avaliação) — mantém a
  compatibilidade com `carga.py`, com os testes e com quem usa a API sem banco.
- Endpoint novo `POST /api/v1/depositos` (mesma chave de API) para entradas de dinheiro vindas de fora.
- O simulador:
  - deposita um **salário** mensal (dia 25 simulado) em cada conta, proporcional ao gasto típico do cliente, e dá
    a cada conta um saldo inicial de cerca de três meses de gastos;
  - mostra no resumo também as **compras recusadas por falta de saldo** e as **retidas**;
  - ganha a fraude "conta esvaziada" como tipo opcional, via API de transferências se for preciso (ver 7).

### 5.8 Clientes sintéticos
- `gerir.py preparar` (e o `instalar.bat`) cria as fichas e contas dos 300 clientes do treino, com nomes e
  documentos fictícios gerados de forma determinística (mesma semente), marcados como `sintético`.
- Os códigos dos clientes novos continuam a sequência (`C0301`, …), sem colidir com os sintéticos.

### 5.9 Painel e auditoria
- Painel: acrescentar **valor retido** (em verificação) e **valor protegido** (movimentos cancelados como fraude).
- Auditoria: `cliente_criado`, `cliente_alterado`, `conta_aberta`, `conta_bloqueada`, `deposito`,
  `levantamento`, `acesso_cliente_criado`, `senha_cliente_redefinida`, `movimento_executado`,
  `movimento_cancelado`; entradas e saídas de clientes na área do cliente.

### 5.10 Pontos de pormenor (decididos na revisão do documento)
- **Ordem num lote da API:** as transações de um pedido são autorizadas por ordem de `data_hora` (empate: ordem
  no pedido), uma a uma, porque cada débito muda o saldo disponível para a seguinte. A avaliação pelo modelo pode
  continuar em lote; só a decisão sobre o saldo é sequencial.
- **Momento do movimento:** o `momento` de um movimento vindo da API é a `data_hora` da transação (tempo
  simulado), para que o extrato do cliente acompanhe o simulador; `registado_em` guarda a hora real.
- **Acesso dos clientes sintéticos:** não recebem login automaticamente; o Balcão cria-o quando for preciso
  (por exemplo, para entrar como `C0001` na demonstração). O guião da Fase 9 inclui esse passo.
- **Avisos ao cliente:** nenhum e-mail ou SMS ao cliente nesta fase; ele vê o estado "em verificação" na área do
  cliente. Não pode cancelar um movimento retido.
- **Voltar a uma versão antiga do modelo** (anterior ao re-treino com transferências e levantamentos) continua
  possível; essa versão trata `TRANSFERENCIA`/`LEVANTAMENTO` como categoria nova, o que dá mais alertas. A página
  de versões avisa disso.
- **Transferência recebida** não passa pelo detetor e não entra no histórico de compras do destinatário.

## 6. Fases propostas (continuação do `PLANO_SISTEMA_COMPLETO.md`)

**Fase 7 — Clientes, contas e balcão.** Migração 0006, perfil Balcão e permissões por área, fichas e contas,
depósitos, clientes sintéticos com conta, extrato no ecrã, auditoria.
*Concluída quando:* um funcionário de balcão cria um cliente, abre-lhe a conta, deposita, e o saldo bate com os
movimentos; o balcão não abre nenhuma página de alertas.

**Fase 8 — Autorização antes do movimento.** `autorizar()`, levantamentos e compras pela API a debitar contas,
retenção e reserva, decisão do analista a executar/cancelar, corte de recusa automática, gerador com
transferências e levantamentos e re-treino, painel com valor retido/protegido.
*Concluída quando:* com o simulador ligado, as compras debitam contas, uma compra de alto risco fica retida, e o
analista a liberta ou cancela com o saldo certo no fim.

**Fase 9 — Área do cliente e cena de demonstração.** Login de cliente, saldo, movimentos, transferências,
modo demonstração ("a partir de"), salário no simulador, manuais atualizados com um guião da demonstração.
*Concluída quando:* o guião corre de ponta a ponta — cliente novo, depósito, compras normais, transferência
"de outra cidade" que esvazia a conta fica em verificação, analista confirma fraude, saldo intacto.

## 7. Deixado ao construtor
- Nomes exatos de módulos, rotas e templates; formato do número de conta.
- Validação dos documentos (NUIT de 9 dígitos é a sugestão; BI com formato livre ou 12 dígitos + letra).
- Como o simulador faz as transferências fraudulentas (endpoint de transferências na API ou só pela área do
  cliente na demonstração ao vivo).
- Taxas e valores das transferências e levantamentos legítimos no gerador, desde que plausíveis e que o re-treino
  mantenha as métricas atuais nas compras.
- Nome do banco fictício e aspeto do cabeçalho da área do cliente.
