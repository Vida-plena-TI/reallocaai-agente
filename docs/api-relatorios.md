# API dos relatórios gerais — contrato para o backend do chatbot

Estes endpoints expõem, por HTTP, o mesmo motor de relatórios que o agente usa nas tools
`gerar_relatorio` e `consultar_catalogo`. Para os mesmos parâmetros, a resposta traz **o mesmo
texto e o mesmo `BlocoRelatorio`** que a tool devolve ao agente. O formato de cada bloco está em
[`exemplos-blocos/bloco_relatorio.schema.json`](exemplos-blocos/bloco_relatorio.schema.json), e há
um exemplo por tipo de bloco em [`exemplos-blocos/`](exemplos-blocos/).
As regras do contrato dos blocos novos (campos, tipos e convenções) estão em
[`contrato-blocos-novos.md`](contrato-blocos-novos.md).

A exportação em Excel continua no backend do chatbot: o RealocAI devolve só o bloco estruturado
(`bloco.tabela` traz as colunas e linhas prontas para exportar).

## Regras comuns

- **Autenticação:** header `X-API-Key` com o valor de `INTERNAL_API_KEY`, como nas demais rotas.
  Sem a chave, ou com a chave errada: `401`.
- **Período:** `inicio` e `fim` (AAAA-MM-DD), inclusive. Sem os dois: a semana de hoje (segunda a
  sábado). Só `inicio`: um dia. No máximo **62 dias**.
- **Domingo** não tem agenda e nunca é lido. Um período só com domingo devolve `200` com
  `bloco: null` e um texto explicando.
- **Dia que não pôde ser lido:** o resultado sai assim mesmo, marcado como parcial, com o aviso
  `"Não foi possível ler: DD/MM; os números são parciais."` em `bloco.avisos`. Se nenhum dia do
  período puder ser lido: `502`.
- **Grade semanal:** a planilha é uma grade semanal (uma aba por dia da semana, sem datas), então
  todo bloco traz essa ressalva em `avisos`, e um período de mais de uma semana repete a mesma
  agenda.
- **Linhas:** `limite` aceita de 1 a **200**.
- **Erros `422`:**
  - erro de schema (campo desconhecido, tipo errado, valor fora do limite): `detail` é uma lista de
    `{loc, msg, type}`. **O valor recebido nunca volta na resposta** (vale para todas as rotas da
    API, inclusive `/agenda/chat`);
  - parâmetro que não faz sentido (profissional ambígua, sala inexistente, período invertido,
    combinação inválida): `detail` é uma frase explicando o que corrigir.
- **Logs:** nada do corpo vai para log. Só o tipo do relatório, o número de dias e o tempo, e,
  numa falha, o tipo do erro.

## `GET /relatorios/catalogo`

O mapa da clínica numa semana: profissionais por especialidade (com dias e salas), salas e
postos, convênios por cor, dias com agenda, e o que significa cada relatório e métrica. Nunca traz
pacientes.

Query string:

| Parâmetro | Tipo | Obrigatório | Descrição |
| --- | --- | --- | --- |
| `data` | `AAAA-MM-DD` | não | Qualquer dia da semana desejada. Padrão: hoje. |

Resposta `200`:

```json
{
  "semana_inicio": "2026-09-28",
  "semana_fim": "2026-10-03",
  "texto": "Catálogo da clínica — semana de 28/09/2026 a 03/10/2026\n...",
  "relatorios": [{"nome": "vagas_livres", "descricao": "vagas de 30 min em faixas, ..."}],
  "metricas": [{"nome": "ocupacao", "descricao": "slots ocupados ÷ slots escalados ..."}],
  "dimensoes": ["dia", "profissional", "especialidade", "sala", "posto", "periodo", "convenio"],
  "parcial": false
}
```

- `texto`: o mesmo texto da tool `consultar_catalogo`.
- `parcial`: indica se algum dia da semana não pôde ser lido.

