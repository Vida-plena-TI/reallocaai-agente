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

`pacientes_por_plano`, `pacientes_por_profissional_nomes` e `panorama_enxugamento` **não** são
valores aceitos aqui (devolvem `422`): esses relatórios existem só pelo chat. Ver
[Só pelo chat: pacientes por plano](#só-pelo-chat-pacientes-por-plano),
[Só pelo chat: nomes de pacientes por profissional](#só-pelo-chat-nomes-de-pacientes-por-profissional)
e [Só pelo chat: panorama de enxugamento](#só-pelo-chat-panorama-de-enxugamento).

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
- O bloco `agenda_paciente` traz `contem_dados_pacientes: true` (campo opcional da versão 1; os
  outros relatórios desta API não o trazem).
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

- **O bloco contém nomes de pacientes** e traz `contem_dados_pacientes: true`. O texto que o
  modelo lê e o texto da resposta só têm contagens; os nomes vão só no bloco. A exibição e a exportação (Excel e PDF) desse bloco
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

## Só pelo chat: nomes de pacientes por profissional

A lista de NOMES dos pacientes de cada profissional **não é exposta nesta API** (nem em
`POST /relatorios/gerar`, que devolve `422` para `relatorio: "pacientes_por_profissional_nomes"`,
nem em rota própria). Ela sai só pela tool `listar_pacientes_por_profissional` do agente
(`POST /agenda/chat`), como um bloco `tabela_generica` com `relatorio:
"pacientes_por_profissional_nomes"` e `periodo: null` (formato em
[`contrato-blocos-novos.md`](contrato-blocos-novos.md#pacientes-por-profissional-com-os-nomes-relatorio-pacientes_por_profissional_nomes)).

- **O bloco contém nomes de pacientes.** O texto que o modelo lê e o texto da resposta só têm
  contagens; os nomes vão só no bloco. Deve entrar na restrição ao grupo de coordenação, como
  `pacientes_por_plano`, e a exibição e a exportação (Excel e PDF) desse bloco precisam de
  revisão do time do chatbot antes de serem liberadas.
- Escopo: relatório gerencial da **grade semanal vigente**, sem limite de linhas (todos os
  profissionais e todos os pacientes com sessão na grade; filtros opcionais por profissional,
  especialidade e dias da semana). O paciente é identificado só pelo nome (homônimos se fundem,
  grafias diferentes viram dois) e quem é atendido por mais de uma profissional aparece em cada
  uma.
- Exibição em seções: `dados` traz `agrupar_por: ["especialidade", "profissional"]` e
  `unidade_grupo: {"singular": "paciente", "plural": "pacientes"}`, com as linhas já contíguas por
  grupo; o front agrupa as linhas consecutivas e monta o título de cada grupo no padrão
  `valor (N unidade)`: especialidade e profissional seguidas de `(1 paciente)` ou
  `(3 pacientes)`. As tabelas de exportação continuam planas.
- O bloco traz `contem_dados_pacientes: true`, como `pacientes_por_plano` e `agenda_paciente`
  (campos opcionais da versão 1, ver
  [`contrato-blocos-novos.md`](contrato-blocos-novos.md#campos-opcionais-versão-1)).
- O relatório de contagem (`pacientes_por_profissional`, tool
  `consultar_pacientes_por_profissional`) continua igual e só traz quantidades.

## Só pelo chat: panorama de enxugamento

O panorama de enxugamento **não é exposto nesta API** (nem em `POST /relatorios/gerar`, que
devolve `422` para `relatorio: "panorama_enxugamento"`, nem em rota própria). Ele sai só pela
tool `planejar_enxugamento` do agente (`POST /agenda/chat`), como **dois blocos**
`tabela_generica` na mesma resposta, ambos com `relatorio: "panorama_enxugamento"` e
`periodo: null` (formato em
[`contrato-blocos-novos.md`](contrato-blocos-novos.md#panorama-de-enxugamento-relatorio-panorama_enxugamento)):

1. **Quadro comparativo** (`"Comparativo de cenários"`): uma linha por cenário, só números, sem
   nomes; `recomendado` é booleano JSON. Aba de exportação **Cenários**.
2. **Passos** (`"Passos do cenário recomendado"`): os passos do recomendado, agrupados por
   cenário (`agrupar_por: ["cenario"]`, `unidade_grupo: {"singular": "passo", "plural":
   "passos"}`). Quando o usuário pede outro cenário ("quero ver o Cenário 3"), o agente chama a
   tool de novo e esse bloco mostra só o que difere do Cenário 1, com título e aviso explícitos.
   Aba de exportação **Passos**.

- **Os dois blocos trazem `contem_dados_pacientes: true`** e a resposta, `resposta_sensivel:
  true`. O bloco de passos contém nomes de pacientes e é sensível de RH (nomes de profissionais
  nos turnos liberados e nos passos); o quadro só tem números, mas tem o mesmo `relatorio`, então
  a restrição ao grupo de coordenação (pela igualdade em `dados.relatorio`) cobre os dois.
- O texto da resposta tem de 3 a 4 frases, sem nome de paciente nem de profissional e sem os
  códigos internos dos cenários: a recomendação com números, o que precisa ser confirmado e a
  pergunta final "Quer ver os passos do Cenário N?". Os códigos não aparecem em bloco algum.
- Escopo: sugestões sobre a **grade semanal vigente**. Um turno (manhã ou tarde) só aparece como
  liberado se todos os pacientes dele forem para turnos que já acontecem; nenhum cenário abre
  turno vazio, ninguém muda de terapia nem de duração. O RealocAI não conhece valores: o
  panorama conta só turnos liberados, nunca dinheiro.
- O chatbot guarda a lista de blocos e desenha um cartão por bloco, na ordem recebida. As tabelas
  de exportação são planas, com nomes de aba únicos entre os dois blocos.

Exemplo de resposta completa (nomes fictícios; os blocos são os exemplos versionados
[`tabela_generica_panorama_enxugamento_comparativo.json`](exemplos-blocos/tabela_generica_panorama_enxugamento_comparativo.json)
e [`tabela_generica_panorama_enxugamento.json`](exemplos-blocos/tabela_generica_panorama_enxugamento.json)):

<!-- exemplo-resposta-panorama -->
```json
{
  "conversa_id": "3f1c…",
  "resposta": "Sugiro o Cenário 1, que equilibra turnos liberados e mudanças: libera 3 turnos por semana sem abrir turno novo e muda 4 pacientes de lugar (3 com troca de profissional e 0 com troca de dia). Cada mudança precisa ser confirmada com as famílias e feita manualmente na planilha. O comparativo de 3 cenários e os passos estão no relatório. Quer ver os passos do Cenário 2?",
  "blocos": [
    {
      "versao": 1,
      "tipo": "tabela_generica",
      "titulo": "Comparativo de cenários",
      "periodo": null,
      "meta": null,
      "parcial": false,
      "dias_nao_lidos": [],
      "avisos": [
        "Sugestão: considera apenas os atendimentos presentes na semana lida (terapias quinzenais ou fora desta semana não aparecem).",
        "Não considera a disponibilidade dos pacientes.",
        "Vagas livres na grade podem estar reservadas (reuniões, supervisão); confira antes de mudar.",
        "Cada mudança precisa ser confirmada com a família e feita manualmente na planilha.",
        "Cenário 1: 2 turnos de terapia ficariam sem nenhum profissional atendendo, num dia em que hoje tem.",
        "Cenário 2: 2 turnos de terapia ficariam sem nenhum profissional atendendo, num dia em que hoje tem.",
        "Cenário 3: 1 turno de terapia ficaria sem nenhum profissional atendendo, num dia em que hoje tem."
      ],
      "resumo": [
        {"rotulo": "Cenários", "valor": 3, "formato": "inteiro", "exibicao": "3"},
        {"rotulo": "Maior número de turnos liberados", "valor": 3, "formato": "inteiro", "exibicao": "3"},
        {"rotulo": "Turnos com presença paga na semana", "valor": 8, "formato": "inteiro", "exibicao": "8"},
        {"rotulo": "Turnos presos por regra", "valor": 0, "formato": "inteiro", "exibicao": "0"}
      ],
      "dados": {
        "tipo": "tabela_generica",
        "relatorio": "panorama_enxugamento",
        "dimensoes": [],
        "metricas": [],
        "colunas": [
          {"chave": "cenario", "rotulo": "Cenário", "formato": "texto"},
          {"chave": "turnos_liberados", "rotulo": "Turnos liberados", "formato": "inteiro"},
          {"chave": "passos", "rotulo": "Passos", "formato": "inteiro"},
          {"chave": "profissionais_afetadas", "rotulo": "Profissionais afetadas", "formato": "inteiro"},
          {"chave": "pacientes_movidos", "rotulo": "Pacientes movidos", "formato": "inteiro"},
          {"chave": "trocam_de_profissional", "rotulo": "Trocam de profissional", "formato": "inteiro"},
          {"chave": "trocam_de_dia", "rotulo": "Trocam de dia", "formato": "inteiro"},
          {"chave": "passos_iguais_ao_recomendado", "rotulo": "Passos iguais ao recomendado", "formato": "inteiro"},
          {"chave": "recomendado", "rotulo": "Recomendado", "formato": "texto"}
        ],
        "linhas": [
          {"cenario": "Cenário 1 · equilibrado", "turnos_liberados": 3, "passos": 4, "profissionais_afetadas": 2, "pacientes_movidos": 4, "trocam_de_profissional": 3, "trocam_de_dia": 0, "passos_iguais_ao_recomendado": 0, "recomendado": true},
          {"cenario": "Cenário 2 · mais turnos", "turnos_liberados": 3, "passos": 4, "profissionais_afetadas": 2, "pacientes_movidos": 4, "trocam_de_profissional": 3, "trocam_de_dia": 0, "passos_iguais_ao_recomendado": 3, "recomendado": false},
          {"cenario": "Cenário 3 · menos mudança", "turnos_liberados": 2, "passos": 3, "profissionais_afetadas": 2, "pacientes_movidos": 3, "trocam_de_profissional": 2, "trocam_de_dia": 0, "passos_iguais_ao_recomendado": 3, "recomendado": false}
        ],
        "total": {},
        "linhas_omitidas": 0
      },
      "tabelas": [
        {
          "nome": "Cenários",
          "colunas": [
            {"chave": "cenario", "rotulo": "Cenário", "formato": "texto"},
            {"chave": "turnos_liberados", "rotulo": "Turnos liberados", "formato": "inteiro"},
            {"chave": "passos", "rotulo": "Passos", "formato": "inteiro"},
            {"chave": "profissionais_afetadas", "rotulo": "Profissionais afetadas", "formato": "inteiro"},
            {"chave": "pacientes_movidos", "rotulo": "Pacientes movidos", "formato": "inteiro"},
            {"chave": "trocam_de_profissional", "rotulo": "Trocam de profissional", "formato": "inteiro"},
            {"chave": "trocam_de_dia", "rotulo": "Trocam de dia", "formato": "inteiro"},
            {"chave": "passos_iguais_ao_recomendado", "rotulo": "Passos iguais ao recomendado", "formato": "inteiro"},
            {"chave": "recomendado", "rotulo": "Recomendado", "formato": "texto"}
          ],
          "linhas": [
            {"cenario": "Cenário 1 · equilibrado", "turnos_liberados": 3, "passos": 4, "profissionais_afetadas": 2, "pacientes_movidos": 4, "trocam_de_profissional": 3, "trocam_de_dia": 0, "passos_iguais_ao_recomendado": 0, "recomendado": true},
            {"cenario": "Cenário 2 · mais turnos", "turnos_liberados": 3, "passos": 4, "profissionais_afetadas": 2, "pacientes_movidos": 4, "trocam_de_profissional": 3, "trocam_de_dia": 0, "passos_iguais_ao_recomendado": 3, "recomendado": false},
            {"cenario": "Cenário 3 · menos mudança", "turnos_liberados": 2, "passos": 3, "profissionais_afetadas": 2, "pacientes_movidos": 3, "trocam_de_profissional": 2, "trocam_de_dia": 0, "passos_iguais_ao_recomendado": 3, "recomendado": false}
          ]
        }
      ],
      "contem_dados_pacientes": true
    },
    {
      "versao": 1,
      "tipo": "tabela_generica",
      "titulo": "Passos do cenário recomendado",
      "periodo": null,
      "meta": null,
      "parcial": false,
      "dias_nao_lidos": [],
      "avisos": [
        "Cada mudança precisa ser confirmada com a família e feita manualmente na planilha."
      ],
      "resumo": [
        {"rotulo": "Turnos liberados", "valor": 3, "formato": "inteiro", "exibicao": "3"},
        {"rotulo": "Passos", "valor": 4, "formato": "inteiro", "exibicao": "4"},
        {"rotulo": "Pacientes movidos", "valor": 4, "formato": "inteiro", "exibicao": "4"}
      ],
      "dados": {
        "tipo": "tabela_generica",
        "relatorio": "panorama_enxugamento",
        "dimensoes": [],
        "metricas": [],
        "colunas": [
          {"chave": "cenario", "rotulo": "Cenário", "formato": "texto"},
          {"chave": "turno_liberado", "rotulo": "Turno liberado", "formato": "texto"},
          {"chave": "passo", "rotulo": "Passo", "formato": "inteiro"},
          {"chave": "paciente", "rotulo": "Quem", "formato": "texto"},
          {"chave": "terapia", "rotulo": "Terapia", "formato": "texto"},
          {"chave": "de", "rotulo": "De", "formato": "texto"},
          {"chave": "para", "rotulo": "Para", "formato": "texto"},
          {"chave": "tipo_de_movimento", "rotulo": "Tipo de movimento", "formato": "texto"},
          {"chave": "observacao", "rotulo": "Observação", "formato": "texto"}
        ],
        "linhas": [
          {"cenario": "Cenário 1 · equilibrado · libera 3 turnos", "turno_liberado": "Ter tarde · Ficticia Acacia · Terapia Alimentar", "passo": 1, "paciente": "Paciente Ficticio Figueira", "terapia": "Terapia Alimentar", "de": "Ter 13:00 às 14:00 · Ficticia Acacia · Sala 7", "para": "Ter 11:00 às 12:00 · Ficticia Acacia · Sala 7", "tipo_de_movimento": "Mesmo profissional · outro turno", "observacao": ""},
          {"cenario": "Cenário 1 · equilibrado · libera 3 turnos", "turno_liberado": "Seg manhã · Ficticia Acacia · Terapia Alimentar", "passo": 2, "paciente": "Paciente Ficticio Aroeira", "terapia": "Terapia Alimentar", "de": "Seg 08:00 às 09:00 · Ficticia Acacia · Sala 7", "para": "Seg 08:00 às 09:00 · Ficticia Bromelia · Sala 8", "tipo_de_movimento": "Outra profissional da mesma terapia · mesmo turno", "observacao": ""},
          {"cenario": "Cenário 1 · equilibrado · libera 3 turnos", "turno_liberado": "Seg manhã · Ficticia Acacia · Terapia Alimentar", "passo": 3, "paciente": "Paciente Ficticio Baoba", "terapia": "Terapia Alimentar", "de": "Seg 10:00 às 11:00 · Ficticia Acacia · Sala 7", "para": "Seg 10:00 às 11:00 · Ficticia Bromelia · Sala 8", "tipo_de_movimento": "Outra profissional da mesma terapia · mesmo turno", "observacao": ""},
          {"cenario": "Cenário 1 · equilibrado · libera 3 turnos", "turno_liberado": "Qua tarde · Ficticia Bromelia · Terapia Alimentar", "passo": 4, "paciente": "Paciente Ficticio Hortela", "terapia": "Terapia Alimentar", "de": "Qua 13:00 às 14:00 · Ficticia Bromelia · Sala 8", "para": "Qua 11:00 às 12:00 · Ficticia Acacia · Sala 7", "tipo_de_movimento": "Outra profissional da mesma terapia · outro turno", "observacao": ""}
        ],
        "total": {},
        "linhas_omitidas": 0,
        "agrupar_por": ["cenario"],
        "unidade_grupo": {"singular": "passo", "plural": "passos"}
      },
      "tabelas": [
        {
          "nome": "Passos",
          "colunas": [
            {"chave": "cenario", "rotulo": "Cenário", "formato": "texto"},
            {"chave": "turno_liberado", "rotulo": "Turno liberado", "formato": "texto"},
            {"chave": "passo", "rotulo": "Passo", "formato": "inteiro"},
            {"chave": "paciente", "rotulo": "Quem", "formato": "texto"},
            {"chave": "terapia", "rotulo": "Terapia", "formato": "texto"},
            {"chave": "de", "rotulo": "De", "formato": "texto"},
            {"chave": "para", "rotulo": "Para", "formato": "texto"},
            {"chave": "tipo_de_movimento", "rotulo": "Tipo de movimento", "formato": "texto"},
            {"chave": "observacao", "rotulo": "Observação", "formato": "texto"}
          ],
          "linhas": [
            {"cenario": "Cenário 1 · equilibrado · libera 3 turnos", "turno_liberado": "Ter tarde · Ficticia Acacia · Terapia Alimentar", "passo": 1, "paciente": "Paciente Ficticio Figueira", "terapia": "Terapia Alimentar", "de": "Ter 13:00 às 14:00 · Ficticia Acacia · Sala 7", "para": "Ter 11:00 às 12:00 · Ficticia Acacia · Sala 7", "tipo_de_movimento": "Mesmo profissional · outro turno", "observacao": ""},
            {"cenario": "Cenário 1 · equilibrado · libera 3 turnos", "turno_liberado": "Seg manhã · Ficticia Acacia · Terapia Alimentar", "passo": 2, "paciente": "Paciente Ficticio Aroeira", "terapia": "Terapia Alimentar", "de": "Seg 08:00 às 09:00 · Ficticia Acacia · Sala 7", "para": "Seg 08:00 às 09:00 · Ficticia Bromelia · Sala 8", "tipo_de_movimento": "Outra profissional da mesma terapia · mesmo turno", "observacao": ""},
            {"cenario": "Cenário 1 · equilibrado · libera 3 turnos", "turno_liberado": "Seg manhã · Ficticia Acacia · Terapia Alimentar", "passo": 3, "paciente": "Paciente Ficticio Baoba", "terapia": "Terapia Alimentar", "de": "Seg 10:00 às 11:00 · Ficticia Acacia · Sala 7", "para": "Seg 10:00 às 11:00 · Ficticia Bromelia · Sala 8", "tipo_de_movimento": "Outra profissional da mesma terapia · mesmo turno", "observacao": ""},
            {"cenario": "Cenário 1 · equilibrado · libera 3 turnos", "turno_liberado": "Qua tarde · Ficticia Bromelia · Terapia Alimentar", "passo": 4, "paciente": "Paciente Ficticio Hortela", "terapia": "Terapia Alimentar", "de": "Qua 13:00 às 14:00 · Ficticia Bromelia · Sala 8", "para": "Qua 11:00 às 12:00 · Ficticia Acacia · Sala 7", "tipo_de_movimento": "Outra profissional da mesma terapia · outro turno", "observacao": ""}
          ]
        }
      ],
      "contem_dados_pacientes": true
    }
  ],
  "resposta_sensivel": true
}
```

## Resposta do chat: `resposta_sensivel`

`POST /agenda/chat` devolve `conversa_id`, `resposta` (o texto) e `blocos`. Desde 2026-11-23 há
também o campo opcional **`resposta_sensivel`**, no nível da resposta (ao lado do texto e dos
blocos, nunca dentro de um bloco), para o chatbot trocar o texto pelo texto fixo quando o usuário
não é do grupo da coordenação.

- **Semântica:** `true` sempre que a rodada (o turno atual) usou uma tool restrita, **com ou sem
  bloco**; ausente nas demais respostas (nunca `false` nem `null`). Tools restritas
  (`TOOLS_RESTRITAS` em `app/ai/relatorios.py`): `planejar_enxugamento`, `planejar_realocacao`,
  `pacientes_por_plano`, `listar_pacientes_por_profissional` e `gerar_relatorio` com
  `relatorio: "agenda_paciente"`.
- **Sem bloco também conta:** o panorama sem cenário (só contagens), a pergunta de profissional
  ambígua do enxugamento, a realocação que pergunta o que falta ou não encontra opção, os
  pacientes por plano ou por profissional sem resultado e a agenda de um paciente que não está na
  agenda trazem `resposta_sensivel: true` mesmo com `blocos: []`.
- A decisão é pelo nome das tools chamadas no turno, não pelo bloco. Uma tool restrita de um
  turno anterior da mesma conversa não marca o turno atual.
- O campo carrega só a flag: nenhum nome nem conteúdo. Os blocos não mudaram (o bloco com nomes
  continua com `contem_dados_pacientes: true`), e `GET /agenda/chat/{conversa_id}` não traz o
  campo.
- Campo opcional da versão 1 (regra em
  [`contrato-blocos-novos.md`](contrato-blocos-novos.md#campos-opcionais-versão-1)): o cliente
  ignora campo que não conhece.

Exemplo (panorama de enxugamento sem cenário, sem bloco):

```json
{
  "conversa_id": "3f1c…",
  "resposta": "Não encontrei movimentação que libere turno sem abrir outro, mantendo a terapia e a duração de cada paciente.",
  "blocos": [],
  "resposta_sensivel": true
}
```

Numa pergunta de disponibilidade, encaixe, plano semanal, profissionais ou numa conversa sem
tool, o campo não aparece.
