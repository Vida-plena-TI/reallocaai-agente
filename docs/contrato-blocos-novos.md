# Contrato dos blocos novos (versão 1)

Para o time do front/backend do chatbot. Descreve os blocos que saem de `gerar_relatorio` (tool
do agente) e de `POST /relatorios/gerar`: `disponibilidade`, `convenio`, `agenda_paciente`,
`qualidade_agenda` e `tabela_generica`. O plano semanal (`planejar_semana`) e os pacientes por
plano (`pacientes_por_plano`) saem só pelo chat, também como `tabela_generica`.

A fonte da verdade é o schema
[`exemplos-blocos/bloco_relatorio.schema.json`](exemplos-blocos/bloco_relatorio.schema.json),
gerado do modelo `BlocoRelatorio` (`app/ai/relatorios.py`). Há um exemplo real de cada bloco em
[`exemplos-blocos/`](exemplos-blocos/). Um teste falha se o schema ou os exemplos ficarem
desatualizados (regenerar: `uv run python scripts/gerar_exemplos_blocos.py`).

## Regras gerais

| Regra | Detalhe |
| --- | --- |
| **Percentual é fração** | `0.8` = 80%. Vale para `meta`, para a métrica `ocupacao` e para todo valor com `formato: "percentual"`. Não é limitado a 1: acima de 1 indica inconsistência na planilha. O texto pronto para exibir está em `resumo[].exibicao` (ex.: `"23,6%"`). |
| **Datas ISO** | `AAAA-MM-DD` (`periodo.inicio` e `periodo.fim` quando `periodo` não é `null`, `dias_nao_lidos[]`, todo campo `data` e toda coluna com `formato: "data"`). Horários são `HH:MM`. |
| **Posto 1-based** | `posto` é o número de exibição, contado a partir de 1 (`>= 1` no schema). |
| **`meta` sempre presente** | O campo nunca é omitido. É `null` quando não se aplica: sempre em `disponibilidade`, `convenio`, `agenda_paciente` e `qualidade_agenda`; em `tabela_generica`, é `0.8` quando o relatório pede `ocupacao` ou `slots_para_meta` e `null` nos demais. No schema: `required` e `number \| null`. |
| **Booleanos JSON** | Valores booleanos (em `dados` e nas linhas das tabelas) saem como `true`/`false` do JSON, nunca como texto (`"Sim"`, `"Não"`, `"true"`). As colunas booleanas das tabelas (`abaixo_da_meta`, `aguardando_autorizacao`) vêm com `formato: "texto"`, porque o enum de formatos não tem booleano: o front decide como exibir. |
| **Especialidade é rótulo** | Em todos os blocos novos, todo campo e toda coluna `especialidade` traz o rótulo legível (`"Terapia Ocupacional"`), nunca o identificador (`terapia_ocupacional`). Sem especialidade: `null` em `profissional.especialidade` (dados), `"—"` nas colunas das tabelas prontas e `"Sem especialidade"` como valor da dimensão em `tabela_generica`. Exceção: em `disponibilidade` a especialidade nunca é vazia: sem cadastro, vale a da grade e, se nem ela existir, `"Não informada"`. |
| **Tipo desconhecido** | O front ignora bloco de `tipo` que não conhece (não quebra a tela nem a resposta). Blocos novos são aditivos na versão 1. |
| **Tipo novo exige aviso prévio** | Um tipo novo de bloco só entra depois de avisar o time do chatbot: ele pode conter dado de paciente, e o front precisa decidir como exibir, guardar e exportar antes de recebê-lo. |

Privacidade: `agenda_paciente` é o único bloco que cita um paciente (nome e convênio), e só o
paciente pedido pelo nome. Os demais trazem apenas contagens. IDs de profissional
(`profissional.id`) são hashes opacos.

## Envelope comum (`BlocoRelatorio`)

Todo campo abaixo está sempre presente na saída. `additionalProperties: false` em todos os
objetos do contrato.

