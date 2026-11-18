# Manual de administração

Para quem instala, configura e mantém o sistema. A instalação está no [manual de instalação](manual_instalacao.md)
e o uso diário no [manual do utilizador](manual_utilizador.md). Os comandos indicados correm na pasta do projeto,
com o Python do ambiente: `.venv\Scripts\python ...`.

## Onde está cada coisa

| Caminho | Conteúdo | No Git? |
|---|---|---|
| `.env` | Configuração e `CHAVE_SECRETA` desta instalação | Não (segredo) |
| `instancia\detetor.db` | Base de dados SQLite: contas, análises, alertas, auditoria, versões | Não |
| `instancia\envios\` | Ficheiros grandes à espera da análise em segundo plano | Não |
| `modelos\` | Versão 1 dos modelos (treino inicial) | Não |
| `modelos\versoes\vN\` | Versões treinadas pela interface | Não |
| `data\` | Dados sintéticos gerados no treino | Não |
| `logs\detetor.log` | Registo de eventos (roda aos 2 MB, guarda 5 cópias) | Não |
| `migracoes\` | Alterações da base de dados (Alembic) | Sim |

## Configuração (`.env`)

Depois de mudar o `.env`, reinicie o sistema. Um valor inválido impede o arranque com uma mensagem que diz qual é.

| Variável | Por omissão | Para que serve |
|---|---|---|
| `AMBIENTE` | `producao` | `desenvolvimento`, `teste` ou `producao`. Em produção a `CHAVE_SECRETA` é obrigatória. |
| `CHAVE_SECRETA` | gerada na instalação | Assina sessões, formulários e os modelos. Mínimo 32 caracteres. Se mudar, todas as sessões terminam e os modelos têm de ser treinados de novo. |
| `URL_BANCO` | SQLite em `instancia\` | Outra base, por exemplo PostgreSQL (`postgresql+psycopg://...`, exige instalar o `psycopg`). |
| `PASTA_MODELOS`, `PASTA_LOGS`, `PASTA_ENVIOS` | `modelos`, `logs`, `instancia/envios` | Pastas relativas ao projeto ou caminhos completos. |
| `NIVEL_LOG` | `INFO` | `DEBUG`, `INFO`, `WARNING` ou `ERROR`. |
| `HOST`, `PORTA`, `THREADS` | `127.0.0.1`, `5000`, `4` | Endereço do servidor. `0.0.0.0` aceita ligações da rede local. |
| `SESSAO_HORAS` | `8` | Horas até a sessão expirar. |
| `LINHAS_SEGUNDO_PLANO` | `2000` | Ficheiros com este número de transações ou mais são analisados em segundo plano. |
| `SMTP_SERVIDOR`, `SMTP_PORTA`, `SMTP_UTILIZADOR`, `SMTP_SENHA`, `SMTP_REMETENTE`, `SMTP_TLS` | vazio, `587`, …, `sim` | E-mail opcional (ver abaixo). |
| `EMAIL_ALERTAS` | vazio | Destinatários do resumo de alertas, separados por vírgulas. |
| `URL_PUBLICA` | `http://HOST:PORTA` | Endereço usado nos links do e-mail. |

### E-mail
Com `SMTP_SERVIDOR` e `EMAIL_ALERTAS` preenchidos, cada ficheiro analisado que gere alertas de alto risco envia um
resumo (só contagens e o link, nunca dados de clientes). O envio corre em segundo plano; se o servidor de e-mail
falhar, a análise continua e a falha fica no log.

## Comandos de administração (`gerir.py`)

| Comando | O que faz |
|---|---|
| `gerir.py preparar` | Cria o `.env` (se faltar), atualiza a base, treina os modelos (se faltarem) e pede o primeiro administrador. É o que o `instalar.bat` corre. |
| `gerir.py banco` | Cria ou atualiza as tabelas. As migrações mantêm os dados. |
| `gerir.py treinar [--forcar]` | Treina a versão 1 dos modelos (com `--forcar`, mesmo que já existam). |
| `gerir.py verificar` | Mostra a configuração e se o banco, os modelos e as contas estão prontos. |
| `gerir.py criar-admin` | Cria uma conta de administrador (pede nome e senha). |
| `gerir.py desbloquear NOME` | Reativa uma conta e limpa o bloqueio por tentativas falhadas. |
| `gerir.py criar-chave NOME [--limite N]` | Cria uma chave da API e mostra-a uma única vez. |

## Contas

Em **Utilizadores** (administrador): criar contas com uma senha temporária, mudar o nome e o perfil, desativar e
redefinir a senha. Uma conta criada ou redefinida tem de mudar a senha na entrada seguinte. Mudar o perfil,
desativar ou redefinir a senha termina as sessões abertas dessa pessoa.

O sistema nunca fica sem administrador: não deixa desativar nem rebaixar o último. Se perder o acesso de
administrador, crie outro com `gerir.py criar-admin`.

## API e chaves

Outros sistemas enviam transações para `POST /api/v1/transacoes` com uma chave no cabeçalho
`Authorization: Bearer dtf_...`. A documentação completa está em `/api/docs` (com o sistema ligado) e a
especificação OpenAPI em `/api/v1/openapi.json`, que abre no Swagger Editor ou no Postman.

- **Chaves**: em **API** (administrador) ou com `gerir.py criar-chave`. Cada sistema deve ter a sua. Só o SHA-256
  fica guardado: se perder uma chave, revogue-a e crie outra.
- **Limite**: cada chave tem um máximo de pedidos por minuto (600 por omissão). Acima dele, a resposta é `429`.
- **Validação**: campos desconhecidos ou inválidos são recusados com `422` e a lista de problemas; nesse caso,
  nada do pedido é gravado. Reenviar o mesmo `id_transacao` devolve o resultado já gravado, sem o avaliar de novo.

