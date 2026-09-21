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
    """
    sem_acento = "".join(unicodedata.normalize("NFKD", caractere)[0] for caractere in texto)
    achatado = " ".join(sem_acento.split()).casefold()
    return "-".join(re.findall(r"[a-z0-9]+", achatado))
