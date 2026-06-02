"""Outbound email via Resend's HTTPS API.

Hand-rolled over urllib in the same style as the Pushover/ntfy notifiers, so it
needs no extra dependency. Configure with RESEND_API_KEY, IRIS_EMAIL_FROM, and
(optionally) IRIS_EMAIL_TO for the default recipient.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from iris.config import IrisConfig

_RESEND_ENDPOINT = "https://api.resend.com/emails"


@dataclass(frozen=True)
class EmailResult:
    ok: bool
    detail: str
    status_code: int | None = None


class EmailService:
    def __init__(self, config: IrisConfig) -> None:
        self.config = config

    @property
    def available(self) -> bool:
        return self.config.has_email

    def send(
        self,
        *,
        subject: str,
        body: str,
        to: str | None = None,
        timeout: float = 20.0,
    ) -> EmailResult:
        recipient = to or self.config.email_to
        if not self.available:
            return EmailResult(
                False,
                "Email is not configured (set RESEND_API_KEY and IRIS_EMAIL_FROM).",
            )
        if not recipient:
            return EmailResult(
                False, "No recipient (pass a 'to' or set IRIS_EMAIL_TO)."
            )
        if self.config.email_provider != "resend":
            return EmailResult(
                False,
                f"Email provider '{self.config.email_provider}' is not supported.",
            )
        payload = {
            "from": self.config.email_from,
            "to": [recipient],
            "subject": subject or "(no subject)",
            "text": body or "",
        }
        request = Request(
            _RESEND_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.resend_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                status_code = getattr(response, "status", None)
                ok = status_code is None or 200 <= int(status_code) < 300
                return EmailResult(
                    ok,
                    f"email sent to {recipient}"
                    if ok
                    else "Resend rejected the message",
                    status_code,
                )
        except HTTPError as exc:
            return EmailResult(False, f"Resend HTTP error: {exc.code}", exc.code)
        except URLError as exc:
            return EmailResult(False, f"Resend network error: {exc.reason}")
        except TimeoutError:
            return EmailResult(False, "Resend request timed out")
