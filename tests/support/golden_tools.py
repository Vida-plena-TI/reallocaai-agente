"""Golden das tools antigas: o texto e o bloco que elas devolviam antes de irem
para a fotografia e o motor genérico.

Os arquivos em `tests/ai/golden/` foram gerados com o código antigo
(`uv run python -m tests.support.golden_tools`) e nunca devem ser regerados
para fazer um teste passar: eles são a prova de que a reescrita não mudou o
que o modelo e o front recebem.

Além da semana fictícia de `agenda_clinica`, há uma variante "borda" com os
casos em que o motor novo e o antigo poderiam divergir: atendimento fora da
grade, atendimento num posto em que a profissional não está escalada,
profissional listado sem grade (especialidade 0/0), ocupação acima de 100%,
nome ambíguo, profissional fora de `listar_profissionais`, sala fora de
`listar_salas` num dia e o mesmo profissional em dois postos da sala no mesmo
horário.

Mudanças intencionais de comportamento, atualizadas entrada por entrada (nunca
o arquivo inteiro), em `feature/catalogo-agenda`: o contrato das tools que
ganharam parâmetros, enums ou passaram a aceitar nome e apelido; e as saídas
de `consultar_disponibilidade` com profissional, que passou a usar o
resolvedor de nomes e o catálogo da semana ("beto" agora encontra Beto Reis;
quem está na grade mas fora de `listar_profissionais` agora aparece); e as
de especialidade fora da semana, que o enum do catálogo recusa antes de chegar
à tool ("Parâmetros inválidos (especialidade)").
"""

import json
from collections.abc import Callable
from datetime import date, time
from pathlib import Path
from typing import Any, cast

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from app.ai.tools import criar_tools
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.domain import Atendimento, Especialidade, Profissional, Slot
from tests.support.agenda_clinica import ANA, BETO, CAU, DIVA, fonte_clinica
from tests.support.agenda_semanal import (
    DOMINGO,
    QUARTA,
    QUINTA,
    SABADO,
    SEGUNDA,
    SEMANA,
    SEXTA,
    TERCA,
    grade,
    horas_da_manha,
)
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

PASTA = Path(__file__).resolve().parent.parent / "ai" / "golden"
ARQUIVO_SAIDAS = PASTA / "tools_antigas.json"
ARQUIVO_CONTRATO = PASTA / "contrato_tools.json"

#: As tools reescritas sobre o motor novo.
TOOLS_REESCRITAS = (
    "consultar_disponibilidade",
    "consultar_ocupacao",
    "consultar_ocupacao_profissional",
    "consultar_pacientes_por_profissional",
)

#: "Hoje" das conversas do golden (padrão de `data` nas tools semanais).
DATA_REFERENCIA = QUARTA

EVA = Profissional(id="eva-nunes", nome="Eva Nunes", especialidade=Especialidade.MUSICOTERAPIA)
ANA_SOUZA = Profissional(
    id="ana-souza", nome="Ana Souza", especialidade=Especialidade.PSICOPEDAGOGIA
)


def _atendimento(
    dia: date,
    profissional: Profissional,
    inicio: time,
    quantidade: int,
    sala_id: str,
    indice_posto: int,
    paciente_id: str,
) -> Atendimento:
    slots = Slot.slots_do_dia(dia)
    primeiro = next(i for i, slot in enumerate(slots) if slot.hora_inicio == inicio)
    return Atendimento(
        id=f"borda-{dia.isoformat()}-{profissional.id}-{inicio:%H%M}-{indice_posto}",
        paciente_ids=[paciente_id],
        profissional_id=profissional.id,
        sala_id=sala_id,
        especialidade=profissional.especialidade,
        slots=slots[primeiro : primeiro + quantidade],
        indice_posto=indice_posto,
    )