| Campo | Tipo | Obrigatório no schema | Observação |
| --- | --- | --- | --- |
| `versao` | integer (`1`) | não (default `1`) | Versão do contrato; sempre sai `1`. |
| `tipo` | string (enum) | sim | Um dos tipos do schema; igual a `dados.tipo`. |
| `titulo` | string | sim | Pronto para exibir. |
| `periodo` | `{inicio: date, fim: date}` \| `null` | sim | Inclusive. Sempre presente (nunca omitido). `null` só nos relatórios da grade semanal, sem datas: o plano semanal e os pacientes por plano (`tabela_generica` com `relatorio: "plano_semanal"` ou `"pacientes_por_plano"`); todos os outros blocos trazem `{inicio, fim}`. |
| `meta` | number \| null | sim | Ver regras gerais. |
| `parcial` | boolean | sim | `true` exatamente quando `dias_nao_lidos` não é vazia. |
| `dias_nao_lidos` | list[date] | sim | Ordenada, sem repetição. |
| `avisos` | list[string] | sim | Ressalvas para exibir. |
| `resumo` | list[`ItemResumo`] | sim | Destaques do relatório. |
| `dados` | objeto (por `tipo`) | sim | Ver cada bloco abaixo. |
| `tabelas` | list[`TabelaExportacao`] | sim | Uma aba por tabela na exportação. |

`ItemResumo`: `rotulo` (string), `valor` (string \| integer \| number \| boolean \| null),
`formato` (`texto` \| `inteiro` \| `decimal` \| `percentual` \| `data`), `exibicao` (string já
formatada). Todos obrigatórios.

`TabelaExportacao`: `nome` (string), `colunas` (list[`ColunaTabela`]), `linhas` (list de objetos
`chave -> string | integer | number | boolean | null`). Cada linha tem exatamente as chaves das
colunas. `ColunaTabela`: `chave`, `rotulo`, `formato` (mesmo enum acima). Todos obrigatórios.

`ProfissionalRelatorio`: `id` (string, hash opaco), `nome` (string), `especialidade`
(string \| null, rótulo). Todos obrigatórios (`especialidade` pode ser `null`).

Valores fixos em `dados`: `dia_semana` é `"segunda-feira"` … `"sábado"`; `periodo` é `"manha"`
ou `"tarde"` (nas tabelas, `"Manhã"`/`"Tarde"`).

## `disponibilidade`

Relatórios `vagas_livres` e `janelas_continuas`. `meta`: sempre `null`.

`dados` (`DadosDisponibilidade`):

| Campo | Tipo | Obrigatório | Observação |
| --- | --- | --- | --- |
| `tipo` | `"disponibilidade"` | não (default) | Sempre presente. |
| `minutos_por_janela` | integer `>= 30` | sim | 30 = todas as vagas; 60/90 = só onde cabe um atendimento desse tamanho. |
| `faixas` | list[`FaixaVaga`] | sim | Vagas seguidas do mesmo profissional, sala e posto. |
| `por_dia` | list[`VagasDiaPeriodo`] | sim | Uma linha por dia e período. |

`FaixaVaga` (todos obrigatórios): `data` (date), `dia_semana` (string), `periodo`
(`manha` \| `tarde`), `profissional` (`ProfissionalRelatorio`), `sala_id` (string), `sala_nome`
(string), `posto` (integer `>= 1`), `inicio` e `fim` (`HH:MM`), `slots` (integer `>= 1`, slots
de 30 min livres), `cabem` (integer `>= 0`, atendimentos de `minutos_por_janela` que cabem).

`VagasDiaPeriodo` (todos obrigatórios): `data`, `dia_semana`, `periodo`, `vagas_livres`
(integer), `horas_livres` (number), `janelas` (integer \| null; `null` quando
`minutos_por_janela` é 30).

Tabelas:

- **Vagas**: `data`, `dia_semana`, `periodo`, `profissional`, `especialidade`, `sala`, `posto`,
  `inicio`, `fim`, `slots`, `cabem`. Linhas ordenadas por data → especialidade → início →
  profissional → sala (ordem natural: Sala 2 antes de Sala 10) → posto; textos sem diferenciar
  acento nem maiúsculas; valores ausentes no fim. (`dados.faixas` mantém a ordem do motor.)
- **Resumo por dia**: `data`, `dia_semana`, `periodo`, `vagas_livres`, `horas_livres`
  (`decimal`), `janelas`.

## `convenio`

Relatório `convenios`. `meta`: sempre `null`.

`dados` (`DadosConvenio`): `tipo` (`"convenio"`, default), `por_convenio`
(list[`ItemConvenio`], obrigatório), `aguardando` (list[`AguardandoProfissionalDia`],
obrigatório).

`ItemConvenio` (todos obrigatórios): `convenio` (string, identificador: `unimed`,
`particular`, …, `nao_informado`; grupos mistos unidos por `+`), `rotulo` (string, para
exibir), `sessoes`, `pacientes_distintos`, `slots_ocupados`, `aguardando_autorizacao`
(integers).

`AguardandoProfissionalDia` (todos obrigatórios): `data`, `dia_semana`, `profissional`
(`ProfissionalRelatorio`), `sessoes` (integer).

