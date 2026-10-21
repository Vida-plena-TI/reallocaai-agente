"""Envio do relatório de ocupação por e-mail via Resend (Fase 7).

API do Resend usada (inspecionada em `resend==2.44.x` instalado, não
assumida de memória): a credencial é atribuída ao atributo de módulo
`resend.api_key` (não a uma instância de cliente) e o envio é
`resend.Emails.send({...})`, aceitando um dict com `from`/`to`/`subject`/
`html`/`text`. Qualquer falha do lado do Resend (rede, autenticação,
validação) levanta uma subclasse de `resend.exceptions.ResendError`.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import resend

from app.config import exigir, get_settings
from app.domain import ScheduleDataSource
from app.engine.fotografia import fotografar_periodo
from app.engine.ocupacao import construir_relatorio_ocupacao_do_dia
from app.reports.exceptions import ReportsEnvioError
from app.reports.relatorios_gerais import renderizar_relatorios_gerais
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
    envio = _Envio.configurado(destinatarios)
    relatorio = construir_relatorio_ocupacao_do_dia(fonte, data)
    assunto, corpo_html, corpo_texto = renderizar_relatorio(relatorio)
    envio.enviar(
        assunto, corpo_html, corpo_texto, contexto=f"relatório de ocupação de {data.isoformat()}"
    )


def enviar_relatorios_gerais_por_email(
    fonte: ScheduleDataSource,
    relatorios: Sequence[str],
    inicio: date,
    fim: date,
    destinatarios: list[str] | None = None,
) -> None:
    """Lê o período uma vez e envia um e-mail com `relatorios` (já validados), na ordem.

    O período e os tipos são validados antes (`validar_tipos` e
    `resolver_periodo`, em `app.reports.relatorios_gerais`): aqui nunca chega
    `agenda_paciente`. Uma falha de leitura da agenda vira `ReportsEnvioError`,
    sem a mensagem original (que pode repetir dados da planilha).
    """
    envio = _Envio.configurado(destinatarios)
    try:
        foto = fotografar_periodo(fonte, inicio, fim)
    except Exception as erro:
        logger.error(
            "Falha ao ler a agenda de %s a %s para o e-mail de relatórios: %s",
            inicio.isoformat(),
            fim.isoformat(),
            type(erro).__name__,
        )
        raise ReportsEnvioError("Falha ao ler a agenda para o e-mail de relatórios.") from erro
    assunto, corpo_html, corpo_texto = renderizar_relatorios_gerais(foto, relatorios)
    envio.enviar(
        assunto,
        corpo_html,
        corpo_texto,
        contexto=f"relatórios ({', '.join(relatorios)}) de {inicio.isoformat()} a "
        f"{fim.isoformat()}",
    )


@dataclass(frozen=True)
class _Envio:
    """Credenciais e destinatários já resolvidos, antes de qualquer leitura da agenda."""

    api_key: str
    remetente: str
    destinatarios: list[str]

    @classmethod
    def configurado(cls, destinatarios: list[str] | None) -> _Envio:
        settings = get_settings()
        return cls(
            api_key=exigir(settings.resend_api_key, "RESEND_API_KEY"),
            remetente=exigir(settings.report_email_from, "REPORT_EMAIL_FROM"),
            destinatarios=destinatarios if destinatarios is not None else settings.report_email_to,
        )

    def enviar(self, assunto: str, corpo_html: str, corpo_texto: str, *, contexto: str) -> None:
        """Envia pelo Resend. `contexto` só descreve o e-mail no log (tipo e datas)."""
        resend.api_key = self.api_key
        try:
            resend.Emails.send(
                {
                    "from": self.remetente,
                    "to": self.destinatarios,
                    "subject": assunto,
                    "html": corpo_html,
                    "text": corpo_texto,
                }
            )
        except Exception as erro:
            # Sem endereços nem a mensagem do Resend, que pode repeti-los.
            logger.error(
                "Falha ao enviar %s por e-mail para %d destinatário(s): %s",
                contexto,
                len(self.destinatarios),
                type(erro).__name__,
            )
            raise ReportsEnvioError(f"Falha ao enviar {contexto} por e-mail.") from erro
