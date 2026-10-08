"""Servidor de produção: `python -m app`.

Lê a porta de `PORT` (padrão 8000) e escuta em 0.0.0.0. Valida a configuração
antes de subir o uvicorn, para um deploy mal configurado sair com a lista do
que falta (código 1) em vez de um traceback.
"""

import sys

import uvicorn

from app.config import ConfiguracaoInvalidaError, get_settings, validar_configuracao

#: Abaixo de 10 s, o `docker stop` padrão (SIGTERM, depois SIGKILL em 10 s).
_TEMPO_PARA_ENCERRAR_SEGUNDOS = 8


def main() -> None:
    settings = get_settings()
    try:
        validar_configuracao(settings)
    except ConfiguracaoInvalidaError as erro:
        print(erro, file=sys.stderr)
        sys.exit(1)

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # dentro do container, atrás do Traefik
        port=settings.port,
        # Um único worker de propósito: as conversas (TTL 4 h) e o cache da
        # agenda (TTL 60 s) vivem na memória do processo. Com mais de um
        # worker, cada requisição de uma mesma conversa poderia cair num
        # processo diferente, que não conhece o `conversa_id` (404).
        workers=1,
        # Atrás do Traefik: usa X-Forwarded-For/Proto para IP do cliente e
        # esquema. As origens confiáveis vêm de FORWARDED_ALLOW_IPS (o uvicorn
        # lê essa variável; o Dockerfile define "*").
        proxy_headers=True,
        log_level=settings.log_level.lower(),
        timeout_graceful_shutdown=_TEMPO_PARA_ENCERRAR_SEGUNDOS,
        server_header=False,
    )


if __name__ == "__main__":
    main()