Tabelas: **Por convênio** (`rotulo`, `sessoes`, `pacientes_distintos`, `slots_ocupados`,
`aguardando_autorizacao`) e **Aguardando autorização** (`data`, `dia_semana`, `profissional`,
`sessoes`).

**Pendência conhecida (não muda neste momento):** este relatório lê a cor de outro jeito que os
pacientes por plano (`pacientes_por_plano`), e os números dos dois podem divergir:

- a cor é a da **célula inteira**, por hex exato (`CORES_CONVENIO`), sem olhar a cor de cada
  trecho do texto. Na planilha de out/2026, 3 células têm o nome noutra cor (uma delas é uma
  sessão em grupo com dois planos);
- o **laranja** (Care Plus) não está no mapa e sai como `nao_informado`;
- cada paciente tem **um convênio por dia** (o primeiro conhecido), mesmo que apareça em cores
  diferentes;
- o **preto** é "aguardando autorização" aqui; em `pacientes_por_plano`, nome preto fica fora.

## `agenda_paciente`

Relatório `agenda_paciente`. **Único bloco com dado de paciente.** `meta`: sempre `null`.

`dados` (`DadosAgendaPaciente`, todos obrigatórios exceto `tipo`): `tipo`
(`"agenda_paciente"`), `paciente` (`PacienteRelatorio`), `dias`
(list[`DiaAgendaPacienteRelatorio`]), `total_sessoes` (integer), `minutos_em_intervalos`
(integer; espera entre terapias, sem a pausa do almoço).

- `PacienteRelatorio`: `nome` (string), `convenio` (string \| null, rótulo). Ambos obrigatórios.
- `DiaAgendaPacienteRelatorio`: `data`, `dia_semana`, `sessoes`
  (list[`SessaoAgendaPaciente`]), `intervalos` (list[`IntervaloAgendaPaciente`]).
- `SessaoAgendaPaciente`: `inicio`, `fim` (`HH:MM`), `profissional_nome`, `especialidade`
  (rótulo), `sala_nome` (strings), `aguardando_autorizacao` (boolean).
- `IntervaloAgendaPaciente`: `inicio`, `fim` (`HH:MM`), `minutos`, `minutos_de_pausa`
  (integers; a parte do intervalo que é o almoço).

Tabelas: **Sessões** (`data`, `dia_semana`, `inicio`, `fim`, `profissional`, `especialidade`,
`sala`, `aguardando_autorizacao` — booleano) e **Intervalos** (`data`, `dia_semana`, `inicio`,
`fim`, `minutos`, `minutos_de_pausa`).

## `qualidade_agenda`

Relatório `qualidade_agenda`. `meta`: sempre `null`.

`dados` (`DadosQualidadeAgenda`): `tipo` (`"qualidade_agenda"`, default), `por_profissional`
(list[`QualidadeProfissional`]), `por_dia` (list[`QualidadeProfissionalDiaRelatorio`]).

`QualidadeProfissional` (todos obrigatórios): `profissional` (`ProfissionalRelatorio`),
`slots_escalados`, `slots_ocupados`, `vagas_livres`, `buracos`, `slots_em_buracos`,
`slots_isolados`, `faixas_livres`, `maior_faixa_livre` (integers, em slots de 30 min).
`QualidadeProfissionalDiaRelatorio`: os mesmos campos mais `data` e `dia_semana`.

Tabelas: **Por profissional** (`profissional`, `especialidade`, e as métricas acima) e
**Por profissional e dia** (`data`, `dia_semana` e as mesmas colunas).

## `tabela_generica`

Relatórios `ranking_ocupacao`, `comparativo_dias`, `salas_ociosas`,
`capacidade_especialidade` e `personalizado`. `meta`: `0.8` quando pede `ocupacao` ou
`slots_para_meta`; senão `null`.

`dados` (`DadosTabelaGenerica`, todos obrigatórios exceto `tipo`):

