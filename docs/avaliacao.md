# Avaliação com casos reais

Régua para saber se uma mudança no RealocAI (prompt, tool, motor) **melhora ou piora** as
respostas às perguntas reais da coordenação. `scripts/avaliar_prompt_renderiza.py` mede se a
resposta é curta; esta avaliação mede se ela está **certa**.

Peças:

| Arquivo | O que é |
| --- | --- |
| `tests/casos_reais/casos.yaml` | Os casos: perguntas, data "hoje" fixa e critérios. |
| `tests/casos_reais/planilha.py` | Semana fictícia no formato cru da API do Sheets (a "cópia da planilha"). |
| `tests/casos_reais/oraculo.py` | Conta o esperado direto na grade crua, sem importar nada de `app`. |
| `tests/casos_reais/casos.py` | Leitura do YAML e os critérios. |
| `scripts/avaliar_casos_reais.py` | Roda os casos nos modos `--deterministico` e `--llm`. |

## Por que o esperado não vem do motor

Validar o motor com ele mesmo não prova nada. O esperado vem de duas fontes independentes:

1. **O YAML**, com o que a coordenação conferiu na planilha (janelas, frases proibidas, número
   máximo de planos etc.);
2. **O oráculo** (`oraculo.py`), que lê a mesma grade que o motor lê e conta do jeito mais simples
   possível: célula vazia sob um profissional, no expediente, é vaga; `FECHADO` não existe;
   qualquer texto é ocupado. Ele não importa `app` (há teste garantindo isso).

O motor lê a semana fictícia pelo caminho de produção (`GoogleSheetsDataSource` → parser →
engine). Uma divergência entre motor e oráculo aponta defeito no parser ou no motor.

O oráculo só entende o layout simples (uma sala por coluna, sem merge). As inconsistências da
planilha real continuam com os testes do parser (`tests/data_sources/`).

## Como rodar

```bash
# Gratuito, sem modelo: executa a tool de cada turno e aplica os critérios.
uv run python scripts/avaliar_casos_reais.py --deterministico
uv run python scripts/avaliar_casos_reais.py --deterministico --casos c01,c07

# Também roda no pytest, com xfail estrito nos casos marcados:
uv run pytest tests/casos_reais

# Com o agente real (PAGO). Sem --confirmar só mostra a estimativa.
uv run python scripts/avaliar_casos_reais.py --llm --preco-entrada X --preco-saida Y
uv run python scripts/avaliar_casos_reais.py --llm --preco-entrada X --preco-saida Y \
    [--repeticoes N] --confirmar
```

**Preços.** `--preco-entrada` e `--preco-saida` (US$ por milhão de tokens) são obrigatórios no
`--llm` e não têm padrão: confira na tabela de preços do provedor o valor do modelo configurado
no `.env` (`OPENAI_MODEL` ou `GOOGLE_MODEL`) antes de usar `--confirmar`. A estimativa mede o
prompt de sistema e os schemas das tools de verdade (cerca de 7,7 mil tokens por chamada) e conta
cada turno como uma conversa de 2 chamadas ao modelo. Com os 10 casos (18 turnos) e 1 repetição,
dá cerca de 303 mil tokens de entrada e 7,2 mil de saída. É um piso: o histórico dos turnos
seguintes de uma mesma conversa não entra.

**Planilha de testes.** Por padrão tudo roda sobre a semana fictícia. Com
`--planilha-id <ID>`, a planilha de testes no Google é lida **uma vez e mantida só em memória**,
porque tem nomes reais. Nada dela é gravado. Os casos com `cenario` e os marcados `so_ficticia` (que dependem dos
nomes fictícios) ficam como "pulado".

## Saída

Uma tabela por caso no terminal, com status, tempo, chamadas de tool, chamadas à planilha,
tokens (só no `--llm`) e o nome dos critérios que falharam. Não imprime o texto das respostas
nem nomes de pacientes. O detalhe de cada critério e de cada turno vai para
`scripts/output/casos_reais_<modo>_<data>.json` (pasta ignorada pelo git), também sem texto de
resposta, texto de tool ou nomes de pacientes.

| Status | Significado |
| --- | --- |
| `passou` | Todos os critérios aplicáveis passaram. |
| `FALHOU` | Algum critério falhou num caso que deveria passar. Código de saída 1. |
| `xfail` | Falhou, como esperado (o caso tem `xfail` no YAML). |
| `XPASS` | Passou, mas ainda está marcado `xfail`: tire a marca. Código de saída 1. |
| `n/a` | Nenhum critério se aplica a este modo (ex.: só critérios da resposta, no determinístico). |
| `pulado` | Caso `so_ficticia` (ou com `cenario`) rodando com `--planilha-id`. |

