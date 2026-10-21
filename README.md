# RealocAI

Agente de IA para **otimizar e sugerir alocações na agenda de uma clínica multidisciplinar**.

A agenda da clínica é organizada em consultas de **30 em 30 minutos**, distribuídas em **salas**,
com **profissionais fixos por sala/dia/turno**. O RealocAI lê essa agenda, identifica ociosidade,
conflitos e oportunidades de remanejamento, e propõe alocações melhores — sempre respeitando as
regras operacionais da clínica.

## Estado do projeto

Fases 1 a 9 concluídas:

| Fase | Entrega |
| --- | --- |
| 1 | Estrutura inicial: esqueleto do projeto em camadas (`domain`/`engine`/`data_sources`/`ai`/`api`) e configuração via `pydantic-settings`. |
| 2 | Domínio: entidades e regras estáticas da clínica (`Sala`, `Profissional`, `Slot`, `Atendimento`, `Paciente`, validação do grid de 30 minutos). |
| 3 | Fonte de dados: parser da planilha do Google Sheets (blocos, salas mescladas, convênio por cor, normalização de nomes) e o contrato de continuidade terapêutica. |
| 4a | Motor de disponibilidade e ocupação (`listar_disponibilidade`, `construir_relatorio_ocupacao_do_dia`). |
| 4b | Motor de encaixe casado e sugestão de realocação (`buscar_melhor_encaixe`, `buscar_alternativas`, `sugerir_realocacao`). |
| 5a | Camada de serviço da agenda (`app/ai/servico_agenda.py`), ponte entre a engine e a IA. |
| 5b | Agente LangChain e as tools que ele usa (`app/ai/agente.py`, `app/ai/tools.py`). |
| 6a | Esqueleto da API HTTP: autenticação por `X-API-Key`, CORS, cache de leitura. |
| 6b | Endpoint de conversa com o agente (`/agenda/chat`) e sessão em memória. |
| 7 | Relatório de ocupação por e-mail via Resend (removido na fase 11). |
| 8 | Cobertura de testes, teste de integração ponta a ponta, verificação automática das fronteiras arquiteturais (`tests/test_arquitetura.py`) e este fechamento da documentação. |
| 9 | Relatórios gerais: fotografia do período e motor de relatório genérico (`app/engine/fotografia.py`, `relatorio_generico.py`, `relatorios_prontos.py`), tools `gerar_relatorio` e `consultar_catalogo` e cinco tipos de bloco novos. |
| 10 | Integração dos relatórios gerais: as tools antigas sobre o motor novo (equivalência provada por golden), `GET /relatorios/catalogo` e `POST /relatorios/gerar`, e-mail com os relatórios gerais (removido na fase 11) e avaliação do prompt com os relatórios novos. |
| 11 | Remoção do envio de e-mail: Resend, `app/reports`, `POST /relatorio/enviar` e a tool `enviar_relatorio`. O RealocAI não envia e-mails. |

## Princípio central: IA sugere, o motor decide

Toda decisão de alocação **determinística** vive em `app/engine`, nunca no modelo de IA.

- `app/engine` é a única autoridade sobre o que é uma alocação **válida**: disponibilidade de sala,
  vínculo profissional × sala × turno, colisão de horários, duração do slot.
- `app/ai` (LangChain + `langchain-openai`) atua na camada de linguagem: interpretar pedidos em
  linguagem natural, priorizar entre alternativas já validadas, explicar as sugestões e redigir
  relatórios.
- Nenhuma sugestão chega ao usuário sem passar pela validação do `engine`. O modelo **não** é a
  fonte de verdade sobre a agenda — ele opera sobre o que o motor de regras autorizou.

Isso mantém o comportamento auditável e testável: as regras da clínica ficam em código
determinístico, coberto por testes, e não em um prompt.

## Estrutura de pastas

```
app/
├── domain/        # Modelos e regras de negócio puras (Sala, Profissional, Slot, Agendamento).
│                  # Sem I/O, sem framework — apenas o vocabulário da clínica.
├── data_sources/  # Acesso a dados externos: Google Sheets (gspread + google-auth).
│                  # Converte planilha ⇄ objetos de domínio. Nenhuma regra de negócio aqui.
├── engine/        # Motor de regras determinístico: validação de alocações, detecção de
│                  # conflitos/ociosidade e cálculo de remanejamentos. O cérebro do produto.
├── ai/            # Integração com LangChain + langchain-openai: interpretação de pedidos em
│                  # linguagem natural, ranqueamento e explicação. Nunca decide sozinho.
├── api/           # Rotas HTTP (FastAPI): routers, schemas de request/response, dependências.
├── config.py      # Variáveis de ambiente centralizadas via pydantic-settings.
├── main.py        # Instância FastAPI (`criar_app`) e endpoint GET /health.
└── __main__.py    # Servidor de produção: `python -m app` (valida a config e sobe o uvicorn).

tests/             # Espelha a estrutura de app/ (tests/domain, tests/engine, ...).
```