def fonte_borda() -> FakeScheduleDataSource:
    """A semana fictícia com os casos de borda descritos no módulo."""
    fonte = fonte_clinica()
    # Segunda: Ana atende às 14:00, mas só está escalada de manhã (fora da grade);
    # Beto atende no posto 1 da Sala 2, mas está escalado no posto 0;
    # Eva está listada sem grade nenhuma (especialidade 0/0).
    fonte.atendimentos[SEGUNDA] += [
        _atendimento(SEGUNDA, ANA, time(14, 0), 2, "sala-1", 0, "paciente-ficticio-alfa"),
        _atendimento(SEGUNDA, BETO, time(10, 0), 1, "sala-2", 1, "paciente-ficticio-bravo"),
    ]
    fonte.profissionais[SEGUNDA] = [*fonte.profissionais[SEGUNDA], EVA]
    # Terça: Beto também no posto 1 da Sala 2 de manhã, ao mesmo tempo que no posto 0.
    fonte.grade[TERCA] += grade(TERCA, horas_da_manha(TERCA)[:3], BETO, "sala-2", 1)
    # Terça: a Sala 2 some de `listar_salas` (o nome dela só existe nos outros dias).
    fonte.salas[TERCA] = [sala for sala in fonte.salas[TERCA] if sala.id != "sala-2"]
    # Sexta: Cau tem grade e atendimento, mas some de `listar_profissionais`.
    fonte.profissionais[SEXTA] = [p for p in fonte.profissionais[SEXTA] if p.id != CAU.id]
    # Sábado: Diva atende a tarde inteira fora da grade (acima de 100%) e Ana
    # Souza aparece listada sem grade ("Ana" fica ambíguo).
    fonte.atendimentos[SABADO] += [
        _atendimento(SABADO, DIVA, time(13, 0), 10, "sala-10", 0, "paciente-ficticio-echo"),
    ]
    fonte.profissionais[SABADO] = [*fonte.profissionais[SABADO], ANA_SOUZA]
    return fonte


FONTES: dict[str, Callable[[], FakeScheduleDataSource]] = {
    "clinica": fonte_clinica,
    "parcial": lambda: fonte_clinica({QUARTA}),
    "ilegivel": lambda: fonte_clinica(set(SEMANA)),
    "borda": fonte_borda,
}

#: (fonte, tool, argumentos). A chave de cada caso é o índice na lista.
CASOS: list[tuple[str, str, dict[str, Any]]] = [
    # consultar_disponibilidade
    *[("clinica", "consultar_disponibilidade", {"data": dia.isoformat()}) for dia in SEMANA],
    ("clinica", "consultar_disponibilidade", {"data": DOMINGO.isoformat()}),
    (
        "clinica",
        "consultar_disponibilidade",
        {"data": SEGUNDA.isoformat(), "especialidade": "psicologia"},
    ),
    (
        "clinica",
        "consultar_disponibilidade",
        {"data": SEGUNDA.isoformat(), "profissional": "Beto Reis"},
    ),
    ("clinica", "consultar_disponibilidade", {"data": SEGUNDA.isoformat(), "profissional": "beto"}),
    (
        "clinica",
        "consultar_disponibilidade",
        {
            "data": SEGUNDA.isoformat(),
            "especialidade": "musicoterapia",
        },
    ),
    ("parcial", "consultar_disponibilidade", {"data": QUARTA.isoformat()}),
    ("parcial", "consultar_disponibilidade", {"data": QUINTA.isoformat()}),
    *[
        ("borda", "consultar_disponibilidade", {"data": dia.isoformat()})
        for dia in (SEGUNDA, TERCA, SEXTA, SABADO)
    ],
    ("borda", "consultar_disponibilidade", {"data": SEXTA.isoformat(), "profissional": "cau-melo"}),
    # consultar_ocupacao
    *[("clinica", "consultar_ocupacao", {"data": dia.isoformat()}) for dia in SEMANA],
    ("clinica", "consultar_ocupacao", {"data": DOMINGO.isoformat()}),
    ("parcial", "consultar_ocupacao", {"data": QUARTA.isoformat()}),
    ("parcial", "consultar_ocupacao", {"data": SEXTA.isoformat()}),
    *[
        ("borda", "consultar_ocupacao", {"data": dia.isoformat()})
        for dia in (SEGUNDA, TERCA, SEXTA, SABADO)
    ],
    # consultar_ocupacao_profissional
    *[
        ("clinica", "consultar_ocupacao_profissional", {"profissional": nome})
        for nome in ("Ana Lima", "beto", "Cau", "diva rocha", "ana", "Zé Ninguém")
    ],
    (
        "clinica",
        "consultar_ocupacao_profissional",
        {"profissional": "Cau", "data": DOMINGO.isoformat()},
    ),
    (
        "clinica",
        "consultar_ocupacao_profissional",
        {"profissional": "Cau", "data": "2026-10-07"},
    ),
    ("parcial", "consultar_ocupacao_profissional", {"profissional": "Cau"}),
    ("parcial", "consultar_ocupacao_profissional", {"profissional": "Ana Lima"}),
    ("ilegivel", "consultar_ocupacao_profissional", {"profissional": "Cau"}),
    *[
        ("borda", "consultar_ocupacao_profissional", {"profissional": nome})
        for nome in ("Ana", "Ana Lima", "Ana Souza", "Beto", "Cau Melo", "Diva", "Eva")
    ],
    # consultar_pacientes_por_profissional
    ("clinica", "consultar_pacientes_por_profissional", {}),
    ("clinica", "consultar_pacientes_por_profissional", {"escopo": "dia"}),
    *[
        ("clinica", "consultar_pacientes_por_profissional", {"escopo": "dia", "data": d})
        for d in (SEGUNDA.isoformat(), TERCA.isoformat(), DOMINGO.isoformat())
    ],
    ("clinica", "consultar_pacientes_por_profissional", {"profissional": "Ana Lima"}),
    (
        "clinica",
        "consultar_pacientes_por_profissional",
        {"profissional": "Ana Lima", "escopo": "dia", "data": TERCA.isoformat()},
    ),
    (
        "clinica",
        "consultar_pacientes_por_profissional",
        {"profissional": "ana", "escopo": "dia", "data": SABADO.isoformat()},
    ),
    ("clinica", "consultar_pacientes_por_profissional", {"especialidade": "psico"}),
    (
        "clinica",
        "consultar_pacientes_por_profissional",
        {"especialidade": "fono", "escopo": "dia", "data": TERCA.isoformat()},
    ),
    ("clinica", "consultar_pacientes_por_profissional", {"especialidade": "xyz"}),
    ("clinica", "consultar_pacientes_por_profissional", {"especialidade": "musicoterapia"}),
    (
        "clinica",
        "consultar_pacientes_por_profissional",
        {"profissional": "beto", "especialidade": "psicologia"},
    ),
    ("clinica", "consultar_pacientes_por_profissional", {"profissional": "Zé Ninguém"}),
    ("clinica", "consultar_pacientes_por_profissional", {"data": "2026-10-07"}),
    ("parcial", "consultar_pacientes_por_profissional", {}),
    (
        "parcial",
        "consultar_pacientes_por_profissional",
        {"escopo": "dia", "data": QUARTA.isoformat()},
    ),
    ("parcial", "consultar_pacientes_por_profissional", {"profissional": "Cau"}),
    ("ilegivel", "consultar_pacientes_por_profissional", {}),
    ("borda", "consultar_pacientes_por_profissional", {}),
    ("borda", "consultar_pacientes_por_profissional", {"profissional": "Ana"}),
    ("borda", "consultar_pacientes_por_profissional", {"profissional": "Ana Souza"}),
    ("borda", "consultar_pacientes_por_profissional", {"profissional": "Eva"}),
    ("borda", "consultar_pacientes_por_profissional", {"especialidade": "musicoterapia"}),
    (
        "borda",
        "consultar_pacientes_por_profissional",
        {"escopo": "dia", "data": SEXTA.isoformat()},
    ),
]


