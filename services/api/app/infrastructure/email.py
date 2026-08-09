"""SMTP email adapter."""

import asyncio
from email.message import EmailMessage
import smtplib


class SmtpEmailSender:
    def __init__(
        self, *, host: str, port: int, sender: str,
        username: str | None = None, password: str | None = None,
        starttls: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._sender = sender
        self._username = username
        self._password = password
        self._starttls = starttls

    async def send(self, *, recipient: str, subject: str, text: str) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(text)
        await asyncio.to_thread(self._send, message)

    def _send(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=8) as client:
            if self._starttls:
                client.starttls()
            if self._username is not None:
                client.login(self._username, self._password or "")
            client.send_message(message)
