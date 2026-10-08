# RealocAI

Agente de IA para **otimizar e sugerir alocações na agenda de uma clínica multidisciplinar**.

A agenda da clínica é organizada em consultas de **30 em 30 minutos**, distribuídas em **salas**,
com **profissionais fixos por sala/dia/turno**. O RealocAI lê essa agenda, identifica ociosidade,
conflitos e oportunidades de remanejamento, e propõe alocações melhores — sempre respeitando as
regras operacionais da clínica.

## Estado do projeto

Fases 1 a 8 concluídas:

| Fase | Entrega |
| --- | --- |
| 1 | Estrutura inicial: esqueleto do projeto em camadas (`domain`/`engine`/`data_sources`/`ai`/`api`/`reports`) e configuração via `pydantic-settings`. |
| 2 | Domínio: entidades e regras estáticas da clínica (`Sala`, `Profissional`, `Slot`, `Atendimento`, `Paciente`, validação do grid de 30 minutos). |
| 3 | Fonte de dados: parser da planilha do Google Sheets (blocos, salas mescladas, convênio por cor, normalização de nomes) e o contrato de continuidade terapêutica. |
| 4a | Motor de disponibilidade e ocupação (`listar_disponibilidade`, `construir_relatorio_ocupacao_do_dia`). |
| 4b | Motor de encaixe casado e sugestão de realocação (`buscar_melhor_encaixe`, `buscar_alternativas`, `sugerir_realocacao`). |
| 5a | Camada de serviço da agenda (`app/ai/servico_agenda.py`), ponte entre a engine e a IA. |
| 5b | Agente LangChain e as tools que ele usa (`app/ai/agente.py`, `app/ai/tools.py`). |
| 6a | Esqueleto da API HTTP: autenticação por `X-API-Key`, CORS, cache de leitura. |
| 6b | Endpoint de conversa com o agente (`/agenda/chat`) e sessão em memória. |
| 7 | Relatório de ocupação por e-mail via Resend. |
| 8 | Cobertura de testes, teste de integração ponta a ponta, verificação automática das fronteiras arquiteturais (`tests/test_arquitetura.py`) e este fechamento da documentação. |

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
├── reports/       # Geração e envio de relatórios (Resend): resumos de ocupação e sugestões.
├── config.py      # Variáveis de ambiente centralizadas via pydantic-settings.
└── main.py        # Instância FastAPI e endpoint GET /health.

tests/             # Espelha a estrutura de app/ (tests/domain, tests/engine, ...).
```

### Direção das dependências

```
api ─┬─> engine ──> domain
     ├─> ai ──────> domain
     └─> reports ─> domain
              ^
