"""Modelos de resposta da API HTTP (Fase 6a).

Contrato próprio da API: nunca reexporta as entidades de `app.domain`
diretamente como resposta HTTP, para a API não quebrar toda vez que um
detalhe interno do domínio mudar.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ai.relatorios import BlocoRelatorio
from app.ai.tools_relatorios import ArgsGerarRelatorio

#: Teto de `limite` em `POST /relatorios/gerar`.
MAX_LINHAS_RELATORIO = 200


class SlotDisponivelResponse(BaseModel):
    """Um horário livre, já com nomes resolvidos (não ids) para exibição."""

    model_config = ConfigDict(frozen=True)

    horario: str
    sala: str
    profissional: str
    especialidade: str


class OcupacaoItemResponse(BaseModel):
    """Ocupação agregada de uma sala ou de uma especialidade."""

    model_config = ConfigDict(frozen=True)

    rotulo: str
    slots_escalados: int
    slots_ocupados: int
    percentual: float = Field(ge=0.0)
    abaixo_da_meta: bool


class OcupacaoResponse(BaseModel):
    """Ocupação consolidada de um dia, por sala e por especialidade."""

    model_config = ConfigDict(frozen=True)

    data: str
    por_sala: list[OcupacaoItemResponse]
    por_especialidade: list[OcupacaoItemResponse]


class ChatRequest(BaseModel):
    """Corpo de `POST /agenda/chat`."""

    model_config = ConfigDict(frozen=True)

    conversa_id: str | None = Field(
        default=None, description="Id de uma conversa já existente. Omitido para iniciar uma nova."
    )
    mensagem: str = Field(min_length=1, description="Mensagem do usuário para o agente.")
    renderiza_relatorios: bool = Field(
        default=False,
        description=(
            "Informe true se o cliente mostra os blocos de relatório na tela. "
            "O agente então responde brevemente; false mantém a resposta detalhada."
        ),
    )

    @field_validator("mensagem")
    @classmethod
    def _rejeitar_mensagem_em_branco(cls, valor: str) -> str:
        if not valor.strip():
            raise ValueError("mensagem não pode conter só espaços em branco.")
        return valor


class ChatResponse(BaseModel):
    """Resposta de `POST /agenda/chat`."""

    model_config = ConfigDict(frozen=True)

    conversa_id: str
    resposta: str
    blocos: list[BlocoRelatorio] = Field(
        default_factory=list,
        description=(
            "Relatórios estruturados do turno, para o app visual desenhar e exportar. "
            "Sempre presente; vazio quando não houver relatório. O RealocAI não gera arquivos."
        ),
    )


class MensagemHistoricoResponse(BaseModel):
    """Uma mensagem do histórico bruto de uma conversa (`GET /agenda/chat/{conversa_id}`)."""

    model_config = ConfigDict(frozen=True)

    papel: str
    conteudo: str
    blocos: list[BlocoRelatorio] = Field(
        default_factory=list,
        description="Relatórios associados à resposta do agente; vazio nas mensagens do usuário.",
    )


class EnviarRelatorioRequest(BaseModel):
    """Corpo de `POST /relatorio/enviar`."""

    model_config = ConfigDict(frozen=True)

    data: date | None = Field(
        default=None,
        description="Data do relatório. Omitida (ou `null`) para usar a data de hoje.",
    )
    destinatarios: list[str] | None = Field(
        default=None,
        description=(
            "Lista de e-mails destinatários. Omitida para usar a lista padrão "
            "configurada em REPORT_EMAIL_TO."
        ),
    )


class EnviarRelatorioResponse(BaseModel):
    """Resposta de `POST /relatorio/enviar`."""

    model_config = ConfigDict(frozen=True)

    enviado: bool
    destinatarios: list[str] = Field(
        description="Destinatários efetivamente usados no envio, já resolvidos."
    )


class GerarRelatorioRequest(ArgsGerarRelatorio):
    """Corpo de `POST /relatorios/gerar`: os mesmos parâmetros da tool `gerar_relatorio`.

    O nome do paciente (`agenda_paciente`) só existe aqui, no corpo: nunca na
    URL, que vai para o access log.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    limite: int | None = Field(
        default=None,
        ge=1,
        le=MAX_LINHAS_RELATORIO,
        description=f"Máximo de linhas (ex.: top 5), até {MAX_LINHAS_RELATORIO}.",
    )
    paciente: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "Só em agenda_paciente: o nome do paciente exatamente como está na agenda. A busca "
            "é exata, sem sugestão de nomes parecidos, e o nome nunca vai para log."
        ),
    )


class RelatorioResponse(BaseModel):
    """Resposta de `POST /relatorios/gerar`: o mesmo texto e bloco da tool."""

    model_config = ConfigDict(frozen=True)

    texto: str = Field(description="Resumo curto, com os números já calculados.")
    bloco: BlocoRelatorio | None = Field(
        description="O relatório estruturado; null quando não há agenda no período."
    )


class ItemCatalogoResponse(BaseModel):
    """Um relatório pronto ou uma métrica, com o que ele significa."""

    model_config = ConfigDict(frozen=True)

    nome: str
    descricao: str


class CatalogoResponse(BaseModel):
    """Resposta de `GET /relatorios/catalogo`."""

    model_config = ConfigDict(frozen=True)

    semana_inicio: date
    semana_fim: date
    texto: str = Field(description="O mesmo texto da tool consultar_catalogo.")
    relatorios: list[ItemCatalogoResponse]
    metricas: list[ItemCatalogoResponse]
    dimensoes: list[str]
    parcial: bool = Field(description="Se algum dia da semana não pôde ser lido.")