| Campo | Tipo | Observação |
| --- | --- | --- |
| `tipo` | `"tabela_generica"` | Default; sempre presente. |
| `relatorio` | string | Relatório que gerou a tabela, ou `personalizado`. |
| `dimensoes` | list[string] | `dia`, `profissional`, `especialidade`, `sala`, `posto`, `periodo`, `convenio`. |
| `metricas` | list[string] | Ex.: `ocupacao`, `vagas_livres`, `pacientes_distintos` (lista completa em `GET /relatorios/catalogo`). |
| `colunas` | list[`ColunaTabela`] | Dimensões (`dia` vira `data` + `dia_semana`), métricas, `abaixo_da_meta` quando há meta, e extras do relatório (ex.: `situacao` em `capacidade_especialidade`). |
| `linhas` | list[objeto] | Valores das dimensões são rótulos (especialidade, convênio, sala…). |
| `total` | objeto | Só as métricas (e `abaixo_da_meta` quando há meta), recalculadas sobre o recorte inteiro: não é a soma das linhas. |
| `linhas_omitidas` | integer `>= 0` | Linhas fora da tabela por causa do `limite`. |

Tabelas: **Resultado** (mesmas colunas e linhas de `dados`) e **Total** (colunas de métrica e
`abaixo_da_meta`, uma linha).

### Plano semanal (`relatorio: "plano_semanal"`)

Vem da tool `planejar_semana` (plano de um paciente: uma sessão por especialidade em cada
dia pedido). Usa o mesmo tipo `tabela_generica`, sem tipo novo de bloco. `relatorio` é o
identificador estável `"plano_semanal"`; `titulo` é legível (ex.: `"Plano semanal: quarta e
quinta, das 10:00 às 12:00 (sessões de 30 min)"`). `dimensoes` e `metricas` vêm vazias,
`total` é `{}`, `meta` é `null` e `linhas_omitidas` é `0`. Não traz paciente.
**`periodo` é `null`** (presente, nunca omitido): a recorrência é a grade semanal, sem datas,
e o bloco não traz data nenhuma; o dia vem só na coluna `dia`, como texto da grade
(`"quinta-feira"`), em todas as linhas ("Plano", "Não coube" e "Alternativa"). Exemplo em
[`exemplos-blocos/tabela_generica_plano_semanal.json`](exemplos-blocos/tabela_generica_plano_semanal.json).

| Coluna | Formato | Valores |
| --- | --- | --- |
| `dia` | `texto` | `"quarta-feira"` etc. (grade semanal, sem data) |
| `situacao` | `texto` | `"Plano"`, `"Não coube"` ou `"Alternativa"` |
| `especialidade` | `texto` | Rótulo (`"Terapia Ocupacional"`) |
| `horario` | `texto` | `"10:00 às 10:30"`; `"—"` em "Não coube" |
| `profissional` | `texto` | Nome; `"—"` em "Não coube" |
| `sala` | `texto` | Nome da sala; `"—"` em "Não coube" |
| `posto` | `inteiro` | A partir de 1; `null` em "Não coube" |

Ordem das linhas, por dia pedido: as sessões do plano (por horário) e, para cada
especialidade que não coube, a linha "Não coube" seguida das "Alternativa" fora da janela
que acontecem **nesse mesmo dia**. As alternativas só caem em dias pedidos: primeiro as do
mesmo dia (até 3) e, se faltar, as que sobram no outro dia pedido em que a especialidade
também não coube; essas aparecem no grupo do dia em que acontecem, sob o "Não coube" dela.
Cada alternativa aparece uma vez na tabela inteira, e o `dia` de toda linha é o do grupo.
Horários em dias não pedidos não entram na tabela (o texto da resposta oferece mostrá-los).

Com sessões seguidas (`planejar_semana` com `seguidas: true`; o `titulo` começa com
`"Plano semanal com sessões seguidas"`), as linhas "Plano" de cada dia são a maior
sequência de sessões uma logo após a outra, e quem ficou de fora tem só a linha "Não coube",
sem "Alternativa". Colunas e valores de `situacao` são os mesmos.

`resumo`: um item por dia (`"3 de 4"`, formato `texto`) e `"Não couberam"` (`inteiro`). Tabela de exportação: **Plano** (mesmas colunas e linhas). `avisos` sempre traz
que a recorrência usa a grade semanal e não vê feriados nem exceções.

### Pacientes por plano (`relatorio: "pacientes_por_plano"`)

Vem da tool `pacientes_por_plano` (só pelo chat; **não** existe em `POST /relatorios/gerar`).
Relatório gerencial: quantos pacientes e atendimentos cada plano de saúde tem na grade semanal
vigente, com a lista de nomes. Usa o mesmo tipo `tabela_generica`, sem tipo novo de bloco.

> **Atenção: este bloco contém nomes de pacientes** (coluna `paciente`). Os nomes nunca vão
> para o modelo da OpenAI nem para log: só para o bloco. O time do chatbot precisa revisar como
> o bloco sai na tela, no Excel e no PDF antes de liberar a exportação dele.