data_sources ─┘
```

O `domain` não importa nada das outras camadas. O `engine` não depende de `ai`, de `api` nem de
`data_sources` — ele recebe dados já carregados e devolve decisões. O contrato de leitura da
agenda (`ScheduleDataSource`/`EntradaGrade`) mora em `app/domain` (não em `app/data_sources`)
justamente para isso — é a engine que depende dele, não o contrário.

Essa direção de dependência, junto com o isolamento de bibliotecas externas por camada
(`langchain*` só em `app/ai`, `fastapi`/`starlette` só em `app/api`, `gspread`/`google.auth`
só em `app/data_sources`, `resend` só em `app/reports`), é verificada automaticamente por
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
# {"status":"ok","service":"realocai"}
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
| `/relatorio/enviar` | POST | Envia por e-mail o relatório de ocupação de um dia (por sala e por especialidade). Ver detalhes abaixo. |

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
  vira um cartão na tela) e não oferece envio por e-mail por conta própria.
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
e ocupação agregada devolvem dados tipados em `app/ai/relatorios.py`, versão 1:

- `tipo`, `titulo`, `periodo` (início e fim ISO), `meta`, `parcial` e `avisos`;
- `meta`: `0.8` em `ocupacao_profissional` e `ocupacao_agregada`; `null` em
  `pacientes_por_profissional`, que não tem meta (suas tabelas não têm coluna de meta);
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
texto das tools e no e-mail de relatório (a ocupação agregada e o e-mail antes usavam
percentual sem casa decimal). `GET /agenda/ocupacao` também aceita percentual acima de 1.
Em caso de inconsistência na grade, a engine pode produzir ocupação acima de 100%.
O bloco é devolvido com o valor real (percentuais só têm limite inferior, `>= 0`):
`abaixo_da_meta` fica `false`, um aviso de ocupação acima de 100% entra em `avisos` (e no
texto) e, em `ocupacao_profissional`, `inconsistencia` fica `true`.
Os blocos não contêm nomes nem IDs de pacientes; IDs de profissionais são opacos.
Ambiguidade, profissional não encontrada, ausência de agenda, especialidade desconhecida
ou erro devolvem apenas texto, com `blocos: []`. Há no máximo dez blocos por turno.

Exemplos de cada variante (`ocupacao_profissional`, `pacientes_por_profissional` com
escopo semana e dia, `ocupacao_agregada`) e o JSON Schema de `BlocoRelatorio` ficam em
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
exportação por conta própria. O envio de relatório no corpo de e-mail já existente
continua sendo uma funcionalidade separada.

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

### `POST /relatorio/enviar`

Corpo da requisição (ambos os campos opcionais):

```json
{
  "data": null,
  "destinatarios": null
}
```

- `data`: omitida (ou `null`) para usar a data de hoje.
- `destinatarios`: omitida para usar a lista padrão configurada em `REPORT_EMAIL_TO`; uma
  lista explícita substitui esse padrão.

O relatório contém **apenas a ocupação por sala/especialidade** (mesmo dado de
`/agenda/ocupacao`) — sem lista de atendimentos aguardando autorização nem sugestões de
desfragmentação, que ficam fora do escopo desta fase.

Resposta:

```json
{
  "enviado": true,
  "destinatarios": ["coordenacao@suaclinica.com.br"]
}
```

Falha no envio (Resend fora do ar, credencial inválida etc.) devolve `502`, sem detalhar o
erro interno do Resend na resposta.

O mesmo envio também pode ser disparado **por conversa com o agente** (`/agenda/chat`),
através da tool `enviar_relatorio` — o agente só a aciona quando o pedido for claro e
explícito (ex.: "manda o relatório de hoje").

**A tool só existe quando o Resend está configurado.** `criar_tools` registra
`enviar_relatorio` apenas se `RESEND_API_KEY` e `REPORT_EMAIL_FROM` estiverem preenchidos
(`envio_de_email_configurado()` em `app/ai/tools.py`). A decisão é por configuração, não por
tentativa: sem as duas variáveis, o agente nem vê a tool, e o prompt de sistema (nas duas
variantes) diz que o envio por e-mail não está disponível neste ambiente, que o agente não deve
oferecê-lo e que, se pedirem, responde em uma frase que não está disponível. Com o Resend
configurado, a tool e o prompt voltam ao comportamento descrito acima, incluindo a regra de não
oferecer o e-mail por conta própria. Os valores de exemplo do `.env.example` não estão vazios:
para desligar o envio, deixe as duas variáveis em branco. Esta regra vale só para o agente; o
endpoint `POST /relatorio/enviar` não foi alterado.

Para revisar o e-mail sem o Resend, use `scripts/preview_email.py` (ver
[Scripts exploratórios](#scripts-exploratórios)).

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
- As regras de contagem são as mesmas de `/agenda/ocupacao` (`app/engine/ocupacao.py`); o
  cálculo vive em `app/engine/ocupacao_profissional.py`.
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
- O cálculo vive em `app/engine/carga_profissionais.py`, que lê cada dia **uma vez** para todas
  as profissionais (as leituras à fonte não crescem com o número de profissionais). Um dia cuja
  leitura falhe é sinalizado e os números ficam marcados como parciais. A resposta nunca traz
  nome de paciente, e no escopo `semana` termina com a mesma ressalva da grade semanal.

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

### `scripts/preview_email.py`

Gera localmente o e-mail do relatório de ocupação de uma data (padrão: hoje), com a planilha
real (`GoogleSheetsDataSource`), **sem usar o Resend e sem enviar nada**. Grava o HTML e o texto
do corpo em `scripts/output/preview_email.html` e `scripts/output/preview_email.txt` e imprime os
caminhos.

```bash
uv run python scripts/preview_email.py [AAAA-MM-DD]
```

Exige só as credenciais do Google Sheets. O relatório traz salas, especialidades e contagens,
sem nomes de pacientes, mas vem da agenda real: por isso a saída fica em `scripts/output/`, que
é git-ignorada.

### `scripts/avaliar_prompt_renderiza.py`

Avalia a variante do prompt com renderização (`renderiza_relatorios=True`) com o **modelo real**
e a **planilha real**. Repete cada pergunta N vezes (padrão 3), sempre em conversa nova:
"Qual a ocupação da Rossana?", "Quantos pacientes cada profissional atende por dia?",
"Ocupação por especialidade hoje" e "Exporta isso em Excel". Para cada execução imprime, só no
terminal, o número de frases da resposta, as tools chamadas, as tools além da esperada (a de
exportação não espera nenhuma), os blocos gerados e se todo número com vírgula decimal citado
aparece no texto da tool, e se a resposta oferece e-mail (qualquer menção a "e-mail", com
qualquer hífen ou nenhum, conta como falha, já que nenhuma pergunta pede e-mail); no fim, um
resumo por pergunta (até 3 frases, só as tools esperadas, números fiéis, sem oferta de e-mail). Não imprime o texto da resposta nem o das tools, e não grava arquivo. O envio
de e-mail é substituído por um registro local: se o agente chamar `enviar_relatorio`, aparece
como tool extra e nenhum e-mail é enviado.

```bash
uv run python scripts/avaliar_prompt_renderiza.py [AAAA-MM-DD] [--repeticoes N]
```

**Não roda em CI** nem no `uv run pytest` (só as funções puras de contagem têm teste): exige
`AI_PROVIDER` com a chave e o modelo do provedor, além das credenciais do Google Sheets.
**Custo aproximado de uma rodada padrão** (4 perguntas × 3 = 12 conversas, em geral 2 chamadas
ao modelo cada): o prompt de sistema e os schemas das tools somam cerca de 5 mil tokens por
chamada, mais o resultado das tools, o que dá por volta de 150 mil tokens de entrada e poucos
milhares de saída. Num modelo de US$ 2,50 por milhão de tokens de entrada e US$ 10 por milhão
de saída, isso fica em torno de US$ 0,40 por rodada; o custo escala linearmente com
`--repeticoes`. Com `AI_PROVIDER=google` no tier gratuito, não há custo, mas as ~24 chamadas
podem esbarrar no limite de requisições por minuto.

## Qualidade

```bash
uv run pytest              # testes
uv run pytest --cov=app    # testes com cobertura
uv run ruff check .        # lint
uv run ruff format .       # formatação
uv run mypy                # checagem de tipos
```

## Limitações conhecidas / próximos passos

Decisões já tomadas ao longo do projeto, documentadas aqui para quem for planejar o que vem
depois — nenhum destes pontos é um problema em aberto de "esqueceram de fazer":

- **Continuidade terapêutica é um contrato trivial hoje.** `SemHistoricoContinuidadeDataSource`
  (`app/data_sources/continuidade.py`) sempre devolve `None` — a integração real com o
  Agendador/API Gateway fica para quando esse sistema estiver em produção.
- **Realocação é sempre de um atendimento por vez.** `sugerir_realocacao` move um único
  atendimento; reorganizar vários pacientes de uma vez para destravar espaço (realocação em
  massa) não foi implementado.
- **O relatório por e-mail cobre só ocupação por sala/especialidade.** Lista de atendimentos
  aguardando autorização e sugestões de desfragmentação no relatório foram adiadas para uma
  feature futura.
- **A fonte de dados atual (Google Sheets) é temporária.** A migração para o Agendador via API
  Gateway ainda depende da documentação dessa API estar disponível.
- **O modelo de IA de produção ainda não foi decidido.** Enquanto isso, o projeto testa via
  Google Gemini (tier gratuito) — ver [Provedores de IA](#provedores-de-ia-appai).
- **Correspondência de paciente é por id normalizado exato, sem fuzzy matching.** Grafias
  diferentes do mesmo paciente na planilha podem gerar registros distintos.

## Variáveis de ambiente

Veja `.env.example`. Resumo:

| Variável | Camada | Descrição |
| --- | --- | --- |
| `APP_ENV` | — | `development` (padrão), `staging` ou `production` |
| `AI_PROVIDER` | `app/ai` | Provedor de chat model: `openai` ou `google` (sem padrão) |
| `OPENAI_API_KEY` | `app/ai` | Chave da OpenAI usada pelo `langchain-openai` |
| `OPENAI_MODEL` | `app/ai` | Modelo da OpenAI, exigido quando `AI_PROVIDER=openai` |
| `GOOGLE_API_KEY` | `app/ai` | Chave do Google AI Studio, exigida quando `AI_PROVIDER=google` |
| `GOOGLE_MODEL` | `app/ai` | Modelo Gemini, exigido quando `AI_PROVIDER=google` |
| `GOOGLE_SHEETS_CREDENTIALS_PATH` | `app/data_sources` | Caminho do JSON da service account do Google |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | `app/data_sources` | ID da planilha da agenda |
| `RESEND_API_KEY` | `app/reports` | Chave da API do Resend |
| `REPORT_EMAIL_FROM` | `app/reports` | Remetente dos relatórios |
| `REPORT_EMAIL_TO` | `app/reports` | Destinatários, separados por vírgula |

O arquivo `.env` e as credenciais da service account estão no `.gitignore` — **nunca** os
versione.
