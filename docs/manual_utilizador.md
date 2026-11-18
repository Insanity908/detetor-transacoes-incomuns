# Manual do utilizador

O Detetor de Fraudes Financeiras avalia transações de cartão e marca as que fogem do comportamento habitual de
cada cliente. As transações marcadas entram numa **fila de alertas**, onde a equipa as investiga e decide se são
fraude ou falso alarme.

## Perfis

| Perfil | O que pode fazer |
|---|---|
| **Analista** | Trabalhar alertas (assumir, investigar, comentar, decidir), testar transações, analisar ficheiros, ver perfis de clientes e o estado dos modelos. Vê os alertas novos e os seus, e só as suas análises. |
| **Supervisor** | Tudo o que o analista faz, e ainda: ver todos os alertas e análises, atribuir e reabrir alertas, o **Painel** de indicadores, a **Auditoria**, ajustar os **limiares** e treinar e publicar **versões do modelo**. |
| **Administrador** | Tudo o que o supervisor faz, e ainda gerir **Utilizadores** e as **chaves da API**. |

## Entrar e sair

- Entre com o nome de utilizador e a senha que o administrador lhe deu. Na primeira entrada terá de escolher uma
  senha nova.
- Depois de 5 tentativas erradas seguidas, a conta fica bloqueada 15 minutos.
- A sessão termina sozinha ao fim de 8 horas (configurável). Use **Sair**, no canto superior direito, ao terminar.
- **Senha**, no mesmo canto, muda a sua senha; as suas outras sessões abertas terminam.

## Níveis de risco

Cada transação recebe duas notas de 0 a 100. Por omissão, 50 é o limite de alerta em ambas.

- **Risco**: a probabilidade de fraude estimada pelo classificador, treinado com fraudes conhecidas.
  Acima do limite, a transação é **Alto risco**.
- **Anomalia**: o quanto a transação foge do padrão, segundo um modelo que não usa rótulos (Isolation Forest).
  Acima do limite, e se o cliente tiver histórico, a transação é **Atenção**.
- Abaixo de ambos: **Normal**.

Os **motivos** explicam em linguagem simples o que é estranho: "Valor 8,2 vezes o habitual deste cliente",
"Primeira compra deste cliente fora de Moçambique", "5 compras na última hora", etc.

## Alertas: do início ao fim

Os alertas chegam de duas fontes: os ficheiros analisados e as transações enviadas por outros sistemas pela API
(por exemplo, o simulador). As transações testadas à mão em "Testar transação" não criam alertas.

1. **Receber.** Em **Alertas**, a vista **Novos** mostra os alertas por atribuir, os de maior risco primeiro.
   O sino 🔔 no cabeçalho conta os alertas novos de alto risco que chegaram desde a última vez que o abriu.
   Para acompanhar em tempo real, use **Atualizar automaticamente** (a fila recarrega-se a cada 15 segundos).
2. **Assumir.** Abra o alerta e clique **Assumir este alerta**. Passa a **Em análise** e fica na sua vista **Os meus**.
   Se outra pessoa o assumiu no mesmo instante, o sistema avisa e não sobrepõe.
3. **Investigar.** A ficha mostra a transação, os motivos, os outros alertas do mesmo cliente e uma ligação para o
   **perfil do cliente** (valor habitual, horários, cidades, países e categorias). Registe o que verificou em
   **Comentários**; tudo fica no **histórico** do alerta.
4. **Decidir.** Escolha **Fraude confirmada** ou **Falso alarme**, escreva a justificação (obrigatória) e clique
   **Encerrar alerta**. Se não for consigo, use **Devolver à fila**.

As decisões servem também para treinar as próximas versões do modelo: são o rótulo mais fiável que o sistema tem.

O **supervisor** pode ainda **atribuir** um alerta a uma pessoa e **reabrir** um alerta decidido (com motivo).

## Testar transação

Descreva uma compra (cliente, data e hora, valor, cidade, país, categoria) ou use um dos exemplos.
O resultado mostra o nível, as duas notas e os motivos. Serve para perceber como o sistema reage; não cria alertas.

## Analisar ficheiro

Envie um CSV com as colunas `cliente, data_hora, valor, cidade, pais, categoria` (e, opcionalmente, `id_transacao`),
até 5 MB. São aceites ficheiros com `,` ou com `;` e vírgula decimal (o formato do Excel em português).

- Os erros do ficheiro (coluna em falta, data ou valor inválido) aparecem logo, com o número da linha.
- Ficheiros com 2000 transações ou mais são analisados **em segundo plano**: pode continuar a trabalhar, e a
  página de acompanhamento atualiza-se sozinha. O resultado aparece em **Análises recentes**.
- O resultado mostra as transações por risco, permite ver só os alertas e **descarregar** tudo em CSV para o Excel.
- As transações com alerta entram na fila de alertas.

## Perfil do cliente

Escreva o código do cliente para ver o seu comportamento habitual: compras por dia, valor típico, horas a que
compra, cidades, países e categorias, e as transações dele que foram marcadas.

## Modelos

Mostra a **versão do modelo em uso**, o seu **desempenho recente** (nas transações da API que já têm rótulo,
comparado com o que teve no teste) e o detalhe da avaliação: métricas, fraudes apanhadas por tipo e importância
de cada característica.

Quando o desempenho cai claramente, por exemplo porque apareceu um tipo de fraude novo ou os clientes mudaram
de hábitos, os supervisores veem um aviso em todas as páginas.

## Para supervisores

### Painel
Alertas criados por dia, taxa de falsos alarmes, valor em risco nos alertas abertos, tempo médio e mediano até
à decisão, e o trabalho de cada pessoa. Escolha 7, 30 ou 90 dias.

### Auditoria
Tudo o que foi feito no sistema, quem fez e quando: entradas, análises, consultas e decisões de alertas, mudanças
de contas, de limiares e de versões do modelo. Filtre por utilizador, ação, alvo ou datas. Não é possível alterar
nem apagar registos.

### Limiares de alerta
Em **Modelos → Ajustar** escolha os limites de risco e de anomalia (1 a 99). Antes de gravar, a tabela mostra o
efeito no teste da versão em uso: alertas por mil transações, fraudes apanhadas e falsos alarmes. Subir os limites
dá menos alertas e menos falsos alarmes, mas deixa passar mais fraudes. Os novos limites valem para as transações
avaliadas a partir desse momento.

### Treinar e publicar uma versão nova
1. Em **Modelos**, clique **Treinar nova versão**. O treino junta o histórico às transações recebidas pela API,
   com as decisões dos analistas como rótulos, e demora cerca de um minuto. Nada muda até publicar.
2. Abra a versão candidata: a tabela compara-a com a versão em uso **nas mesmas transações recentes**, também por
   tipo de fraude. O sistema diz se a nova é melhor.
3. Clique **Publicar**. As transações seguintes passam a ser avaliadas pela nova versão.
4. Se correr mal, publique a versão anterior (**Voltar a esta**). Nenhuma versão é apagada.
