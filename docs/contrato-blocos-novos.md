# Contrato dos blocos novos (versão 1)

Para o time do front/backend do chatbot. Descreve os blocos que saem de `gerar_relatorio` (tool
do agente) e de `POST /relatorios/gerar`: `disponibilidade`, `convenio`, `agenda_paciente`,
`qualidade_agenda` e `tabela_generica`.

A fonte da verdade é o schema
[`exemplos-blocos/bloco_relatorio.schema.json`](exemplos-blocos/bloco_relatorio.schema.json),
gerado do modelo `BlocoRelatorio` (`app/ai/relatorios.py`). Há um exemplo real de cada bloco em
[`exemplos-blocos/`](exemplos-blocos/). Um teste falha se o schema ou os exemplos ficarem
desatualizados (regenerar: `uv run python scripts/gerar_exemplos_blocos.py`).

## Regras gerais

| Regra | Detalhe |
| --- | --- |
| **Percentual é fração** | `0.8` = 80%. Vale para `meta`, para a métrica `ocupacao` e para todo valor com `formato: "percentual"`. Não é limitado a 1: acima de 1 indica inconsistência na planilha. O texto pronto para exibir está em `resumo[].exibicao` (ex.: `"23,6%"`). |
| **Datas ISO** | `AAAA-MM-DD` (`periodo.inicio`, `periodo.fim`, `dias_nao_lidos[]`, todo campo `data` e toda coluna com `formato: "data"`). Horários são `HH:MM`. |
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
| `periodo` | `{inicio: date, fim: date}` | sim | Inclusive. |
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
especialidade que não coube, a linha "Não coube" seguida de até 3 "Alternativa" fora da
janela (mesmo dia primeiro, depois outros dias; o `dia` da alternativa é o dia em que ela
acontece). `resumo`: um item por dia (`"3 de 4"`, formato `texto`) e `"Não couberam"`
(`inteiro`). Tabela de exportação: **Plano** (mesmas colunas e linhas). `avisos` sempre traz
que a recorrência usa a grade semanal e não vê feriados nem exceções.
