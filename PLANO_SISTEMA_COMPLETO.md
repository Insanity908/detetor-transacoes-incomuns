# Plano: do protótipo a um sistema completo

O ponto de partida é uma aplicação Flask que já funciona e tem 129 testes passando,
mas que usa dados sintéticos, tem um único usuário e guarda os resultados só na memória.

**Premissas deste plano:**
- **Sem Docker:** o sistema é instalado e executado diretamente no Windows, com Python e um ambiente virtual (`.venv`).
- **Sem dados reais:** tudo funciona com os dados sintéticos do gerador que o projeto já tem (`detector/gerador.py`),
  ampliado com um simulador que produz transações continuamente, como se viessem de um banco.

## Fase 1 — Infraestrutura
**Objetivo:** o sistema deixa de depender de arquivos soltos e guarda tudo de forma permanente.
- Banco de dados **SQLite** (um único arquivo, não precisa instalar nada) para clientes, transações, alertas e resultados.
  O acesso é feito com SQLAlchemy, para poder trocar por PostgreSQL no futuro sem reescrever o código.
- Gestão das mudanças no banco com Alembic.
- Configuração num arquivo `.env`, com ambientes de desenvolvimento, teste e produção.
- Servidor de produção **waitress** (funciona no Windows) e um script `iniciar.bat` que liga o sistema com um duplo clique.
- Script `instalar.bat` que cria o `.venv`, instala as dependências, cria o banco e treina os modelos.
- Registo de eventos (logs) num arquivo e uma página que indica se o sistema está funcionando (`/saude`).

**Concluída quando:** o sistema é instalado e iniciado com dois scripts e os dados continuam lá depois de reiniciar.

## Fase 2 — Usuários e segurança
**Objetivo:** só pessoas autorizadas acedem, e cada uma vê apenas o que lhe compete.
- Login com senhas guardadas com hash.
- Três perfis: analista, supervisor e administrador.
- Proteção CSRF em todos os formulários.
- Auditoria: quem consultou ou decidiu cada alerta, e quando.
- Verificação de integridade dos arquivos de modelo antes de os carregar.

**Concluída quando:** nenhuma página abre sem login e todas as ações ficam registadas.

## Fase 3 — Gestão de alertas e casos
**Objetivo:** transformar os alertas num fluxo de trabalho para a equipe. É a fase que mais aproxima o projeto de um sistema completo.
- Fila de alertas com estados: Novo → Em análise → Fraude confirmada / Falso alarme.
- Atribuição de cada alerta a um analista, com comentários e histórico.
- Painel de indicadores: alertas por dia, taxa de falsos alarmes, valor em risco e tempo médio até a decisão.
- Notificações dentro do próprio sistema (um sino com os alertas novos de alto risco).
  E-mail fica opcional, configurável no `.env`.

**Concluída quando:** um analista consegue receber, investigar e encerrar um alerta do início ao fim dentro do sistema.

## Fase 4 — Integração e simulação em tempo real
**Objetivo:** o sistema recebe transações automaticamente, como num ambiente real, mas alimentado por dados simulados.
- API REST (`POST /api/v1/transacoes`) com chaves de acesso e documentação OpenAPI (Swagger).
- **Simulador de transações** (`simulador.py`): usa o gerador sintético para enviar transações à API continuamente,
  misturando compras normais com os 5 tipos de fraude que o gerador já conhece. Velocidade e percentagem de fraude ajustáveis.
- Processamento em segundo plano de arquivos CSV grandes, com uma fila simples em Python (sem Redis).
- Limite de pedidos por chave da API e validação rigorosa dos dados.

**Concluída quando:** com o simulador ligado, as transações chegam, são avaliadas e os alertas aparecem na fila em tempo real.

## Fase 5 — Ciclo de vida do modelo
**Objetivo:** o modelo pode ser re-treinado, comparado e substituído pela interface.
- Re-treino a partir da interface, usando os dados sintéticos acumulados no banco
  e as decisões dos analistas da Fase 3 como rótulos (confirmam ou corrigem o que o gerador marcou).
- Registo de versões dos modelos numa tabela do banco, com comparação de métricas e possibilidade de voltar a uma versão anterior.
- **Cenários de mudança no simulador:** novos tipos de fraude, mudança nos hábitos dos clientes ou nos valores.
  Servem para demonstrar que a monitorização deteta a queda de desempenho e que o re-treino a corrige.
- Limiares de risco ajustáveis pelo supervisor na interface.

**Concluída quando:** ao ativar um cenário de mudança, o sistema avisa que o desempenho caiu,
um novo modelo é treinado e publicado pela interface e o desempenho volta a subir.

## Fase 6 — Qualidade e entrega
**Objetivo:** garantir que o sistema se mantém confiável à medida que evolui.
- Integração contínua (GitHub Actions) para rodar o pytest e o linter (ruff) a cada alteração.
- Testes de carga usando o próprio simulador.
- Modelos, banco e dados gerados fora do Git, com um `.gitignore` adequado.
- Manual do usuário, manual de instalação no Windows e manual de administração.

**Concluída quando:** cada alteração é testada automaticamente e qualquer pessoa consegue instalar o sistema seguindo o manual.

---

**Ordem recomendada:** fases 1, 2 e 3 primeiro, porque dão uma ferramenta completa que a equipe consegue usar no dia a dia.
Depois vem a Fase 4, que dá vida ao sistema com o simulador, e a Fase 5, que usa o simulador para mostrar o modelo a evoluir.
A Fase 6 deve acompanhar todas as outras.
