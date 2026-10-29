"""Plano de saúde pela cor do texto do nome do paciente.

Regra do relatório `pacientes_por_plano`: o plano é a cor do TEXTO (não do
fundo) e só os trechos coloridos contam como nome. Numa célula "Nome (falta)"
com o nome azul e a observação preta, vale o trecho do nome: o paciente é
SulAmérica e a observação sai do nome. Texto todo preto é ignorado, por
decisão da clínica; cor que não é preta e não cai em nenhuma faixa de
`PLANO_POR_COR` é "não reconhecida".
"""

import colorsys
from collections import Counter
from typing import Final, NamedTuple

from app.domain.constants import (
    BRILHO_MAXIMO_CINZA_ESCURO,
    BRILHO_MAXIMO_PRETO,
    PLANO_POR_COR,
    SATURACAO_MAXIMA_CINZA_ESCURO,
    SATURACAO_MINIMA_PLANO,
    FaixaDePlano,
)
from app.domain.entities import NomeColorido

#: Pontuação que sobra nas bordas do nome depois de tirar a observação
#: (a mesma do parser: "Ana (" vira "Ana").
_BORDAS: Final[str] = " ()[].,;:-_|"


class LeituraDeNome(NamedTuple):
    """O nome como o relatório o enxerga.

    `plano` é o rótulo de `PLANO_POR_COR`, ou `None` quando o nome é preto
    (`preto=True`) ou tem cor não reconhecida (`preto=False`).
    """

    nome: str
    plano: str | None
    preto: bool


def _hsv(cor: str) -> tuple[float, float, float]:
    """Matiz em graus (0 a 360), saturação e brilho (0 a 1) de `#RRGGBB`."""
    vermelho, verde, azul = (int(cor[indice : indice + 2], 16) / 255 for indice in (1, 3, 5))
    matiz, saturacao, brilho = colorsys.rgb_to_hsv(vermelho, verde, azul)
    return matiz * 360, saturacao, brilho


def e_preto(cor: str) -> bool:
    """Texto padrão ou tom muito escuro (ver `BRILHO_MAXIMO_PRETO`)."""
    _, saturacao, brilho = _hsv(cor)
    return brilho < BRILHO_MAXIMO_PRETO or (
        brilho < BRILHO_MAXIMO_CINZA_ESCURO and saturacao < SATURACAO_MAXIMA_CINZA_ESCURO
    )


def _na_faixa(matiz: float, faixa: FaixaDePlano) -> bool:
    if faixa.inicio <= faixa.fim:
        return faixa.inicio <= matiz <= faixa.fim
    return matiz >= faixa.inicio or matiz <= faixa.fim


def plano_da_cor(cor: str) -> str | None:
    """Rótulo do plano de uma cor que não é preta; `None` quando não reconhecida."""
    matiz, saturacao, _ = _hsv(cor)
    if saturacao < SATURACAO_MINIMA_PLANO:
        return None
    return next((faixa.rotulo for faixa in PLANO_POR_COR if _na_faixa(matiz, faixa)), None)


def ler_nome_colorido(nome: NomeColorido) -> LeituraDeNome:
    """Nome e plano de um `NomeColorido`, pela regra do trecho do nome.

    - Sem nenhuma letra colorida (não preta): é preto; o nome é o texto inteiro.
    - Com letra colorida: o nome é só o texto colorido (os espaços dos trechos
      pretos ficam, para não colar palavras), e o plano é o da cor com mais
      letras; empate fica com a que aparece primeiro. Se essa cor não cair em
      nenhuma faixa, o nome é de cor não reconhecida.
    """
    letras_por_cor: Counter[str] = Counter()
    for trecho in nome.trechos:
        if not e_preto(trecho.cor):
            letras_por_cor[trecho.cor] += sum(caractere.isalpha() for caractere in trecho.texto)
    if not +letras_por_cor:
        texto = "".join(trecho.texto for trecho in nome.trechos)
        return LeituraDeNome(nome=_limpar(texto), plano=None, preto=True)

    texto = "".join(
        trecho.texto
        if not e_preto(trecho.cor)
        else "".join(caractere for caractere in trecho.texto if caractere.isspace())
        for trecho in nome.trechos
    )
    # `most_common` preserva a ordem de inserção nos empates.
    cor, _ = letras_por_cor.most_common(1)[0]
    return LeituraDeNome(nome=_limpar(texto), plano=plano_da_cor(cor), preto=False)


def _limpar(texto: str) -> str:
    return " ".join(texto.split()).strip(_BORDAS).strip()
