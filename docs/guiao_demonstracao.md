# Guião da demonstração

Uma história de seis passos que mostra o sistema de ponta a ponta: um cliente novo, o dinheiro a entrar, compras e
transferências normais, uma transferência suspeita que fica **em verificação**, a decisão do analista e o valor
protegido no painel. Demora uns 10 minutos.

Todos os valores são em metical e aparecem como `1.234,50 MT`.

## Antes de começar

- O sistema está instalado (`instalar.bat`) e ligado (`iniciar.bat`), em <http://127.0.0.1:5000>.
- Há um **administrador** (ou, em alternativa, uma conta de Balcão e uma de Analista). O administrador abre o
  balcão e os alertas, por isso serve para tudo, mas na demonstração fica mais claro usar uma conta por papel.
- `MODO_DEMONSTRACAO` está ligado: é o valor por omissão fora de produção. É ele que mostra o campo **A partir de**
  na transferência do cliente (ver o [manual de administração](manual_administracao.md)).
- Use uma janela privada (ou outro navegador) para o cliente, e a janela normal para a equipa: é a melhor prática,
  mesmo que as duas áreas tenham sessões separadas no mesmo navegador.
- Depois de atualizar de uma versão antiga, treine os modelos de novo: `gerir.py treinar --forcar`.

## Os seis passos

### 1. O balcão cria o cliente e deposita 20.000 MT
Em **Clientes → Novo cliente**, preencha o nome, a data de nascimento, o documento (por exemplo, NUIT de 9
dígitos), o telefone, a cidade (**Maputo**) e a morada. Ao gravar, o sistema abre a ficha com o código do cliente
(com os 300 sintéticos, `C0301`) e o número da conta.

Na ficha, em **Depósito**, escreva `20000` e grave.

**O que se vê:** a ficha com o saldo `20.000,00 MT` e, no extrato, o depósito. Quem tem o perfil Balcão não vê
nenhuma página de alertas.

### 2. O balcão cria o acesso do cliente
Na mesma ficha, em **Acesso do cliente**, escreva uma senha temporária e clique **Criar acesso**.

**O que se vê:** "Acesso criado. O cliente muda a senha ao entrar."

### 3. O cliente entra, muda a senha e faz duas transferências pequenas
Na outra janela, abra <http://127.0.0.1:5000/cliente/entrar> e entre com o código do cliente e a senha temporária.
O sistema pede logo uma senha nova. Depois, em **Fazer uma transferência**, envie `150` e depois `75,50` para o
número de conta de outro cliente (por exemplo, o de `C0001`, que está na ficha dele no balcão). Se o quadro "A partir de" aparecer, deixe-o em Maputo.

**O que se vê:** cada transferência abre a página do movimento com **Concluída**. Na página **Início**, o saldo é de
`19.774,50 MT`, igual ao disponível, e a coluna "Em verificação" está a zero.

### 4. De outra cidade, o cliente transfere quase todo o saldo
Em **Fazer uma transferência**, no quadro **A partir de**, escolha a cidade **Tete** (país **MZ**), e transfira
`19700` para a mesma conta. Este campo só existe no modo demonstração; simula o telemóvel do cliente noutra cidade.
Se o movimento não ficar em verificação (o modelo real pode não o reter), veja o conselho no fim deste guião.

**O que se vê:** o movimento fica **Em verificação**. Na página **Início** do cliente, o **saldo** continua `19.774,50 MT`
(nada saiu), mas o **disponível** desce para `74,50 MT` e **Em verificação** mostra `19.700,00 MT`. O destinatário
ainda não recebeu nada.

### 5. O analista assume o alerta e confirma a fraude
Entre como analista. Em **Alertas → Novos** aparece o alerta de **Alto risco**, com os motivos (valor muito acima do
habitual, cidade diferente, quase todo o saldo). Abra-o, **Assuma** o alerta e, depois de "verificar com o
cliente", escolha **Fraude confirmada**, escreva a justificação e **Encerre**.

**O que se vê:** o movimento passa a **Cancelada** ("Fraude confirmada."). Na área do cliente, o disponível volta a
`19.774,50 MT` e a verificação a zero. Se escolhesse **Falso alarme**, a transferência seria executada (um levantamento retido, não: é cancelado, porque o balcão não entregou o dinheiro).

### 6. O painel mostra o valor protegido
Como supervisor, abra o **Painel**.

**O que se vê:** o cartão **Valor protegido** com `19.700,00 MT` (o que foi cancelado por fraude ou recusado por
risco no período) e **Valor retido** a zero. Em **Auditoria** ficam os registos de cada passo, desde o depósito até
ao cancelamento.

## Com o simulador ligado (cliente sintético `C0001`)
O `gerir.py preparar` cria 300 clientes sintéticos, com conta e saldo, iguais aos do treino do modelo. Para ver a
mesma história com tráfego em volta:

1. No balcão, abra a ficha de `C0001` e crie-lhe um acesso (passo 2).
2. Crie uma chave e ligue o simulador (ver o manual de administração):
   `gerir.py criar-chave Simulador` e `simulador.py --chave dtf_... --velocidade 600 --fraude 5`.
3. Entre como `C0001` na área do cliente e atualize a página: as compras do simulador debitam a conta e aparecem
   nos movimentos; no dia 25 (simulado) entra o salário. As compras muito suspeitas ficam **em verificação** e
   chegam à fila de alertas; as de risco acima do corte de recusa (95 por omissão) são recusadas logo.
4. No terminal do simulador, a linha "recusadas por saldo · retidas" conta o que o banco recusou e reteve. Com
   `--tipos conta_esvaziada` o simulador envia só esse tipo de fraude: uma única transferência de 15 a 40 vezes o valor habitual do cliente, de outra cidade.

Para confirmar no fim que nenhum cêntimo se perdeu: `gerir.py verificar-saldos` deve dizer
"Saldos coerentes com os movimentos."

## Esta demonstração é testada
O ficheiro `tests/test_guiao.py` percorre os passos 1 a 6 pela própria aplicação, com uma base de dados temporária,
e confirma em cada passo o que este guião diz (os saldos, os estados e o valor protegido), terminando com
`verificar-saldos` sem diferenças. Corre com o resto dos testes:
`.venv\Scripts\python -m pytest tests\test_guiao.py`. No teste, o nível de risco da transferência do passo 4 é
forçado, para ser sempre "Alto risco"; na demonstração real é o modelo que decide. Se uma transferência não ficar
em verificação, experimente outra cidade ou país em **A partir de**, ou um valor próximo de todo o saldo disponível. Um valor acima do saldo disponível não fica em verificação: é recusado por saldo insuficiente.
