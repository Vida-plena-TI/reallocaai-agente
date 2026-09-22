"""Envio do relatório de ocupação por e-mail via Resend (Fase 7).

API do Resend usada (inspecionada em `resend==2.44.x` instalado, não
assumida de memória): a credencial é atribuída ao atributo de módulo
`resend.api_key` (não a uma instância de cliente) e o envio é
`resend.Emails.send({...})`, aceitando um dict com `from`/`to`/`subject`/
`html`/`text`. Qualquer falha do lado do Resend (rede, autenticação,
validação) levanta uma subclasse de `resend.exceptions.ResendError`.
"""

import logging
from datetime import date

import resend

from app.config import exigir, get_settings
from app.data_sources.base import ScheduleDataSource
from app.engine.ocupacao import construir_relatorio_ocupacao_do_dia
from app.reports.exceptions import ReportsEnvioError
from app.reports.template import renderizar_relatorio

logger = logging.getLogger(__name__)


def enviar_relatorio_por_email(
    fonte: ScheduleDataSource, data: date, destinatarios: list[str] | None = None
) -> None:
    """Monta o relatório de ocupação de `data` e envia por e-mail via Resend.

    `destinatarios=None` usa `settings.report_email_to`; uma lista explícita
    substitui esse padrão. `resend_api_key`/`report_email_from` são validados
    só aqui, no momento do envio — nunca em `Settings` — para nenhum teste
    automatizado precisar dessas credenciais (mesmo padrão já aplicado a
    `OPENAI_API_KEY` e às credenciais do Google Sheets; ver `exigir`).
    """
    settings = get_settings()
    api_key = exigir(settings.resend_api_key, "RESEND_API_KEY")
    remetente = exigir(settings.report_email_from, "REPORT_EMAIL_FROM")
    destinatarios_efetivos = (
        destinatarios if destinatarios is not None else settings.report_email_to
    )

    relatorio = construir_relatorio_ocupacao_do_dia(fonte, data)
    assunto, corpo_html, corpo_texto = renderizar_relatorio(relatorio)

    resend.api_key = api_key
    try:
        resend.Emails.send(
            {
                "from": remetente,
                "to": destinatarios_efetivos,
                "subject": assunto,
                "html": corpo_html,
                "text": corpo_texto,
            }
        )
    except Exception as erro:
        logger.error(
            "Falha ao enviar relatório de ocupação de %s por e-mail para %r: %s",
            data.isoformat(),
            destinatarios_efetivos,
            erro,
        )
        raise ReportsEnvioError(
            f"Falha ao enviar relatório de ocupação de {data.isoformat()} por e-mail."
        ) from erro
