# Detetor de Fraudes Financeiras

Sistema web que avalia transações de cartão, marca as que fogem do comportamento habitual de cada cliente e leva
a equipa a investigá-las numa fila de alertas, com auditoria de tudo o que é feito. Funciona em Windows, sem
Docker nem servidor de base de dados, e é alimentado por dados sintéticos: um gerador de transações para o treino
e um simulador que envia transações continuamente pela API, como se viessem de um banco.

## Arranque rápido (Windows)

1. Instale o [Python 3.11 ou mais recente](https://www.python.org/downloads/windows/), com a opção
   **"Add python.exe to PATH"** marcada.
2. Na pasta do projeto, faça duplo clique em **`instalar.bat`**. Cria o ambiente, instala as dependências, prepara
   a base de dados, treina os modelos (1 a 2 minutos) e pede os dados do primeiro administrador.
3. Faça duplo clique em **`iniciar.bat`** e entre no navegador, em <http://127.0.0.1:5000>.

Para ver o sistema a receber transações em tempo real, com o sistema ligado:

```
.venv\Scripts\python gerir.py criar-chave Simulador
.venv\Scripts\python simulador.py --chave dtf_...
```

Os alertas aparecem em **Alertas** (use "Atualizar automaticamente").

## Publicar online (Render, plano gratuito)

O ficheiro `render.yaml` descreve o serviço. No [Render](https://render.com): **New → Blueprint**, escolha este
repositório e, quando pedir, defina **`ADMIN_SENHA`** (a senha do utilizador `admin`). O build treina os modelos;
cada arranque corre `gerir.py nuvem`, que cria a base de dados, os 300 clientes de demonstração e o administrador.

No plano gratuito não há disco persistente: os dados (clientes novos, movimentos, alertas) perdem-se quando o
serviço reinicia ou adormece (após cerca de 15 minutos sem visitas). Serve para demonstrar; para guardar dados,
use um plano com disco e aponte `URL_BANCO` para ele.

## O que faz

| Área | Resumo |
|---|---|
| **Deteção** | Para cada compra, compara o valor, a hora, o local, a categoria e o ritmo com o histórico do cliente. Um classificador (o mais simples entre Regressão logística, Random Forest e Gradient Boosting que fica a 0,02 do melhor F1) dá o **risco**; um Isolation Forest, sem rótulos, dá a **anomalia**. Cada alerta vem com os motivos em linguagem simples. |
| **Alertas** | Fila com os estados Novo → Em análise → Fraude confirmada / Falso alarme. Inclui atribuição, comentários, histórico, notificações (um sino no cabeçalho) e e-mail opcional. Há um painel com alertas por dia, taxa de falsos alarmes, valor em risco e tempo até à decisão. |
| **Utilizadores** | Login com senhas em hash e três perfis (analista, supervisor e administrador). Proteção CSRF e auditoria de todas as ações. |
| **Integração** | API REST (`/api/v1/transacoes`) com chaves, limite de pedidos e documentação OpenAPI. Ficheiros CSV grandes são analisados em segundo plano. |
| **Ciclo do modelo** | Monitorização do desempenho nas transações recentes e re-treino pela interface, com as decisões dos analistas como rótulos. Cada versão pode ser publicada ou desfeita, e os limiares de alerta são ajustáveis. Os modelos são assinados (HMAC) e verificados antes de serem carregados. |
| **Banco de demonstração** | Clientes com conta e saldo, um perfil **Balcão** (fichas, depósitos, levantamentos) e uma **área do cliente** (saldo, movimentos, transferências). Cada saída de dinheiro é avaliada pelo detetor antes de mexer no saldo: autoriza, retém para a equipa decidir (fraude confirmada cancela e repõe o saldo) ou recusa. O painel mostra o valor retido e o protegido. |
| **Simulação** | Compras normais, salários no dia 25 e 6 tipos de fraude (valor alto, país estranho, horário estranho, rajada, conta tomada, conta esvaziada). Há também cenários de mudança (novo tipo de fraude, hábitos, inflação), para demonstrar a monitorização e o re-treino. |

## Documentação

- [Manual de instalação (Windows)](docs/manual_instalacao.md): do Python ao primeiro login, uso na rede local,
  problemas frequentes e atualização.
- [Manual do utilizador](docs/manual_utilizador.md): perfis, níveis de risco, o ciclo de um alerta, ficheiros,
  painel, limiares e versões do modelo.
- [Manual de administração](docs/manual_administracao.md): configuração (`.env`), comandos, contas, API e chaves,
  simulador, ciclo de vida do modelo, cópias de segurança, segurança e desempenho.
- [Guião da demonstração](docs/guiao_demonstracao.md): a história em seis passos, do balcão ao painel, com o
  cliente sintético `C0001` e o simulador.
- [Plano do sistema](PLANO_SISTEMA_COMPLETO.md): as nove fases, do protótipo ao sistema completo.

## Estrutura

```
app.py              aplicação web (Flask): páginas principais e ligação dos módulos
servidor.py         servidor de produção (waitress), usado pelo iniciar.bat
gerir.py            administração: preparar, banco, treinar, verificar, verificar-saldos, criar-admin, desbloquear, criar-chave
treinar.py          treino inicial: gera os dados, treina, avalia e grava os modelos (versão 1)
simulador.py        envia transações simuladas à API, com cenários de mudança
carga.py            teste de carga da API
detector/           o sistema: modelos, features, API, alertas, contas, auditoria, versões, monitorização...
migracoes/          alterações da base de dados (Alembic), aplicadas automaticamente
templates/, static/ interface
tests/              testes (pytest)
docs/               manuais
```

Os dados gerados não vão para o Git: a base de dados e a chave local ficam em `instancia/`, os modelos em
`modelos/`, os dados do treino em `data/`, os registos em `logs/` e a configuração em `.env`.

## Desenvolvimento

```
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest              # cerca de 330 testes, uns 6 minutos
.venv\Scripts\python -m pytest -m carga     # teste de carga com o servidor waitress
.venv\Scripts\python -m ruff check .        # linter
python app.py                               # servidor de desenvolvimento em http://127.0.0.1:5000
```

O GitHub Actions corre o linter e os testes a cada push e pull request, em Windows (Python 3.13) e Linux
(Python 3.11).