## `POST /relatorios/gerar`

Corpo (JSON). Só `relatorio` é obrigatório. Campo desconhecido devolve `422`.

| Campo | Tipo | Descrição |
| --- | --- | --- |
| `relatorio` | texto | Um relatório pronto (tabela abaixo) ou `personalizado`. |
| `inicio`, `fim` | `AAAA-MM-DD` | Período (ver regras comuns). |
| `profissionais` | lista de texto | Filtra por profissional (nome ou parte do nome; ambíguo → `422`). |
| `especialidades` | lista de texto | Filtra por especialidade, como escrita (`"psico"`, `"fono"`, `"TO"`). |
| `salas` | lista de texto | Filtra por sala (`"Sala 3"` ou `"3"`). |
| `convenios` | lista de texto | `unimed`, `klini`, `sulamerica`, `particular`, `nao_informado`. |
| `dias_semana` | lista | `segunda` … `sabado`, dentro do período. |
| `periodo_do_dia` | `manha` \| `tarde` | Só manhã (antes das 12h) ou só tarde (a partir das 13h). |
| `hora_inicio` | `HH:MM` | Janela de horário: só slots que começam neste horário ou depois. |
| `hora_fim` | `HH:MM` | Janela de horário: só slots que terminam até este horário (fechada no início, aberta no fim: com 10:00 a 12:00, 11:30-12:00 entra e 09:30-10:00 não). Precisa ser depois de `hora_inicio`. |
| `duracao_minutos` | inteiro | `janelas_continuas`: duração do atendimento (60 = 1 h, 90 = 1 h 30). |
| `dimensoes` | lista | `personalizado`: por onde agrupar; `ranking_ocupacao`: uma dimensão. |
| `metricas` | lista | `personalizado` (obrigatório): o que calcular. |
| `ordenar_por` | texto | `personalizado`: uma das métricas ou dimensões pedidas. |
| `ordem` | `desc` \| `asc` | Ordem de `ordenar_por` e do ranking. Padrão: `desc`. |
| `limite` | 1 a 200 | Máximo de linhas. |
| `paciente` | texto (até 120) | **Só em `agenda_paciente`**: o nome exato do paciente. |

Relatórios prontos:

| `relatorio` | Bloco (`bloco.tipo`) | O que responde |
| --- | --- | --- |
| `vagas_livres` | `disponibilidade` | Vagas de 30 min em faixas, com resumo por dia e manhã/tarde. |
| `janelas_continuas` | `disponibilidade` | Onde cabe um atendimento de `duracao_minutos` seguidos (padrão 60). |
| `ranking_ocupacao` | `tabela_generica` | Ocupação por profissional (ou outra dimensão), da maior para a menor. |
| `comparativo_dias` | `tabela_generica` | Um dia contra o outro: ocupação, vagas, pacientes e sessões. |
| `convenios` | `convenio` | Sessões e pacientes por convênio e o que aguarda autorização (só contagens). |
| `qualidade_agenda` | `qualidade_agenda` | Buracos entre atendimentos e vagas isoladas por profissional. |
| `salas_ociosas` | `tabela_generica` | Salas da menos para a mais ocupada. |
| `capacidade_especialidade` | `tabela_generica` | Capacidade sobrando, equilibrada ou no limite por especialidade. |
| `agenda_paciente` | `agenda_paciente` | Horários, profissionais e esperas de UM paciente pedido pelo nome. |
| `personalizado` | `tabela_generica` | Qualquer cruzamento de `dimensoes` e `metricas`. |

Os parâmetros que não se aplicam a um relatório pronto (por exemplo, `metricas` em `convenios`)
são ignorados, e uma observação vai no `texto`.