O `xfail` vale para os dois modos. c01 não tem `xfail`: no `--llm` ele fica vermelho até o prompt
levar o agente ao bloco de disponibilidade.

## Como adicionar um caso novo

A coordenadora (ou o time) manda **a pergunta exatamente como foi feita** e **a resposta certa,
conferida na planilha**. Com isso:

1. **Anonimize.** Nenhum nome de paciente entra no YAML, nem no `descricao`. Se o caso depende
   de um paciente, use um nome fictício.
2. **Garanta que a semana fictícia tem a situação.** Se o caso precisa de algo que
   `tests/casos_reais/planilha.py` não tem (outra especialidade, uma profissional lotada num
   horário), edite `AGENDA`. Cada sala tem 10 posições de manhã (07:00 a 11:30) e 10 à tarde
   (13:00 a 17:30): `.` é vaga, `#` é FECHADO, letra é paciente. Depois rode
   `uv run pytest tests/casos_reais` para conferir que os casos antigos não mudaram. Se a
   situação brigar com os casos antigos (ex.: poucas vagas onde outro caso precisa de várias),
   crie uma semana própria em `CENARIOS` e aponte o caso para ela com `cenario: <nome>`: o caso
   roda e é conferido pelo oráculo sobre essa semana, e a `AGENDA` padrão fica intacta.
3. **Escreva o caso** no fim de `casos.yaml`:

   ```yaml
   - id: c11
     descricao: Vagas de psicologia na sexta à tarde
     hoje: 2026-09-28            # "hoje" fixo da conversa
     turnos:
       - pergunta: tem psico livre na sexta à tarde?
         chamada:                # o que o --deterministico executa no lugar da LLM
           tool: gerar_relatorio
           args: {relatorio: vagas_livres, inicio: "{sexta}", fim: "{sexta}",
                  especialidades: [psico], periodo_do_dia: tarde}
     criterios:
       - {tipo: bloco_presente, bloco: disponibilidade}
       - {tipo: vagas_dentro_da_janela, inicio: "13:00", fim: "18:00"}
       - {tipo: maximo_de_frases, maximo: 3}
   ```

   `"{segunda}"` ... `"{sabado}"` viram a data daquele dia na semana de `hoje`. Para testar no
   `--deterministico` as regras do texto (vocabulário, lista com tela, fundamentação), um turno
   pode ter `respostas_simuladas`: o turno roda pelo agente real, com a tool e a verificação do
   código, e um modelo roteirizado responde esses textos em ordem (o segundo só se o primeiro for
   recusado). Isso testa a última barreira do código, não o modelo; o modelo é medido no `--llm`. Para conversas
   com mais de uma pergunta, liste os turnos em ordem; `turno: N` (a partir de 1) limita um
   critério a um turno. Um turno sem tool (ex.: "pode marcar") leva `chamada: null`.