def criar_tools_golden(fonte: FakeScheduleDataSource) -> dict[str, BaseTool]:
    tools = criar_tools(fonte, SemHistoricoContinuidadeDataSource(), DATA_REFERENCIA)
    return {item.name: item for item in tools}


def executar_caso(nome_fonte: str, nome_tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Chama a tool como o agente chama (tool call) e devolve texto e artefato."""
    tool = criar_tools_golden(FONTES[nome_fonte]())[nome_tool]
    mensagem = tool.invoke({"type": "tool_call", "id": "golden", "name": nome_tool, "args": args})
    assert isinstance(mensagem, ToolMessage)
    return {"texto": mensagem.content, "bloco": mensagem.artifact}


def chave_caso(indice: int) -> str:
    nome_fonte, nome_tool, args = CASOS[indice]
    return f"{indice:03d} {nome_fonte} {nome_tool} {json.dumps(args, sort_keys=True)}"


def capturar_saidas() -> dict[str, dict[str, Any]]:
    return {chave_caso(i): executar_caso(*caso) for i, caso in enumerate(CASOS)}


def capturar_contrato(tools: list[BaseTool]) -> dict[str, dict[str, Any]]:
    """O que o modelo vê de cada tool: nome, descrição e schema dos argumentos."""
    return {
        tool.name: {
            "descricao": tool.description,
            # As tools daqui são todas definidas com `args_schema` pydantic v2.
            "argumentos": cast(type[BaseModel], tool.tool_call_schema).model_json_schema(),
            "formato_resposta": tool.response_format,
        }
        for tool in tools
    }


def _gravar(caminho: Path, dados: Any) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps(dados, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> None:
    contrato = capturar_contrato(list(criar_tools_golden(fonte_clinica()).values()))
    _gravar(ARQUIVO_CONTRATO, contrato)
    _gravar(ARQUIVO_SAIDAS, capturar_saidas())
    print(f"{len(CASOS)} casos e {len(contrato)} tools gravados em {PASTA}")


if __name__ == "__main__":
    main()
