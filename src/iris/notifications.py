from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from iris.config import IrisConfig


@dataclass(frozen=True)
class NotificationResult:
    ok: bool
    detail: str
    status_code: int | None = None


class NotificationService:
    def __init__(self, config: IrisConfig) -> None:
        self.config = config

    def send_phone_push(
        self,
        message: str,
        *,
        title: str | None = None,
        priority: int = 3,
        tags: str | None = None,
    ) -> NotificationResult:
        if self.config.notify_provider == "pushover":
            return self._send_pushover(
                message=message,
                title=title or self.config.agent_name,
                priority=priority,
            )
        if self.config.notify_provider != "ntfy":
            return NotificationResult(
                False,
                f"unsupported notification provider: {self.config.notify_provider}",
            )
        if not self.config.ntfy_topic:
            return NotificationResult(
                False,
                "IRIS_NTFY_TOPIC is missing. Add an unguessable ntfy topic in .env.",
            )
        return self._send_ntfy(
            message=message,
            title=title or self.config.agent_name,
            priority=priority,
            tags=tags,
        )

    def _send_ntfy(
        self,
        *,
        message: str,
        title: str,
        priority: int,
        tags: str | None,
    ) -> NotificationResult:
        topic = quote(self.config.ntfy_topic or "", safe="")
        url = f"{self.config.ntfy_server.rstrip('/')}/{topic}"
        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "Title": title,
            "Priority": str(max(1, min(priority, 5))),
            "Cache": "no",
        }
        if tags:
            headers["Tags"] = tags
        if self.config.ntfy_token:
            headers["Authorization"] = f"Bearer {self.config.ntfy_token}"

        request = Request(
            url,
            data=message.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(
                request, timeout=self.config.notify_timeout_seconds
            ) as response:
                status_code = getattr(response, "status", None)
                ok = status_code is None or 200 <= int(status_code) < 300
                detail = "phone notification sent" if ok else "ntfy rejected message"
                return NotificationResult(ok, detail, status_code)
        except HTTPError as exc:
            return NotificationResult(False, f"ntfy HTTP error: {exc.code}", exc.code)
        except URLError as exc:
            return NotificationResult(False, f"ntfy network error: {exc.reason}")
        except TimeoutError:
            return NotificationResult(False, "ntfy request timed out")

    def _send_pushover(
        self,
        *,
        message: str,
        title: str,
        priority: int,
    ) -> NotificationResult:
        if not self.config.pushover_token or not self.config.pushover_user:
            return NotificationResult(
                False,
                "Pushover is missing. Set PUSHOVER_TOKEN and PUSHOVER_USER in .env.",
            )
        body = urlencode(
            {
                "token": self.config.pushover_token,
                "user": self.config.pushover_user,
                "title": title,
                "message": message,
                "priority": 1 if priority >= 4 else 0,
            }
        ).encode("utf-8")
        request = Request(
            "https://api.pushover.net/1/messages.json",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(
                request, timeout=self.config.notify_timeout_seconds
            ) as response:
                status_code = getattr(response, "status", None)
                ok = status_code is None or 200 <= int(status_code) < 300
                detail = (
                    "phone notification sent" if ok else "Pushover rejected message"
                )
                return NotificationResult(ok, detail, status_code)
        except HTTPError as exc:
            return NotificationResult(
                False, f"Pushover HTTP error: {exc.code}", exc.code
            )
        except URLError as exc:
            return NotificationResult(False, f"Pushover network error: {exc.reason}")
        except TimeoutError:
            return NotificationResult(False, "Pushover request timed out")