`pacientes_por_plano` **não** é um valor aceito aqui (devolve `422`): esse relatório existe só
pelo chat. Ver [Só pelo chat](#só-pelo-chat-pacientes-por-plano).

Resposta `200`:

```json
{
  "texto": "Vagas livres de 28/09/2026 a 02/10/2026\n...",
  "bloco": {"versao": 1, "tipo": "disponibilidade", "titulo": "Vagas livres de 28/09/2026 a 02/10/2026", "...": "..."}
}
```

`bloco` é `null` quando não há agenda no período (só domingo) ou quando o paciente de
`agenda_paciente` não está na agenda.

### Agenda de um paciente

- O nome vai **somente no corpo do POST** (`paciente`). Nunca na URL nem na query string, que
  vão para o access log. Não existe rota `GET` para isso, e `?paciente=` na URL é ignorado.
- A busca é pelo **nome exato** como está na agenda (sem diferenciar acento e caixa), **sem
  sugestão de nomes parecidos**. Se o nome não estiver na agenda do período, a resposta é `200`
  com `bloco: null` e um texto dizendo que a busca é pelo nome exato.
- O nome nunca vai para log, nem no `422` (o valor recebido não volta).
- `paciente` em qualquer outro relatório devolve `422`: os demais relatórios nunca trazem
  pacientes, só contagens.

### Exemplos

Vagas de segunda a sexta, só à tarde:

```json
{"relatorio": "vagas_livres", "inicio": "2026-09-28", "fim": "2026-10-02", "periodo_do_dia": "tarde"}
```

Onde cabe 1 h 30 de fonoaudiologia na semana:

```json
{"relatorio": "janelas_continuas", "duracao_minutos": 90, "especialidades": ["fono"]}
```

Top 5 do ranking de ocupação:

```json
{"relatorio": "ranking_ocupacao", "limite": 5}
```

Ocupação e vagas por dia e especialidade:

```json
{"relatorio": "personalizado", "dimensoes": ["dia", "especialidade"], "metricas": ["ocupacao", "vagas_livres"]}
```

Agenda de um paciente (nome fictício):

```json
{"relatorio": "agenda_paciente", "paciente": "Paciente Exemplo"}
```

### Erros

| Status | Quando |
| --- | --- |
| `401` | Sem `X-API-Key`, ou com a chave errada. |
| `405` | `GET /relatorios/gerar`. |
| `422` | Schema inválido (lista `{loc, msg, type}`, sem o valor) ou parâmetro sem sentido (frase). |
| `502` | Nenhum dia do período pôde ser lido. Sem detalhe do erro interno. |

## Só pelo chat: pacientes por plano

O relatório de pacientes por plano de saúde **não é exposto nesta API** (nem em
`POST /relatorios/gerar`, nem em rota própria). Ele sai só pela tool `pacientes_por_plano` do
agente (`POST /agenda/chat`), como um bloco `tabela_generica` com `relatorio:
"pacientes_por_plano"` e `periodo: null` (formato em
[`contrato-blocos-novos.md`](contrato-blocos-novos.md#pacientes-por-plano-relatorio-pacientes_por_plano)).

- **O bloco contém nomes de pacientes.** O texto que o modelo lê e o texto da resposta só têm
  contagens; os nomes vão só no bloco. A exibição e a exportação (Excel e PDF) desse bloco
  precisam de revisão do time do chatbot antes de serem liberadas.
- Escopo: relatório gerencial da **grade semanal vigente** (só quem tem sessão na grade desta
  semana, não todos os pacientes). O plano vem da cor do texto do nome; nome em preto fica fora.
  O paciente é identificado só pelo nome (homônimos se fundem, grafias diferentes viram dois).
  Faturamento, guias, valores e cadastro ficam fora do RealocAI.
- **Diferença em relação a `convenios`** (pendência registrada, sem mudança por ora): o
  relatório `convenios` usa a cor da célula inteira por hex exato, mostra o laranja (Care Plus)
  como `nao_informado`, guarda um convênio por paciente por dia e trata o preto como "aguardando
  autorização". Os números dos dois relatórios podem divergir; detalhes no
  [contrato](contrato-blocos-novos.md#convenio).
