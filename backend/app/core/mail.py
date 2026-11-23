"""Outgoing e-mail. `smtp` backend talks to Mailpit (dev) or any SMTP server; `memory` keeps
messages in `outbox` (tests)."""

import logging
from dataclasses import dataclass
from email.message import EmailMessage

import aiosmtplib

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SentMail:
    to: str
    subject: str
    body: str


outbox: list[SentMail] = []


async def send_mail(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    if settings.mail_backend == "memory":
        outbox.append(SentMail(to=to, subject=subject, body=body))
        return

    msg = EmailMessage()
    msg["From"] = settings.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            timeout=10,
        )
    except Exception:
        # Called from a background task: the HTTP response is already sent (always 202).
        logger.exception("Failed to send e-mail (subject=%r)", subject)
