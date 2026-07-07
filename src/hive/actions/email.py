"""Email send path (PRD build item: "first real send path", L1).

Design invariants:
- **The send fires at the gate, not inside an agent.** No agent ever holds a
  send tool; approval resolution (or an earned-L2 auto path with audit) is
  what executes the email. A model cannot be prompt-injected into sending —
  there is no send capability in any prompt loop.
- **Backends mirror the model tiers**: "outbox" (default, $0, no credentials)
  writes each approved email as a real .eml file to <data_dir>/outbox — you
  can open it in a mail client and send it yourself. "smtp" delivers for real
  through any provider (stdlib smtplib; a free Gmail app password works).
"""

from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from hive.config import HiveConfig


class EmailSender(Protocol):
    name: str

    def send(self, to: str, subject: str, body: str, job_id: str) -> str: ...


def _build_message(from_addr: str, to: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = from_addr
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return message


class OutboxEmailSender:
    """$0 backend: approved emails land as .eml files in the outbox dir."""

    name = "outbox"

    def __init__(self, outbox_dir: str | Path, from_addr: str = "hive@localhost") -> None:
        self.outbox = Path(outbox_dir)
        self.outbox.mkdir(parents=True, exist_ok=True)
        self.from_addr = from_addr

    def send(self, to: str, subject: str, body: str, job_id: str) -> str:
        message = _build_message(self.from_addr, to, subject, body)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        path = self.outbox / f"{stamp}-{job_id}.eml"
        counter = 0
        while path.exists():
            counter += 1
            path = self.outbox / f"{stamp}-{counter}-{job_id}.eml"
        path.write_bytes(message.as_bytes())
        return f"outbox: {path}"


class SmtpEmailSender:
    """Real delivery over SMTP + STARTTLS. Works with any provider."""

    name = "smtp"

    def __init__(self, host: str, port: int, user: str, password: str,
                 from_addr: str, timeout: float = 30.0) -> None:
        self.host, self.port = host, port
        self.user, self.password = user, password
        self.from_addr, self.timeout = from_addr, timeout

    def send(self, to: str, subject: str, body: str, job_id: str) -> str:
        message = _build_message(self.from_addr, to, subject, body)
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            smtp.starttls()
            smtp.login(self.user, self.password)
            smtp.send_message(message)
        return f"sent to {to} via {self.host}"


def make_email_sender(config: "HiveConfig") -> EmailSender:
    if config.email_backend == "smtp":
        missing = [name for name, value in (
            ("HIVE_SMTP_HOST", config.smtp_host),
            ("HIVE_SMTP_USER", config.smtp_user),
            ("HIVE_SMTP_PASSWORD", config.smtp_password),
        ) if not value]
        if missing:
            raise ValueError(f"email_backend=smtp but missing: {', '.join(missing)}")
        return SmtpEmailSender(
            host=config.smtp_host, port=config.smtp_port,
            user=config.smtp_user, password=config.smtp_password,
            from_addr=config.email_from,
        )
    return OutboxEmailSender(config.outbox_dir, from_addr=config.email_from)