## Simulador

O `simulador.py` faz de banco: envia compras normais e fraudes à API continuamente, com os mesmos clientes do
treino.

```
.venv\Scripts\python gerir.py criar-chave Simulador
.venv\Scripts\python simulador.py --chave dtf_...
```

| Opção | Efeito |
|---|---|
| `--velocidade 60` | Segundos simulados por segundo real (60 = um dia em 24 minutos). |
| `--fraude 2` | Percentagem de transações fraudulentas. |
| `--tipos rajada,valor_alto` | Só estes tipos de fraude. |
| `--cenario novo_tipo` | Mudança de comportamento: `novo_tipo` (fraude fracionada, que o modelo não conhece), `habitos` (compras seis horas mais tarde e em categorias novas) ou `valores` (inflação de 2,5 vezes). |
| `--duracao 300` | Pára ao fim de 300 segundos reais. |

O simulador mostra periodicamente quantas fraudes injetou e quantas o sistema apanhou. Envia também o que o
gerador marcou como fraude: serve de rótulo à monitorização e ao re-treino, mas nunca aparece aos analistas.

## Ciclo de vida do modelo

- **Versões**: a versão 1 é a pasta `modelos\`. Cada re-treino pela interface cria `modelos\versoes\vN`.
  Publicar troca o modelo em uso sem reiniciar o servidor; publicar uma versão anterior volta atrás.
  As versões nunca são apagadas pelo sistema.
- **Re-treino**: usa o histórico do treino e as transações da API com rótulo (a decisão do analista ou, sem ela, o
  rótulo do gerador). Valida e testa nas transações mais recentes e compara a candidata com a versão em uso nos
  mesmos dados. Precisa de pelo menos 300 transações da API com rótulo e de 5 fraudes nos períodos recentes.
- **Monitorização**: nas últimas 1000 transações da API com rótulo, avaliadas pela versão em uso. Avisa quando o
  F1 cai 0,10, o recall cai 15 pontos (com pelo menos 10 fraudes) ou os falsos alarmes por mil compras legítimas
  duplicam.
- **Integridade**: cada versão tem um `integridade.json` com o SHA-256 dos ficheiros, assinado com a
  `CHAVE_SECRETA`. Ficheiros alterados ou de outra instalação são recusados antes de serem lidos (os modelos usam
  `pickle`, que executa código ao ser lido). Nunca copie modelos de fontes em que não confia.

Testado com o simulador: no cenário `novo_tipo` a monitorização avisa e a versão retreinada melhora o F1 nas
transações recentes (de 0,35 para 0,50 no teste feito). No cenário `valores` o aviso aparece, mas o re-treino só
corrige em parte, porque o "valor habitual" de cada cliente usa todo o histórico.

## Cópias de segurança

Com o sistema **desligado**, copie para outro disco:

- `instancia\` (base de dados; inclui os ficheiros `-wal` e `-shm`, se existirem);
- `modelos\` (todas as versões);
- `.env` (sem a mesma `CHAVE_SECRETA`, as cópias dos modelos são recusadas).

Para restaurar, ponha os três no lugar com o sistema desligado e ligue de novo. Guarde as cópias num sítio
protegido: contêm dados de clientes e o segredo da instalação.

## Segurança

- Senhas guardadas só como hash (scrypt). Bloqueio de 15 minutos depois de 5 tentativas falhadas.
- Todos os formulários têm proteção CSRF. A API usa só chaves, sem cookies.
- Cabeçalhos de segurança (CSP, `X-Frame-Options`, `no-store` nas páginas com dados).
- Auditoria de todas as ações, sem possibilidade de alterar ou apagar registos pela interface.
- **Sem HTTPS próprio.** Numa rede local de confiança basta `HOST=0.0.0.0`. Para acesso de fora, coloque o sistema
  atrás de um servidor com HTTPS (IIS, Caddy ou nginx) e mantenha `HOST=127.0.0.1`.

## Desempenho

Medido num portátil com o teste de carga (`carga.py`) e os modelos de 300 clientes:

| Carga | Transações/s | Tempo por pedido (mediana / p95) |
|---|---|---|
| 1 cliente, 1 transação por pedido | 8 | 112 / 171 ms |
| 4 clientes, lotes de 20 | 45 | 1,7 / 2,2 s |
| 8 clientes, lotes de 20 | 43 | 3,6 / 4,5 s |

O limite é de cerca de 45 transações por segundo, porque a avaliação usa um núcleo do processador de cada vez.
É cerca de 90 vezes o ritmo do simulador por omissão. Para repetir a medida numa instalação de teste
(o teste grava as transações na base):

```
.venv\Scripts\python gerir.py criar-chave Carga --limite 60000
.venv\Scripts\python carga.py --chave dtf_... --clientes 4 --lote 20 --duracao 60
```

## Desenvolvimento e qualidade

```
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest              # todos os testes (cerca de 6 minutos)
.venv\Scripts\python -m pytest -m carga     # teste de carga com o servidor waitress
.venv\Scripts\python -m ruff check .        # linter
```

O GitHub Actions (`.github/workflows/ci.yml`) corre o linter e os testes a cada push e pull request, em Windows
(Python 3.13) e em Linux (Python 3.11, a versão mínima).

Alterações às tabelas: mude `detector/banco.py` e crie uma migração nova em `migracoes/versions/`. As migrações
correm com as chaves estrangeiras desligadas (no SQLite, alterar uma tabela é recriá-la) e o teste
`tests/test_migracoes.py` confirma que nenhuma apaga dados.
