# Manual de instalação (Windows)

Este manual leva-o de um computador com Windows sem nada instalado até ao sistema a funcionar no navegador.
Não é preciso Docker nem nenhum servidor de base de dados: tudo corre num ambiente Python dentro da pasta do projeto.

## 1. O que é preciso

| Requisito | Notas |
|---|---|
| Windows 10 ou 11 (64 bits) | Também funciona em Linux, mas os scripts `.bat` são para Windows. |
| Python 3.11 ou mais recente | Testado com 3.13; o CI testa também a 3.11. |
| 2 GB de espaço livre | Dependências (~1 GB), modelos e base de dados. |
| Ligação à internet | Só durante a instalação, para descarregar as dependências. |

## 2. Instalar o Python

1. Descarregue o instalador em <https://www.python.org/downloads/windows/> (versão 3.11 ou mais recente).
2. No primeiro ecrã do instalador, **marque a opção "Add python.exe to PATH"**. Sem ela o `instalar.bat` não encontra o Python.
3. Clique em "Install Now".

Para confirmar, abra a Linha de Comandos (tecla Windows, escreva `cmd`) e execute `py --version`.
Deve aparecer `Python 3.11` ou mais recente.

## 3. Copiar o projeto

Copie a pasta do projeto para o computador, por exemplo para `C:\DetetorFraudes`.
Com Git: `git clone <endereço do repositório> C:\DetetorFraudes`.

Evite pastas sincronizadas (OneDrive, Google Drive): a base de dados é um ficheiro que muda a toda a hora.

## 4. Instalar

Na pasta do projeto, faça duplo clique em **`instalar.bat`**. O script:

1. Cria o ambiente Python `.venv` dentro da pasta do projeto.
2. Instala as dependências de `requirements.txt` (pode demorar alguns minutos na primeira vez).
3. Prepara o sistema (`python gerir.py preparar`):
   - cria o ficheiro de configuração `.env` com uma **chave secreta nova**, única desta instalação;
   - cria a base de dados em `instancia\detetor.db`;
   - gera os dados sintéticos e **treina os modelos** (1 a 2 minutos);
   - **pede os dados do primeiro administrador**: nome de utilizador (por omissão `admin`), nome e senha
     (pelo menos 10 caracteres; não aparece enquanto escreve).

No fim aparece `Instalação concluída`. Se algum passo falhar, a mensagem de erro diz qual; veja a secção 8.

Pode executar o `instalar.bat` as vezes que quiser: o que já existe (`.env`, base de dados, modelos, contas) é mantido.

## 5. Ligar o sistema

Faça duplo clique em **`iniciar.bat`**. Abre-se uma janela preta com a linha
`Detetor de Fraudes a funcionar em http://127.0.0.1:5000` e o navegador abre a página de entrada.

- Entre com a conta de administrador criada na instalação.
- **Mantenha a janela preta aberta** enquanto usa o sistema. Para o desligar, feche-a ou carregue em Ctrl+C.
- Para criar as contas da equipa, veja o [manual de administração](manual_administracao.md#contas).

## 6. Usar a partir de outros computadores da rede

Por omissão, o sistema só aceita ligações do próprio computador. Para a equipa o usar a partir da rede local:

1. Edite o `.env` (com o Bloco de Notas) e mude `HOST=127.0.0.1` para `HOST=0.0.0.0`.
2. Reinicie o sistema (feche a janela e volte a abrir o `iniciar.bat`).
3. Na primeira vez, o Windows pergunta se autoriza o Python na Firewall: autorize só em **redes privadas**.
4. Nos outros computadores, abra `http://<nome-ou-IP-deste-computador>:5000`.
   O IP vê-se com o comando `ipconfig` (linha "Endereço IPv4").

O sistema não usa HTTPS. Use-o só numa rede interna de confiança; para o expor fora dela, coloque-o atrás de um
servidor com HTTPS (veja o manual de administração, secção Segurança).

## 7. Verificar que está tudo bem

Na pasta do projeto, abra a Linha de Comandos e execute:

```
.venv\Scripts\python gerir.py verificar
```

Deve mostrar `Banco: ... -> ok`, `Modelos: ... -> ok` e pelo menos uma conta.
Com o sistema ligado, o endereço `http://127.0.0.1:5000/saude` responde com `"estado": "ok"`.

## 8. Problemas frequentes

| Sintoma | Solução |
|---|---|
| `[ERRO] Python nao encontrado` | Reinstale o Python com a opção "Add python.exe to PATH" marcada e abra uma nova janela. |
| `E preciso o Python 3.11 ou mais recente` | Instale uma versão mais recente; a antiga pode ficar instalada. |
| A instalação falha a descarregar dependências | Verifique a ligação à internet (ou o proxy da empresa) e execute o `instalar.bat` de novo. |
| A janela do `iniciar.bat` fecha logo com um erro de porta | Outro programa usa a porta 5000. Mude `PORTA=` no `.env` (por exemplo 5050). |
| A página diz "Os modelos ainda não foram treinados" | Execute `.venv\Scripts\python gerir.py treinar`. Se o log disser "assinatura não confere", a `CHAVE_SECRETA` mudou: use `gerir.py treinar --forcar`. |
| "Ainda não existe nenhuma conta" | Execute `.venv\Scripts\python gerir.py criar-admin`. |
| Conta bloqueada por tentativas falhadas | Espere 15 minutos ou execute `.venv\Scripts\python gerir.py desbloquear <nome>`. |
| Qualquer outro erro | Veja `logs\detetor.log`: cada erro fica lá registado com os detalhes. |

## 9. Atualizar para uma versão nova

1. Feche o sistema.
2. Faça uma cópia de segurança (manual de administração, secção Cópias de segurança).
3. Substitua os ficheiros do programa pelos novos (ou `git pull`). **Não apague** `.env`, `instancia\` nem `modelos\`.
4. Execute o `instalar.bat`: instala as dependências novas e atualiza a base de dados (as migrações correm sozinhas
   e mantêm os dados).
5. Ligue com o `iniciar.bat`.

## 10. Desinstalar

Feche o sistema e apague a pasta do projeto. Nada foi instalado fora dela, exceto o próprio Python.
