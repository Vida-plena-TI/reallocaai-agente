# RealocAI

Agente de IA para **otimizar e sugerir alocações na agenda de uma clínica multidisciplinar**.

A agenda da clínica é organizada em consultas de **30 em 30 minutos**, distribuídas em **salas**,
com **profissionais fixos por sala/dia/turno**. O RealocAI lê essa agenda, identifica ociosidade,
conflitos e oportunidades de remanejamento, e propõe alocações melhores — sempre respeitando as
regras operacionais da clínica.

> **Status:** bootstrap da estrutura. Ainda não há lógica de negócio implementada.

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
`data_sources` — ele recebe dados já carregados e devolve decisões.

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

## Qualidade

```bash
uv run pytest              # testes
uv run pytest --cov=app    # testes com cobertura
uv run ruff check .        # lint
uv run ruff format .       # formatação
uv run mypy                # checagem de tipos
```

## Variáveis de ambiente

Veja `.env.example`. Resumo:

| Variável | Camada | Descrição |
| --- | --- | --- |
| `APP_ENV` | — | `development` (padrão), `staging` ou `production` |
| `OPENAI_API_KEY` | `app/ai` | Chave da OpenAI usada pelo `langchain-openai` |
| `GOOGLE_SHEETS_CREDENTIALS_PATH` | `app/data_sources` | Caminho do JSON da service account do Google |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | `app/data_sources` | ID da planilha da agenda |
| `RESEND_API_KEY` | `app/reports` | Chave da API do Resend |
| `REPORT_EMAIL_FROM` | `app/reports` | Remetente dos relatórios |
| `REPORT_EMAIL_TO` | `app/reports` | Destinatários, separados por vírgula |

O arquivo `.env` e as credenciais da service account estão no `.gitignore` — **nunca** os
versione.
