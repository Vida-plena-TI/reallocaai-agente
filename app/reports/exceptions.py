"""Exceções de `app.reports`.

Não herda de `ValueError`, pelo mesmo motivo de `app.domain.exceptions`: um
erro de envio deve chegar a quem chamou (tool, endpoint) com seu próprio
tipo, nunca confundido com uma falha de validação de entrada.
"""


class ReportsEnvioError(Exception):
    """Erro ao montar ou enviar o relatório de ocupação por e-mail."""
