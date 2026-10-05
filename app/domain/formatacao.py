"""Exibição comum ao texto das tools, aos destaques dos relatórios e ao e-mail."""

from decimal import ROUND_HALF_UP, Decimal


def formatar_razao(soma: int, quantidade: int, *, percentual: bool = False) -> str:
    """Arredonda só a exibição: HALF_UP, uma casa decimal e vírgula."""
    valor = Decimal(soma) / Decimal(quantidade) if quantidade else Decimal(0)
    if percentual:
        valor *= 100
    exibicao = str(valor.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)).replace(".", ",")
    return exibicao + ("%" if percentual else "")
