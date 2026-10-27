"""Interpretação estruturada do pedido, antes do agente (atrás de `INTERPRETADOR_ATIVO`).

Uma chamada ao modelo com saída estruturada (JSON schema estrito, via
`with_structured_output(method="json_schema", strict=True)` do
`langchain-openai` instalado) transforma a conversa em `PedidoAgenda`. Se
`lacunas` vier preenchida, o código devolve UMA pergunta curta com tudo o que
falta, sem chamar o agente; vazia, a interpretação vai para o agente como
contexto e ele segue direto às ferramentas.

Desligado por padrão: só deve ser ligado se melhorar a régua dos casos reais
(`scripts/avaliar_casos_reais.py --llm`). Só para `AI_PROVIDER=openai`.

Temperatura: o pedido é temperatura baixa, mas o `ChatOpenAI` instalado
descarta temperatura diferente de 1 nos modelos `gpt-5*` de raciocínio (a não
ser com `reasoning_effort="none"`). Por isso `temperature=0` é enviado e vale
para os modelos que aceitam; o controle efetivo nos de raciocínio é
`OPENAI_REASONING_EFFORT_INTERPRETADOR`.
"""

import json
import logging
from collections.abc import Callable, Sequence
from datetime import date
from enum import StrEnum
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field

from app.ai.blocos_gerais import enumerar
from app.config import exigir, get_settings

logger = logging.getLogger(__name__)


class Intencao(StrEnum):
    DISPONIBILIDADE = "disponibilidade"
    ENCAIXE_PACIENTE = "encaixe_paciente"
    PLANO_SEMANAL = "plano_semanal"
    REALOCACAO = "realocacao"
    OCUPACAO = "ocupacao"
    QUALIDADE = "qualidade"
    OUTRO = "outro"


class Janela(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inicio: str | None = Field(description="Início da janela, HH:MM; null se não foi dito.")
    fim: str | None = Field(description="Fim da janela, HH:MM; null se não foi dito.")


class PedidoAgenda(BaseModel):
    """O pedido de quem pergunta, já interpretado. Todos os campos são obrigatórios
    (o modo estrito do JSON schema exige): o que não foi dito vai `null` ou vazio."""

    model_config = ConfigDict(extra="forbid")

    intencao: Intencao
    dias: list[str] = Field(
        description="Datas pedidas (AAAA-MM-DD), já resolvidas a partir de hoje; vazia se nenhuma."
    )
    janela: Janela | None = Field(description="Janela de horário pedida; null se nenhuma.")
    turno: Literal["manha", "tarde"] | None
    especialidades: list[str] = Field(description="Como foram ditas ('fono', 'TO').")
    profissionais: list[str] = Field(description="Nomes como foram ditos, sem corrigir.")
    duracao_min: int | None
    recorrente: bool = Field(description="Pedido que se repete toda semana.")
    autoriza_mover: bool = Field(description="Quem perguntou autorizou mover outros atendimentos.")
    paciente: str | None = Field(description="Nome do paciente, como foi dito; null se nenhum.")
    lacunas: list[str] = Field(
        description=(
            "O que REALMENTE falta para atender, em poucas palavras ('o dia', 'a "
            "especialidade'). Nunca inclua o que tem padrão: duração (30 min), "
            "profissional (qualquer), recorrência (grade semanal), horário (a partir de 08:00)."
        )
    )


_INSTRUCOES = """\
Você interpreta pedidos à agenda de uma clínica multidisciplinar (uso interno da \
equipe). Hoje é {hoje}. Extraia o pedido da ÚLTIMA mensagem, usando as anteriores \
como contexto: o que já foi dito antes conta como dito. Dias relativos ("quarta", \
"amanhã") viram a próxima ocorrência a partir de hoje, em AAAA-MM-DD. Não invente \
nada. Em `lacunas`, só o que impede responder; o que tem padrão nunca é lacuna.\
"""

#: Recebe a conversa e devolve o pedido interpretado (ou `None` se falhar).
Interpretador = Callable[[Sequence[BaseMessage]], PedidoAgenda | None]


def criar_interpretador(modelo: BaseChatModel, hoje: date) -> Interpretador:
    """Interpretador sobre `modelo`, com saída estruturada em JSON schema estrito."""
    estruturado = modelo.with_structured_output(PedidoAgenda, method="json_schema", strict=True)

    def interpretar(conversa: Sequence[BaseMessage]) -> PedidoAgenda | None:
        mensagens = [
            SystemMessage(_INSTRUCOES.format(hoje=hoje.isoformat())),
            *(m for m in conversa if isinstance(m, HumanMessage)),
        ]
        try:
            resultado: Any = estruturado.invoke(mensagens)
        except Exception as erro:
            logger.warning(
                "Interpretador falhou (%s); o agente segue sem ele.", type(erro).__name__
            )
            return None
        if isinstance(resultado, PedidoAgenda):
            return resultado
        try:
            return PedidoAgenda.model_validate(resultado)
        except Exception:
            logger.warning("Interpretador devolveu formato inesperado; o agente segue sem ele.")
            return None

    return interpretar


def pergunta_das_lacunas(pedido: PedidoAgenda) -> str:
    """UMA pergunta curta com tudo o que falta."""
    return f"Para eu sugerir, só preciso saber {enumerar(pedido.lacunas)}."


def contexto_para_o_agente(pedido: PedidoAgenda) -> SystemMessage:
    """O pedido interpretado, para o agente não perguntar o que já foi dito."""
    dados = json.dumps(pedido.model_dump(mode="json", exclude={"lacunas"}), ensure_ascii=False)
    return SystemMessage(
        "Interpretação do pedido (use como referência; não pergunte o que já está aqui; "
        f"o que estiver vazio segue os padrões): {dados}"
    )


def criar_interpretador_configurado(hoje: date) -> Interpretador | None:
    """O interpretador de `INTERPRETADOR_ATIVO`, ou `None` (desligado ou provedor sem suporte)."""
    settings = get_settings()
    if not settings.interpretador_ativo:
        return None
    if settings.ai_provider != "openai":
        logger.warning("INTERPRETADOR_ATIVO ignorado: só é suportado com AI_PROVIDER=openai.")
        return None
    parametros: dict[str, Any] = {
        "model": settings.openai_model_interpretador
        or exigir(settings.openai_model, "OPENAI_MODEL"),
        "api_key": exigir(settings.openai_api_key, "OPENAI_API_KEY"),
        "temperature": 0,
    }
    if settings.openai_reasoning_effort_interpretador:
        parametros["reasoning_effort"] = settings.openai_reasoning_effort_interpretador
    return criar_interpretador(ChatOpenAI(**parametros), hoje)
