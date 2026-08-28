"""Production alert notifications via email (SMTP).

Integrates automatically with structlog: every logger.error() and
logger.exception() fires an email alert, debounced per event type
to prevent notification storms (default: 5 min cooldown).

Add to your .env:
    MAIL_HOST=mail.sofopolis.com
    MAIL_PORT=465
    MAIL_USERNAME=jmogro@sofopolis.com
    MAIL_PASSWORD=<password>
    MAIL_FROM_ADDRESS=jmogro@sofopolis.com
    MAIL_TO_ADDRESS=jmogro@sofopolis.com   # recipient for alerts
    NOTIFICATION_COOLDOWN_SECONDS=300
"""

import asyncio
import logging
import smtplib
import ssl
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

from app.core.config import settings

# Use stdlib logger to avoid circular import with app.core.logging
_log = logging.getLogger(__name__)

# Per-event-type cooldown - prevents duplicate alerts for repeated errors
_last_alerted: dict[str, float] = {}


def _build_email(subject: str, body_html: str, body_text: str) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.MAIL_FROM_ADDRESS
    msg["To"] = settings.MAIL_TO_ADDRESS
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    return msg


def _smtp_send_ssl(msg: MIMEMultipart, context: ssl.SSLContext, port: int) -> None:
    """Deliver over implicit-SSL SMTP (typically port 465)."""
    with smtplib.SMTP_SSL(
        settings.MAIL_HOST, port, context=context, timeout=settings.MAIL_TIMEOUT_SECONDS
    ) as server:
        server.login(settings.MAIL_USERNAME, settings.MAIL_PASSWORD)
        server.sendmail(settings.MAIL_FROM_ADDRESS, settings.MAIL_TO_ADDRESS, msg.as_string())


def _smtp_send_starttls(msg: MIMEMultipart, context: ssl.SSLContext, port: int) -> None:
    """Deliver over the submission port with STARTTLS (typically port 587)."""
    with smtplib.SMTP(settings.MAIL_HOST, port, timeout=settings.MAIL_TIMEOUT_SECONDS) as server:
        server.ehlo()
        server.starttls(context=context)
        server.login(settings.MAIL_USERNAME, settings.MAIL_PASSWORD)
        server.sendmail(settings.MAIL_FROM_ADDRESS, settings.MAIL_TO_ADDRESS, msg.as_string())


def _smtp_send(msg: MIMEMultipart) -> None:
    """Send email synchronously via SMTP. Runs in a thread pool (fire-and-forget).

    Short, configurable timeout: many PaaS (Railway) block outbound SMTP, so the connect would
    otherwise hang a pool thread. This alert is best-effort and never on the request path, so failing
    fast is fine.

    Hardening: try implicit-SSL on MAIL_PORT (465) first; if that connect fails - the exact symptom
    when a host blocks 465 - retry with STARTTLS on the submission port (587), which some of those
    hosts still allow. If both fail, raise so the caller logs `alert_email_failed`.

    Caveat: if the host blocks ALL outbound SMTP (465 and 587), no amount of SMTP hardening delivers
    the alert - moving to an HTTPS-based transport is the only real fix. That was left out per scope.
    """
    context = ssl.create_default_context()
    try:
        _smtp_send_ssl(msg, context, settings.MAIL_PORT)
        return
    except (OSError, smtplib.SMTPException) as e:
        if settings.MAIL_STARTTLS_PORT == settings.MAIL_PORT:
            raise
        _log.warning(
            "alert_smtp_ssl_failed port=%s error=%s - falling back to starttls port=%s",
            settings.MAIL_PORT, e, settings.MAIL_STARTTLS_PORT,
        )
    _smtp_send_starttls(msg, context, settings.MAIL_STARTTLS_PORT)


async def _send_email_alert(
    event: str,
    error: str,
    wa_id: Optional[str],
    extra: Optional[dict[str, Any]],
) -> None:
    """Build and send the alert email. Never raises - all errors are logged."""
    if not _is_email_configured():
        return
    try:
        env = settings.ENVIRONMENT.value
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        wa_row = f"<tr><td><b>wa_id</b></td><td><code>{wa_id}</code></td></tr>" if wa_id else ""
        err_row = f"<tr><td><b>Error</b></td><td><code>{error[:800]}</code></td></tr>" if error else ""
        extra_rows = "".join(
            f"<tr><td><b>{k}</b></td><td><code>{v}</code></td></tr>"
            for k, v in (extra or {}).items()
        )

        body_html = f"""
        <html><body style="font-family: monospace;">
        <h2 style="color:#c0392b;">🚨 Error en {settings.PROJECT_NAME} [{env}]</h2>
        <table border="1" cellpadding="6" cellspacing="0">
          <tr><td><b>Event</b></td><td><code>{event}</code></td></tr>
          <tr><td><b>Timestamp</b></td><td>{ts}</td></tr>
          <tr><td><b>Environment</b></td><td>{env}</td></tr>
          {wa_row}{err_row}{extra_rows}
        </table>
        </body></html>
        """
        body_text = (
            f"[{env}] {settings.PROJECT_NAME} ERROR\n"
            f"Event: {event}\n"
            f"Time: {ts}\n"
            + (f"wa_id: {wa_id}\n" if wa_id else "")
            + (f"Error: {error[:800]}\n" if error else "")
        )

        subject = f"[{env.upper()}] {settings.PROJECT_NAME} - {event}"
        msg = _build_email(subject, body_html, body_text)
        await asyncio.to_thread(_smtp_send, msg)
        _log.debug("alert_email_sent: %s", event)
    except Exception as e:
        _log.warning("alert_email_failed event=%s error=%s", event, e)


def _is_email_configured() -> bool:
    return bool(
        settings.MAIL_HOST
        and settings.MAIL_USERNAME
        and settings.MAIL_PASSWORD
        and settings.MAIL_TO_ADDRESS
    )


def notify(
    event: str,
    error: str = "",
    wa_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Fire-and-forget email alert with per-event debounce.

    Safe to call from sync or async code. Silently skipped when:
    - Email is not configured (MAIL_HOST not set)
    - The same event was already alerted within NOTIFICATION_COOLDOWN_SECONDS
    - There is no running asyncio event loop
    """
    if not _is_email_configured():
        return

    now = time.monotonic()
    if now - _last_alerted.get(event, 0) < settings.NOTIFICATION_COOLDOWN_SECONDS:
        return
    _last_alerted[event] = now

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_send_email_alert(event, error, wa_id, extra))
    except RuntimeError:
        pass  # no running event loop during startup - skip safely


def alert_processor(
    _logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Structlog processor: fires an email alert on every error/exception log.

    Register this in get_structlog_processors() just before the renderer.
    All logger.error() and logger.exception() calls automatically trigger
    an alert - no need to add notify() calls throughout the codebase.
    """
    if method_name in ("error", "exception"):
        notify(
            event=str(event_dict.get("event", "unknown_error")),
            error=str(event_dict.get("error", "")),
            wa_id=str(event_dict["wa_id"]) if event_dict.get("wa_id") else None,
        )
    return event_dict
