"""Normalização de texto livre em id estável, compartilhada pelo domínio.

Hoje só o parser da planilha gera ids assim (nome de paciente, de profissional).
A Fase 5a passa a reaproveitar a mesma função para casar o nome citado numa
conversa com o paciente já conhecido da agenda (`buscar_paciente`).
"""

import re
import unicodedata


def normalizar_id(texto: str) -> str:
    """Id estável a partir de um texto livre (`Maria Yasmin (online)` -> `maria-yasmin-online`).

    Remove acento e caixa, e junta os trechos alfanuméricos com hífen —
    pontuação, parênteses e espaço viram apenas separador.

    A decomposição (NFKD) é aplicada ao texto inteiro e as marcas combinantes
    (categoria `Mn`) são descartadas depois: assim o texto já decomposto (NFD,
    ex.: `"Joa\\u0303o"`) gera o mesmo id do texto composto (NFC, `"João"`), em
    vez de o acento solto virar separador (`joa-o`).
    """
    decomposto = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(
        caractere for caractere in decomposto if unicodedata.category(caractere) != "Mn"
    )
    return "-".join(re.findall(r"[a-z0-9]+", sem_acento.casefold()))
