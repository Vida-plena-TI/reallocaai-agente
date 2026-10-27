"""Tipos dos parâmetros de especialidade e dia, derivados do catálogo da semana.

O schema que o modelo vê traz só as especialidades (e os dias) que a planilha
mostra na semana: o modelo não consegue inventar valor. Sinônimos que a
equipe usa ("fono", "psico", "TO") são aceitos antes da validação. Sem
catálogo (leitura falhou ou semana vazia), valem as listas de reserva
(`ESPECIALIDADES_RESERVA` e os seis dias), e o chat segue funcionando.
"""

from collections.abc import Callable, Sequence
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BeforeValidator, ValidationError

from app.domain import ESPECIALIDADES_RESERVA, Especialidade, reconhecer_especialidade
from app.domain.catalogo_agenda import NOMES_DOS_DIAS, CatalogoAgenda
from app.domain.formatacao import rotulo_enum


def especialidades_validas(catalogo: CatalogoAgenda | None) -> tuple[Especialidade, ...]:
    if catalogo is not None and catalogo.especialidades:
        return tuple(catalogo.especialidades)
    return ESPECIALIDADES_RESERVA


def dias_validos(catalogo: CatalogoAgenda | None) -> tuple[str, ...]:
    if catalogo is not None and catalogo.dias_com_agenda:
        return tuple(NOMES_DOS_DIAS[dia.weekday()] for dia in catalogo.dias_com_agenda)
    return NOMES_DOS_DIAS


def _reconhecer(valor: Any) -> Any:
    if isinstance(valor, str) and not isinstance(valor, StrEnum):
        especialidade = reconhecer_especialidade(valor)
        if especialidade is not None:
            return especialidade.value
    return valor


def tipo_especialidade(validas: Sequence[Especialidade]) -> Any:
    """Enum só com `validas` (nome `Especialidade` no schema), aceitando sinônimos."""
    enum = StrEnum("Especialidade", {e.value: e.value for e in validas})  # type: ignore[misc]
    return Annotated[enum, BeforeValidator(_reconhecer)]


def tipo_dia(validos: Sequence[str]) -> Any:
    """Enum de dia da semana (`segunda` ... `sabado`), só com os dias com agenda."""
    return StrEnum("DiaDaSemana", {dia: dia for dia in validos})


def como_especialidade(valor: Any) -> Especialidade:
    """O valor do enum dinâmico como `Especialidade` do domínio."""
    return Especialidade(str(valor))


def mensagem_de_parametro_invalido(
    validas: Sequence[Especialidade], dias: Sequence[str] | None = None
) -> Callable[[Any], str]:
    """Texto para o modelo quando ele manda valor fora do schema (sem repetir o valor)."""

    def mensagem(erro: ValidationError) -> str:
        campos = sorted({".".join(str(p) for p in item["loc"]) for item in erro.errors()})
        texto = (
            f"Parâmetros inválidos ({', '.join(campos)}). Especialidades nesta semana: "
            f"{', '.join(rotulo_enum(e.value) for e in validas)}."
        )
        if dias is not None:
            texto += f" Dias com agenda: {', '.join(dias)}."
        return texto + " Corrija e chame de novo."

    return mensagem
