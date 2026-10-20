"""Exibição comum ao texto das tools, aos destaques dos relatórios e ao e-mail."""

import re
from decimal import ROUND_HALF_UP, Decimal


def formatar_razao(soma: int, quantidade: int, *, percentual: bool = False) -> str:
    """Arredonda só a exibição: HALF_UP, uma casa decimal e vírgula."""
    valor = Decimal(soma) / Decimal(quantidade) if quantidade else Decimal(0)
    if percentual:
        valor *= 100
    exibicao = str(valor.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)).replace(".", ",")
    return exibicao + ("%" if percentual else "")


def chave_natural(texto: str) -> tuple[str | int, ...]:
    """`Sala 10` -> `("sala ", 10, "")`: números comparados como números (Sala 2 < Sala 10)."""
    return tuple(
        int(parte) if i % 2 else parte
        for i, parte in enumerate(re.split(r"(\d+)", texto.casefold()))
    )


def rotulo_enum(valor: str) -> str:
    """`terapia_ocupacional` -> `Terapia Ocupacional` (especialidade, convênio)."""
    return valor.replace("_", " ").title()