4. **Escolha os critérios** (catálogo em `tests/casos_reais/casos.py`, `CRITERIOS`):

   | Critério | Verifica | Modo |
   | --- | --- | --- |
   | `bloco_presente {bloco}` | Algum bloco do tipo pedido no turno. | ambos |
   | `vagas_iguais_ao_oraculo {dias}` | Cada vaga de 30 min dos blocos de disponibilidade = cada célula vazia da grade, nos dias dados. | ambos |
   | `vagas_dentro_da_janela {inicio, fim}` | Toda faixa `HH:MM às HH:MM` (texto e blocos) começa em `inicio` ou depois e termina em `fim` ou antes; exige ao menos uma. | ambos |
   | `sem_sobreposicao_no_dia` | Sessões do mesmo dia não se cruzam. | ambos |
   | `nao_coube_com_alternativas` | Diz "não coube" e traz alternativas. | ambos |
   | `plano_em_vagas_reais {situacao?}` | Cada slot das linhas "Plano" (ou da `situacao` dada, como `Alternativa`) do plano semanal é uma célula vazia da grade. | ambos |
   | `alternativas_nos_dias_pedidos {dias}` | Toda "Alternativa" do plano semanal cai num dos `dias`. | ambos |
   | `alternativas_sem_repeticao` | Nenhuma "Alternativa" (dia, especialidade, horário, sala) aparece duas vezes. | ambos |
   | `alternativa_no_grupo_do_dia` | Toda "Alternativa" vem logo depois do "Não coube" da mesma especialidade e do mesmo dia. | ambos |
   | `sequencia_maxima {dias, especialidades, inicio, fim}` | Em cada dia, as linhas "Plano" são seguidas e são tantas quanto a maior sequência que o oráculo acha na grade (rótulos como na planilha: `Fono`, `TO`...). | ambos |
   | `texto_contem {frases}` | Todas as frases aparecem (sem diferenciar acento nem maiúsculas). | ambos |
   | `sem_datas` | Nem o texto nem o plano semanal citam datas (`28/10`, `2026-10-28`). | ambos |
   | `tools_chamadas {tools, nunca}` | O turno chamou todas as `tools` e nenhuma de `nunca`. | ambos |
   | `pacientes_por_plano_igual_ao_oraculo` | Em cada bloco `pacientes_por_plano`, (plano, paciente, atendimentos) = o que o oráculo conta na grade crua (hex exato, independente das faixas de matiz); avisos numéricos só quando devidos; ordem dos planos; sem linha de total. O detalhe só traz contagens. | ambos |
   | `sem_nomes_de_paciente` | Nenhum nome do bloco `pacientes_por_plano` no texto das tools (o que o modelo lê) nem na resposta. | ambos |
   | `frase_proibida {frases}` | Nenhuma das frases aparece. | ambos |
   | `estado_profissional {na_planilha, dia, inicio, fim}` | Diz o mesmo estado que o oráculo: não existe, não atende no dia, sem vaga na janela ou com vaga (`na_planilha: null` = profissional inexistente). | ambos |
   | `max_planos {maximo}` | De 1 a `maximo` planos ("Plano 1", "Opção 2"). | ambos |
   | `max_chamadas_planilha {maximo}` | Leituras da planilha no turno. | ambos |
   | `max_segundos {maximo}` | Tempo do turno abaixo do limite. | ambos |
   | `maximo_de_frases {maximo}` | Frases da resposta. | `--llm` |
   | `sem_linhas_hh_mm` | Nenhuma linha começando com `HH:MM às HH:MM`. | `--llm` |
   | `nao_pede_atendimento_id` | Não pede o id do atendimento. | `--llm` |
   | `vocabulario_proibido` | Nenhum termo de `VOCABULARIO_PROIBIDO`; no `--llm` vale para todos os casos. | `--llm` |

   No determinístico, "o que o usuário veria" é o texto da tool. No `--llm`, é a resposta do
   agente. Os blocos contam nos dois.
5. **Se o caso depende de algo que ainda não existe**, marque `xfail: <motivo>`. O pytest usa
   xfail estrito: quando a correção chegar, o caso vira XPASS e a marca precisa sair.
6. **Rode** `uv run python scripts/avaliar_casos_reais.py --deterministico --casos c11` e
   `uv run pytest tests/casos_reais`. Critério novo? Acrescente a função em `casos.py`, registre
   em `CRITERIOS` e cubra em `tests/casos_reais/test_criterios.py`.

## Casos atuais

| Caso | Pergunta | Status (determinístico) |
| --- | --- | --- |
| c01 | Vagas de um dia (pela tool de um dia, com relatório) | passou |
| c02 | Encaixe na terça, 10h às 12h, 30 min, qualquer especialidade | passou (`horario_maximo` e item sem especialidade) |
| c03 | Vagas quarta e quinta, fono e psicomotricidade, 10h às 12h | passou (`hora_inicio`/`hora_fim`) |
| c04 | Plano semanal quarta e quinta, 4 especialidades | passou (`planejar_semana`; critérios só nas linhas "Plano"; 1 leitura) |
| c05 | Nomes (apelido, grafia, três estados) | passou |
| c06 | Realocação com autorização do usuário | xfail: falta a tool de realocação por pedido |
| c07 | Vagas de segunda a sexta (1 leitura da planilha, < 20 s) | passou |
| c08 | Vocabulário proibido | passou (com `respostas_simuladas`; o modelo, no `--llm`) |
| c09 | Plano semanal com poucas vagas no mesmo dia (`cenario: poucas_vagas`) | passou (alternativas só nos dias pedidos, sem repetir, no grupo do dia; avisa dos outros dias) |
| c10 | "E se eu preferir sessões seguidas?" depois do plano | passou (`planejar_semana` com `seguidas`; maior sequência = oráculo; oferece ampliar a janela) |
| c11 | Pacientes por plano de saúde (`cenario: planos_por_cor`) | passou (`pacientes_por_plano`; contagens = oráculo; preto fora; avisos numéricos; nenhum nome no texto; o 2º turno roteirizado cita um nome e vira resumo determinístico) |