`titulo`: `"Pacientes por plano de saúde (grade semanal vigente)"` (sem nomes). **`periodo` é
`null`** (a fonte é a grade semanal, sem datas). `meta` é `null`, `dimensoes` e `metricas` vêm
vazias, `total` é `{}` e `linhas_omitidas` é `0`. Exemplo (nomes fictícios) em
[`exemplos-blocos/tabela_generica_pacientes_por_plano.json`](exemplos-blocos/tabela_generica_pacientes_por_plano.json).

| Coluna | Formato | Valores |
| --- | --- | --- |
| `plano` | `texto` | `"SulAmérica"`, `"Klini"`, `"Particulares"`, `"Care Plus"` ou `"Unimed"` |
| `paciente` | `texto` | Nome do paciente como na planilha (só o trecho colorido) |
| `atendimentos_semana` | `inteiro` | Sessões da semana com o nome na cor desse plano |

Uma linha por paciente e plano, agrupadas na ordem dos planos acima e em ordem alfabética dentro
de cada plano. **Não há linha de total no meio da lista**: os totais ficam no `resumo` (um item
por plano, `valor` = pacientes, `exibicao` = `"2 pacientes, 4 atendimentos"`, e o item
`"Pacientes com plano"`, com os pacientes distintos) e na tabela de exportação **Resumo por
plano** (`plano`, `pacientes`, `atendimentos_semana`, uma linha por plano, inclusive os sem
paciente). A outra tabela de exportação é **Pacientes** (mesmas colunas e linhas de `dados`).

Regras:

- O plano vem da **cor do texto** do nome (não do fundo), por faixa de matiz
  (`PLANO_POR_COR` em `app/domain/constants.py`): azul SulAmérica, vermelho Klini, lilás
  Particulares, laranja Care Plus, verde Unimed. Uma célula com dois nomes de cores diferentes
  conta cada nome no seu plano.
- **Preto** (texto padrão e tons muito escuros) fica **fora** da lista e das contagens. Não há
  categoria "sem plano".
- Nome colorido com observação preta (`Nome (falta)`): vale o trecho do nome; a observação sai.
- Cor que não é preta nem de plano: ignorada, com o aviso `"N nomes com cor não reconhecida
  foram ignorados."` só quando N > 0 (sem nomes).
- Paciente com cores de mais de um plano na semana: aparece em cada plano, com o aviso
  `"N pacientes aparecem com cores de mais de um plano…"` (só a contagem).
- Paciente = nome normalizado (sem acento, caixa nem pontuação): homônimos se fundem e grafias
  diferentes viram dois. Atendimento = sessão (horários seguidos contam uma vez).

`avisos` sempre traz: que cobre só a grade semanal vigente (não todos os pacientes), a
identificação só pelo nome, que nome preto fica fora, `"Atendimentos contados apenas com cor de
plano…"` e que faturamento, guias, valores e cadastro ficam fora do RealocAI.

## Histórico de mudanças (versão 1)

| Data | Mudança | Compatibilidade |
| --- | --- | --- |
| 2026-10-28 | Novo valor de `relatorio` em `tabela_generica`: `"pacientes_por_plano"` (tool `pacientes_por_plano`, só pelo chat), com `periodo` `null`. **O bloco contém nomes de pacientes.** Nenhum tipo de bloco, campo ou coluna existente mudou. | Compatível: `versao` continua `1`, schema igual. Quem trata `tabela_generica` pelo `relatorio` precisa conhecer o valor novo; a exportação em Excel/PDF desse bloco precisa de revisão antes de sair. |
| 2026-10-27 | Plano semanal: alternativas só nos dias pedidos, sem repetição, sempre no grupo do dia em que acontecem (antes podiam vir de dias não pedidos, repetidas em cada grupo, com `dia` diferente do grupo); e o modo `seguidas` (mesmo formato). Nenhuma coluna nem valor de `situacao` mudou. | Compatível: `versao` continua `1`, formato igual. Só muda quais linhas vêm e a ordem dentro do grupo de cada dia. |
| 2026-10-27 | `periodo` passou a aceitar `null` (o campo continua obrigatório e sempre presente). Só o plano semanal (`relatorio: "plano_semanal"`) envia `null`; nenhum outro tipo de bloco mudou. O schema (`bloco_relatorio.schema.json`) aceita `null` em qualquer tipo, por ser um envelope único. | Compatível: `versao` continua `1`. Quem guarda cópia do schema precisa atualizá-la, e quem exibe ou exporta o plano deve tratar `periodo` nulo (sem linha de período). |
