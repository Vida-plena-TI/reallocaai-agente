"""Mantém desligados o debug/verbose do LangChain e o tracing do LangSmith.

Qualquer um deles enviaria as conversas (com nomes de pacientes) para a saída
padrão ou para um serviço de terceiros. Basta uma variável como
`LANGCHAIN_TRACING_V2=true` esquecida no painel de deploy para o tracing ligar
sozinho, por isso `desligar_rastreamento_externo` não confia no ambiente:
apaga essas variáveis e força o desligamento pela API das bibliotecas.

API usada (inspecionada em `langsmith==0.12.4`/`langchain-core==1.6.2`
instalados): `langsmith.configure(enabled=False)` grava um valor global que
`langsmith.utils.tracing_is_enabled` consulta antes do ambiente;
`langsmith.utils.get_env_var` é cacheada com `lru_cache`, daí o `cache_clear`.
"""

import os
from typing import Final

import langsmith
import langsmith.utils
from langchain_core.globals import set_debug, set_verbose

#: Variáveis que ligam o tracing (`LANGCHAIN_*`/`LANGSMITH_*`) ou um handler v1.
VARIAVEIS_DE_RASTREAMENTO: Final[tuple[str, ...]] = (
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGSMITH_TRACING",
    "LANGCHAIN_HANDLER",
)


def desligar_rastreamento_externo() -> None:
    """Desliga debug, verbose e tracing do LangChain/LangSmith. Idempotente."""
    for nome in VARIAVEIS_DE_RASTREAMENTO:
        os.environ.pop(nome, None)
    # `get_env_var` é sobrecarregada (`@overload`) sobre um `lru_cache`: o mypy
    # só enxerga as sobrecargas, não o `cache_clear` do objeto real.
    langsmith.utils.get_env_var.cache_clear()  # type: ignore[attr-defined]
    langsmith.configure(enabled=False)
    set_debug(False)
    set_verbose(False)