### Direção das dependências

```
api ─┬─> engine ──────> domain
     ├─> ai ──────────> domain
     └─> data_sources ─> domain
```

O `domain` não importa nada das outras camadas. O `engine` não depende de `ai`, de `api` nem de
`data_sources` — ele recebe dados já carregados e devolve decisões. O contrato de leitura da
agenda (`ScheduleDataSource`/`EntradaGrade`) mora em `app/domain` (não em `app/data_sources`)
justamente para isso — é a engine que depende dele, não o contrário.

Essa direção de dependência, junto com o isolamento de bibliotecas externas por camada
(`langchain*` só em `app/ai`, `fastapi`/`starlette` só em `app/api`, `gspread`/`google.auth`
só em `app/data_sources`), é verificada automaticamente por
`tests/test_arquitetura.py`, com as poucas exceções aceitas documentadas ali mesmo.

## Requisitos

- Python 3.14 (fixado em `.python-version`)
- [uv](https://docs.astral.sh/uv/)

## Rodando localmente

```bash
# 1. Instalar dependências (cria o .venv automaticamente)
uv sync

# 2. Configurar variáveis de ambiente
cp .env.example .env   # no Windows: copy .env.example .env
# edite o .env com as credenciais reais

# 3. Subir a API em modo desenvolvimento
uv run uvicorn app.main:app --reload
```

A API sobe em `http://127.0.0.1:8000`. Verifique com:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

Documentação interativa em `http://127.0.0.1:8000/docs`.

## Endpoints da API

Todos os endpoints abaixo (exceto `/health`) exigem o header `X-API-Key`, com o valor de
`INTERNAL_API_KEY` (`.env`). Requisição sem a chave, ou com a chave errada, recebe `401`.

| Endpoint | Método | Descrição |
| --- | --- | --- |
| `/health` | GET | Verificação de disponibilidade do serviço. Público, sem `X-API-Key`. |
| `/agenda/disponibilidade` | GET | Slots livres de um dia, com filtros opcionais de especialidade, profissional e sala. |
| `/agenda/ocupacao` | GET | Ocupação de um dia, agregada por sala e por especialidade. |
| `/agenda/chat` | POST | Envia uma mensagem ao agente de IA e devolve a resposta. Ver detalhes abaixo. |
| `/agenda/chat/{conversa_id}` | GET | Histórico bruto de uma conversa (útil para depuração). |
| `/relatorios/catalogo` | GET | Mapa da clínica na semana e o significado de cada relatório e métrica (o mesmo da tool `consultar_catalogo`). |
| `/relatorios/gerar` | POST | Relatório geral de qualquer período: o mesmo `{texto, bloco}` da tool `gerar_relatorio`. Contrato em [`docs/api-relatorios.md`](docs/api-relatorios.md). |

Os erros `422` de validação, em todas as rotas, trazem só `loc`, `msg` e `type` de cada erro: o
valor recebido nunca volta na resposta (o corpo de `/agenda/chat` pode ter nome de paciente).

### `POST /agenda/chat`

Corpo da requisição:

```json
{
  "conversa_id": null,
  "mensagem": "Tem vaga de psicologia hoje à tarde?",
  "renderiza_relatorios": false
}
```

- `conversa_id`: omitido (ou `null`) para iniciar uma conversa nova. Para continuar uma
  conversa existente, envie o `conversa_id` devolvido numa resposta anterior — o histórico
  completo do diálogo é reaproveitado automaticamente.
- `mensagem`: texto da pergunta ao agente (não pode ser vazia nem conter só espaços).
- `renderiza_relatorios`: opcional, `false` por padrão. Use `true` quando o cliente
  mostra as tabelas e os destaques dos relatórios na tela. Nesse modo, o agente
  responde em no máximo 3 frases curtas, em prosa, com os destaques, sem repetir a lista,
  a tabela nem os avisos; chama só as tools que a pergunta pede (cada tool de relatório
  vira um cartão na tela).
  Omitido ou `false` mantém as respostas detalhadas atuais.

Resposta:

```json
{
  "conversa_id": "3f1b1e4a-...",
  "resposta": "Sim, há um horário às 14h com a Dra. Ana na Sala 2.",
  "blocos": []
}
```

Conversas ficam **em memória** (perdidas num restart do processo) e expiram após um período
de inatividade. Um `conversa_id` inexistente ou expirado devolve `404`; um erro inesperado ao
rodar o agente (rede, modelo) devolve `502`, sem vazar detalhes internos na resposta.

O campo `blocos` está sempre presente, mesmo quando vazio e independentemente de
`renderiza_relatorios`. As consultas de ocupação profissional, pacientes por profissional
e ocupação agregada, e todos os relatórios de `gerar_relatorio` (ver
[Relatórios gerais](#relatórios-gerais-tools-gerar_relatorio-e-consultar_catalogo)), devolvem
dados tipados em `app/ai/relatorios.py`, versão 1:

- `tipo`, `titulo`, `periodo` (início e fim ISO), `meta`, `parcial` e `avisos`;
- `meta`: sempre `0.8` em `ocupacao_profissional` e `ocupacao_agregada`; sempre `null` em
  `pacientes_por_profissional`, `disponibilidade`, `convenio`, `agenda_paciente` e
  `qualidade_agenda` (suas tabelas não têm coluna de meta); em `tabela_generica`, `0.8`
  quando a ocupação (ou slots para a meta) foi pedida e `null` caso contrário;
- `dias_nao_lidos`: datas ISO, ordenadas, dos dias cuja leitura falhou; lista vazia
  exatamente quando `parcial` é `false`;
- `resumo`: valores e strings de exibição já formatadas;
- `dados`: união discriminada por `tipo`, com os detalhes específicos da consulta;
- `tabelas`: nomes, colunas (`chave`, `rotulo`, `formato`) e linhas prontas para exportação.

O tipo `ocupacao_profissional` traz a semana, os dias, manhã/tarde, salas/postos e
inconsistências, com as tabelas **Por dia** e **Resumo da semana**. Em `por_sala_posto`,
`posto` é o número de exibição, contado a partir de 1 (o primeiro posto da sala é 1),
igual ao "posto N" do texto das tools.
`pacientes_por_profissional` traz pacientes, sessões, slots, dias sem agenda, média
por dia com agenda e distintos por profissional no período; as tabelas são
**Por profissional e dia**, **Resumo por profissional** e **Clínica por dia**.
Os totais da clínica por dia representam a clínica inteira, mesmo com filtro de
profissional ou especialidade. A engine não fornece pacientes distintos da clínica
na semana: os totais diários não são somados como se fossem pessoas únicas na semana.
O resumo traz distintos e média quando disponíveis para a consulta individual semanal,
ou os distintos da clínica na consulta diária geral.
`ocupacao_agregada` traz o mesmo conteúdo de `/agenda/ocupacao`, com as tabelas
**Por especialidade** e **Por sala**.

Texto e blocos usam o mesmo resultado da engine. Os dados preservam valores sem
arredondamento, contagens inteiras, percentuais como frações e dias da semana por extenso.
As strings de exibição usam `ROUND_HALF_UP`, uma casa decimal e vírgula, também no
texto das tools (a ocupação agregada antes usava percentual sem casa decimal). `GET /agenda/ocupacao` também aceita percentual acima de 1.
Em caso de inconsistência na grade, a engine pode produzir ocupação acima de 100%.
O bloco é devolvido com o valor real (percentuais só têm limite inferior, `>= 0`):
`abaixo_da_meta` fica `false`, um aviso de ocupação acima de 100% entra em `avisos` (e no
texto) e, em `ocupacao_profissional`, `inconsistencia` fica `true`.
Os blocos não contêm nomes nem IDs de pacientes, com uma única exceção: `agenda_paciente`
traz o nome do paciente que o usuário pediu explicitamente. IDs de profissionais são opacos.
Ambiguidade, profissional não encontrada, ausência de agenda, especialidade desconhecida
ou erro devolvem apenas texto, com `blocos: []`. Há no máximo dez blocos por turno.

Exemplos de cada variante (`ocupacao_profissional`, `pacientes_por_profissional` com
escopo semana e dia, `ocupacao_agregada`, `disponibilidade` de vagas e de janelas,
`convenio`, `agenda_paciente`, `qualidade_agenda` e `tabela_generica` de ranking,
capacidade e personalizado) e o JSON Schema de `BlocoRelatorio` ficam em
`docs/exemplos-blocos/`, gerados com dados fictícios por
`uv run python scripts/gerar_exemplos_blocos.py`; um teste falha se ficarem desatualizados.

`GET /agenda/chat/{conversa_id}` devolve `blocos` associados a cada resposta do agente
(lista vazia nas mensagens do usuário). São armazenados separados do histórico textual,
e não enviados ao modelo nas rodadas seguintes. Clientes antigos podem ignorar o campo.

**O RealocAI não desenha relatórios nem gera PDF ou Excel.** O app visual de outro projeto
renderiza os dados e implementa a exportação. Se o usuário pedir exportação, com
`renderiza_relatorios: true` o agente informa que o relatório na tela tem botões
"Exportar"; com `false`, informa que a exportação não está disponível neste canal.
As duas variantes proíbem afirmar que um arquivo foi exportado ou enviado e oferecer
exportação por conta própria.

**O RealocAI não envia e-mails.** As duas variantes do prompt dizem para nunca oferecer envio
por e-mail e, se pedirem, responder em uma frase que o agente não envia e-mails.

Em `uv run python scripts/chat_manual.py --verbose`, cada turno também imprime o
tipo e o título dos blocos, além do rastro de tools. Por padrão o script usa a variante
de texto completo do prompt (`renderiza_relatorios: false`); com `--renderiza`, cria o
agente com `renderiza_relatorios=True`, a variante curta usada por clientes que mostram
os blocos na tela. As flags combinam:
`uv run python scripts/chat_manual.py 2026-10-05 --renderiza --verbose`.

Textos voltados ao usuário nos blocos (`titulo`, `resumo[].exibicao`, `avisos`, nomes de
tabelas) usam datas em dd/mm/aaaa, por exemplo "Ocupação de 05/10/2026"; datas ISO
aparecem só nos campos de dados (`periodo`, `data`, `dias_nao_lidos`, colunas de formato
`data`). As salas seguem ordem natural pelo número (Sala 1, Sala 2, Sala 10) no texto de
`consultar_ocupacao`, em `dados.por_sala` e na tabela **Por sala**.

### Ocupação de uma profissional (tool `consultar_ocupacao_profissional`)

Responde, pelo chat, perguntas como "qual a taxa de ocupação da profissional Rossana?": a
ocupação dela **dia a dia** (segunda a sábado da semana da data consultada, ou da semana de
hoje) e o **total da semana**, sempre frente à meta de 80%.

- O nome é resolvido entre os profissionais da semana sem diferenciar acento e caixa, e aceita
  nome completo ou parcial por palavras ("Rossana Belfort" encontra `Rossana`). Se mais de uma
  profissional corresponder, o agente lista as candidatas e pergunta qual é — nunca escolhe
  sozinho; se nenhuma corresponder, lista os nomes disponíveis.
- Cada dia traz percentual (uma casa decimal, ex.: `77,5%`), slots ocupados/escalados e livres,
  os blocos manhã/tarde, quantos slots faltam para a meta e, quando ela passou por mais de uma
  sala ou posto no dia, uma linha por sala/posto. O total da semana é **ponderado** pelos slots
  de cada dia (não é a média dos percentuais diários).
- As regras de contagem são as mesmas de `/agenda/ocupacao` (`app/engine/ocupacao.py`). A tool lê
  a semana numa fotografia só (`fotografar_periodo`) e monta o resultado em
  `app/engine/visoes_fotografia.py`; os modelos ficam em `app/engine/ocupacao_profissional.py`.
- Dias sem escala dela aparecem como "Sem agenda"; um dia cuja leitura falhe é sinalizado e os
  totais da semana ficam marcados como parciais; atendimento fora dos horários escalados gera
  um aviso de possível inconsistência na planilha.

**Limitação:** a planilha é uma grade semanal (uma aba por dia da semana, sem datas), então os
números refletem a grade vigente, e não uma semana específica do calendário — a resposta da tool
sempre traz essa ressalva (`NOTA_GRADE_SEMANAL` em `app/ai/tools.py`, a remover na migração
para o Agendador).

### Pacientes por profissional (tool `consultar_pacientes_por_profissional`)

Responde, pelo chat, perguntas como "quantos pacientes cada profissional atende por dia?":
quantos **pacientes** (pessoas, não slots) cada profissional atende. Sem pedir confirmação, o
padrão é a semana de hoje, todas as profissionais, com o detalhe por dia.

- **Escopos:** `semana` (padrão; segunda a sábado da semana da data) traz, por profissional, os
  pacientes de cada dia com agenda, a média por dia e os pacientes distintos na semana, além dos
  pacientes distintos da clínica por dia; `dia` (quando a pergunta cita "hoje", "na terça" ou uma
  data) traz o total de cada profissional naquele dia e os pacientes distintos da clínica no dia.
- **Filtros opcionais:** uma profissional (resolvida como em `consultar_ocupacao_profissional`,
  com aliases e pergunta em caso de ambiguidade; traz também sessões e slots ocupados por dia) e
  uma especialidade, reconhecida de forma tolerante ("psicóloga", "fono", "TO") pela mesma tabela
  que o parser usa (`app/domain/especialidade_texto.py`).
- **O que conta como paciente:** cada paciente conta **uma vez por profissional e dia**, mesmo que
  apareça em dois blocos ou em dois postos; numa sessão em grupo (`Ana/Beto` na célula), cada
  paciente conta. Sessões = número de atendimentos; slots ocupados seguem a mesma conta da
  ocupação. Na linha da clínica, quem passa por mais de uma profissional no dia conta uma vez só
  — por isso ela pode ser menor que a soma das linhas por profissional.
- **Média:** soma dos pacientes dos dias com agenda (grade ou atendimento) dividida pelo número
  desses dias; um dia com agenda e nenhum paciente entra como 0. Profissional sem agenda no
  período não aparece; com agenda e nenhum paciente aparece com 0.
- O cálculo vive em `app/engine/visoes_fotografia.py` (modelos em
  `app/engine/carga_profissionais.py`), sobre a fotografia do período: cada dia é lido **uma vez**
  para todas as profissionais (as leituras à fonte não crescem com o número de profissionais). Um dia cuja
  leitura falhe é sinalizado e os números ficam marcados como parciais. A resposta nunca traz
  nome de paciente, e no escopo `semana` termina com a mesma ressalva da grade semanal.

### Relatórios gerais (tools `gerar_relatorio` e `consultar_catalogo`)

Em vez de uma tool fixa por relatório (e uma chamada por dia), o agente responde qualquer
pedido de período ou combinado com **uma** chamada a `gerar_relatorio`. "Horários vagos de
segunda a sexta" passou de 5 chamadas a `consultar_disponibilidade` e ~110 s (com a
planilha real e o modelo de produção) para 1 chamada e 13 a 18 s ponta a ponta; a tool em
si leva ~2 s com o cache frio, e o resto é o modelo.

**Como funciona** (camadas):

1. `fotografar_periodo` (`app/engine/fotografia.py`) lê cada dia do intervalo **uma vez**,
   em paralelo (até 6), sem domingo, e normaliza tudo por slot: data, profissional,
   especialidade, sala, posto, manhã/tarde, estado (`livre`, `ocupado` ou `fechado` = posto
   sem ninguém escalado), convênio e autorização. Um dia ilegível entra em `dias_nao_lidos`
   e o resto segue (relatório parcial). O id do paciente é interno: nunca sai.
2. `gerar_relatorio` (`app/engine/relatorio_generico.py`) agrupa por **dimensões** (`dia`,
   `profissional`, `especialidade`, `sala`, `posto`, `periodo`, `convenio`) e calcula
   **métricas** (`ocupacao`, `slots_escalados`, `slots_ocupados`, `vagas_livres`,
   `slots_para_meta`, `slots_fechados`, `pacientes_distintos`, `sessoes`, `horas_*`,
   `aguardando_autorizacao`, `janelas_livres`), com filtros (profissional, especialidade,
   sala, convênio, dia da semana, manhã/tarde), ordenação e limite. O total é recalculado
   sobre o recorte inteiro (pacientes distintos não são a soma das linhas; a ocupação é
   ponderada, nunca média de percentuais).
3. `app/engine/relatorios_prontos.py`: `vagas_livres`, `janelas_continuas` (1 h, 1 h 30...),
   `ranking_ocupacao`, `comparativo_dias`, `convenios` (com aguardando autorização),
   `qualidade_agenda` (buracos e vagas isoladas), `salas_ociosas`,
   `capacidade_especialidade` (sobrando / equilibrada / no limite, a partir de 95%) e
   `agenda_paciente`.

**Regras de contagem** — as mesmas das engines antigas, e um teste de equivalência
(`tests/engine/test_equivalencia_relatorios.py`) garante que, no mesmo recorte, os números
batem exatamente com `consultar_ocupacao`, `consultar_ocupacao_profissional`,
`consultar_pacientes_por_profissional` e `consultar_disponibilidade`.

**As quatro tools antigas rodam sobre o motor novo.** `consultar_disponibilidade`,
`consultar_ocupacao`, `consultar_ocupacao_profissional` e `consultar_pacientes_por_profissional`
fazem uma fotografia por chamada (o dia, ou a semana) e montam o resultado em
`app/engine/visoes_fotografia.py`, com `calcular_metricas` do motor genérico. Nome, descrição,
argumentos, texto e bloco não mudaram: `tests/ai/test_golden_tools.py` compara cada tool com o
golden capturado do código antigo (`tests/ai/golden/`, 74 casos, inclusive os de borda) e com o
contrato que o modelo vê (nome, descrição e schema). O golden nunca é regerado para fazer um teste
passar. `listar_disponibilidade` e `construir_relatorio_ocupacao_do_dia` continuam existindo
porque o encaixe e as rotas `GET /agenda/*` usam.

Regras antigas preservadas de propósito: a ocupação **por sala** do dia só conta atendimento que
caiu numa janela da grade; profissional listado sem grade aparece com 0 de 0; a disponibilidade
casa atendimento e vaga pelo posto exato (como o encaixe); sala e profissional são nomeados só
com a lista do próprio dia.

- slots de 30 min; cada posto da sala é uma vaga independente; sessão em grupo ocupa o slot
  uma vez e cada paciente conta;
- atendimento em outro posto do mesmo profissional vai para o menor posto escalado dele no
  horário; atendimento sem grade conta como ocupado, mas fica fora das quebras por sala e
  posto (com aviso);
- janela contínua = vagas seguidas do mesmo profissional, sala e posto, sem atravessar a
  pausa; `janelas_livres` conta quantos atendimentos de N slots cabem (`floor(faixa / N)`);
- buraco = faixa livre com atendimento imediatamente antes e depois no mesmo posto;
- convênio só existe em slot ocupado, por isso não combina com métricas de oferta (a tool
  explica isso ao modelo).

**A tool** (`app/ai/tools_relatorios.py`) recebe `relatorio`, `inicio`/`fim` (padrão: a
semana de hoje; só `inicio` = um dia; até 62 dias), filtros por nome em texto livre
(resolvidos como em `consultar_ocupacao_profissional`, com pergunta em caso de
ambiguidade) e, em `personalizado`, `dimensoes`, `metricas`, `ordenar_por`, `ordem` e
`limite`. Usa `response_format="content_and_artifact"`: o bloco vai como artefato e o modelo
recebe só um resumo curto (total, destaques e até 10 linhas), com percentuais formatados a
partir dos inteiros. Parâmetro inválido, inclusive erro de schema, volta como mensagem útil;
parâmetro que só sobra num relatório pronto é ignorado com uma nota, sem nova chamada.

`consultar_catalogo` descreve a semana: profissionais por especialidade (dias e salas),
salas e postos, convênios por cor, dias com agenda e a definição de cada métrica e
relatório. Não traz pacientes.

**Privacidade:** `agenda_paciente` é o único relatório que cita um paciente, e só o pedido
pelo nome (correspondência exata, sem sugerir nomes parecidos). Os demais mostram apenas
contagens. Testes cobrem os blocos, o texto devolvido ao modelo e os logs.

**Blocos novos** (aditivos na versão 1; clientes antigos podem ignorar tipos que não
conhecem): `disponibilidade` (faixas de vagas por dia, profissional, sala e posto, e resumo
por dia/manhã/tarde), `convenio`, `agenda_paciente`, `qualidade_agenda` e `tabela_generica`
(dimensões x métricas, com `colunas`, `linhas`, `total` e `linhas_omitidas`). Todos seguem
o mesmo contrato e trazem `tabelas` para exportação em Excel (uma aba por tabela).

A documentação interativa completa (`/docs`) descreve o schema exato de cada endpoint,
incluindo os campos de `/agenda/disponibilidade` e `/agenda/ocupacao`.

## Provedores de IA (`app/ai`)

O agente (`app/ai/agente.py`) suporta dois provedores de chat model, escolhidos pela
variável `AI_PROVIDER` — não há um provedor "padrão" fixado no código:

- **`AI_PROVIDER=openai`** (`langchain-openai`): exige `OPENAI_API_KEY` e `OPENAI_MODEL`.
  Provedor pretendido para produção; o modelo definitivo ainda não foi decidido.
- **`AI_PROVIDER=google`** (`langchain-google-genai`, Gemini): exige `GOOGLE_API_KEY` e
  `GOOGLE_MODEL`. Útil para testar o agente de ponta a ponta **sem custo**, com uma chave
  gratuita do [Google AI Studio](https://aistudio.google.com), antes de decidir o modelo
  definitivo da OpenAI para produção.

Um `AI_PROVIDER` ausente, com valor diferente de `"openai"`/`"google"`, ou sem as
variáveis exigidas pelo provedor escolhido, faz `criar_chat_model()` levantar um erro
claro — só é validado no momento de uso, nunca em `Settings()` (ver `.env.example`).

## Scripts exploratórios

`scripts/` fica **fora** do pacote `app`: são ferramentas pontuais de investigação, não parte
da aplicação. Nada em `app/` importa daqui.

### `scripts/inspect_sheet.py`

Conecta na planilha real da agenda (via `gspread` + service account) e imprime, para cada aba:
nome, dimensões do conteúdo real (ignorando linhas/colunas vazias no fim), as primeiras 40
linhas de valores em formato de tabela e a lista de intervalos de células mescladas.

Serve para conhecer a estrutura real da planilha antes de escrever o parser definitivo em
`app/data_sources`.

```bash
uv run python scripts/inspect_sheet.py
```

Pré-requisitos: `GOOGLE_SHEETS_CREDENTIALS_PATH` e `GOOGLE_SHEETS_SPREADSHEET_ID` no `.env`, e a
planilha compartilhada com o e-mail da service account (basta permissão de leitura).

O relatório é impresso no terminal e salvo em `scripts/output/sheet_inspection.txt`. Essa pasta
é git-ignorada — o output contém dados reais de pacientes e profissionais.

### `scripts/avaliar_prompt_renderiza.py`

Avalia a variante do prompt com renderização (`renderiza_relatorios=True`) com o **modelo real**
e a **planilha real**. Cada pergunta é feita N vezes (padrão **1**), sempre em conversa nova:

- as de sempre: "Qual a ocupação da Rossana?", "Quantos pacientes cada profissional atende por
  dia?", "Ocupação por especialidade hoje" e "Exporta isso em Excel";
- as dos relatórios gerais (todas esperam `gerar_relatorio` com o relatório certo): vagas de
  segunda a sexta (`vagas_livres`), janelas de 1 h 30 (`janelas_continuas`), ranking de ocupação,
  convênios e aguardando autorização, buracos e fragmentação (`qualidade_agenda`) e a agenda de um
  paciente (`agenda_paciente`). Esta última roda sobre a **semana fictícia** de
  `tests/support/agenda_clinica.py`, com um nome fictício: nenhum nome real fica no script.

Para cada execução imprime, só no terminal: as tools e o relatório escolhidos, o número de chamadas
ao modelo e às tools, o tempo, o número de frases (a resposta curta tem de 1 a 3), se todo número
com vírgula decimal citado aparece no texto da tool, **quantos** nomes de pacientes aparecem na
resposta (comparando com os pacientes da fonte, fora o nome pedido e os que também são nome de
profissional). No fim, um resumo por pergunta, com o tempo médio. Nunca imprime o texto da
resposta, o das tools nem nomes de pacientes, e não grava arquivo.

**Custo antes de rodar.** Sem `--confirmar`, o script só mostra a estimativa de custo e o comando
para rodar, sem chamar o modelo nem ler a planilha. A estimativa mede o prompt de sistema e os
schemas das tools de verdade (hoje, cerca de 5,8 mil tokens por chamada), supõe 2 chamadas ao
modelo por conversa, 1,5 mil tokens de resultado das tools e 400 de saída, e usa US$ 2,50 por
milhão de tokens de entrada e US$ 10 por milhão de saída (ajustáveis com `--preco-entrada` e
`--preco-saida`, conforme o modelo de `OPENAI_MODEL`). Com 10 perguntas e 1 repetição, dá cerca de
131 mil tokens de entrada, 4 mil de saída e **US$ 0,37**; o custo escala linearmente com
`--repeticoes`.

```bash
uv run python scripts/avaliar_prompt_renderiza.py                 # só a estimativa
uv run python scripts/avaliar_prompt_renderiza.py --confirmar     # roda (pago)
uv run python scripts/avaliar_prompt_renderiza.py [AAAA-MM-DD] [--repeticoes N] --confirmar
```

**Não roda em CI** nem no `uv run pytest` (só as funções puras e a estimativa têm teste): exige
`AI_PROVIDER` com a chave e o modelo do provedor, além das credenciais do Google Sheets. Com
`AI_PROVIDER=google` no tier gratuito, não há custo, mas as chamadas podem esbarrar no limite de
requisições por minuto.

## Qualidade

```bash
uv run pytest              # testes
uv run pytest --cov=app    # testes com cobertura
uv run ruff check .        # lint
uv run ruff format .       # formatação
uv run mypy                # checagem de tipos
```

## Deploy (Docker / Easypanel)

```bash
docker build -t realocai .
docker run --env-file .env.producao -p 8000:8000 realocai
```

- **Porta interna:** `8000` (ou `PORT`). **Health check:** `GET /health`, público.
- **1 worker, de propósito:** conversas (TTL 4 h) e cache da agenda (TTL 60 s) vivem na memória
  do processo. Como a API do Sheets devolve todas as abas numa chamada, a leitura de um dia
  já guarda a semana inteira (segunda a sábado); o cache de `GoogleSheetsDataSource` respeita
  `CACHE_TTL_SEGUNDOS` (antes guardava cada dia até o restart). Não aumente workers nem réplicas: uma conversa cairia num processo que não a
  conhece. Pelo mesmo motivo, **todo deploy/restart apaga as conversas em andamento**.
- **Nada é gravado em disco:** não há volume nem banco/Redis. A credencial do Google vai em
  `GOOGLE_SERVICE_ACCOUNT_JSON` (ex.: `base64 -w0 credentials/google-service-account.json`).
- O container roda como usuário não-root (UID 10001), atrás do Traefik com `--proxy-headers`
  (`FORWARDED_ALLOW_IPS="*"`: não publique a porta do container direto na internet).
- Debug/verbose do LangChain e tracing do LangSmith são forçados para desligado na
  inicialização, mesmo que `LANGCHAIN_TRACING_V2` esteja no ambiente.

## Limitações conhecidas / próximos passos

Decisões já tomadas ao longo do projeto, documentadas aqui para quem for planejar o que vem
depois — nenhum destes pontos é um problema em aberto de "esqueceram de fazer":

- **Continuidade terapêutica é um contrato trivial hoje.** `SemHistoricoContinuidadeDataSource`
  (`app/data_sources/continuidade.py`) sempre devolve `None` — a integração real com o
  Agendador/API Gateway fica para quando esse sistema estiver em produção.
- **Realocação é sempre de um atendimento por vez.** `sugerir_realocacao` move um único
  atendimento; reorganizar vários pacientes de uma vez para destravar espaço (realocação em
  massa) não foi implementado.
- **A fonte de dados atual (Google Sheets) é temporária.** A migração para o Agendador via API
  Gateway ainda depende da documentação dessa API estar disponível.
- **O modelo de IA de produção ainda não foi decidido.** Enquanto isso, o projeto testa via
  Google Gemini (tier gratuito) — ver [Provedores de IA](#provedores-de-ia-appai).
- **Correspondência de paciente é por id normalizado exato, sem fuzzy matching.** Grafias
  diferentes do mesmo paciente na planilha podem gerar registros distintos.

## Variáveis de ambiente

Veja `.env.example`. Resumo:

| Variável | Obrigatória | Descrição |
| --- | --- | --- |
| `APP_ENV` | não | `development` (padrão), `staging` ou `production` (padrão na imagem Docker) |
| `INTERNAL_API_KEY` | **sim** | Chave exigida no header `X-API-Key` (todas as rotas, menos `/health`) |
| `AI_PROVIDER` | **sim** | `openai` ou `google` |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | se `AI_PROVIDER=openai` | Chave e modelo da OpenAI |
| `GOOGLE_API_KEY` / `GOOGLE_MODEL` | se `AI_PROVIDER=google` | Chave e modelo do Google AI Studio |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | **sim** | ID da planilha da agenda |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | uma das duas | JSON da service account (puro ou base64); prioridade sobre o arquivo, só em memória |
| `GOOGLE_SHEETS_CREDENTIALS_PATH` | uma das duas | Caminho do JSON da service account (uso local) |
| `CORS_ALLOWED_ORIGINS` | **em production** | Origens liberadas, separadas por vírgula (`*` é recusado) |
| `LOG_LEVEL` | não | `DEBUG`, `INFO` (padrão), `WARNING`, `ERROR`, `CRITICAL` |
| `DOCS_ENABLED` | não | Força `/docs`, `/redoc`, `/openapi.json`; sem valor, ligados só em `development` |
| `PORT` | não | Porta do servidor (padrão 8000) |
| `CACHE_TTL_SEGUNDOS` | não | TTL do cache em memória da agenda (padrão 60) |

A configuração é validada na inicialização (`validar_configuracao` em `app/config.py`): se
faltar algo, o servidor não sobe e lista **todas** as variáveis com problema, sem imprimir
valores.

O arquivo `.env` e as credenciais da service account estão no `.gitignore` — **nunca** os
versione.
