from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any
from urllib import parse, request

from iris.mac_controller import ActionResult


@dataclass(frozen=True)
class BrowserTab:
    tab_id: str
    title: str
    url: str
    type: str
    web_socket_url: str

    def summary(self) -> dict[str, str]:
        return {
            "tab_id": self.tab_id,
            "title": self.title,
            "url": self.url,
            "type": self.type,
        }


class ChromeCDPBackend:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("IRIS_CHROME_CDP_URL") or "http://127.0.0.1:9222").rstrip("/")

    def available(self) -> bool:
        try:
            self._get_json("/json/version", timeout=0.5)
            return True
        except Exception:
            return False

    def list_tabs(self) -> ActionResult:
        try:
            tabs = self._tabs()
        except Exception as exc:
            return ActionResult("browser_cdp_tabs", False, _friendly_cdp_error(str(exc)))
        return ActionResult(
            "browser_cdp_tabs",
            True,
            f"Found {len(tabs)} Chrome tabs.",
            {"tabs": [tab.summary() for tab in tabs], "backend": "cdp"},
        )

    def current_page(self) -> ActionResult:
        tab = self._current_tab()
        if tab is None:
            return ActionResult("browser_cdp_current_page", False, "No Chrome CDP page tab is available.")
        return ActionResult(
            "browser_cdp_current_page",
            True,
            f"Current browser page: {tab.title or tab.url or 'unknown'}",
            {**tab.summary(), "backend": "cdp"},
        )

    def navigate(self, url: str, *, new_tab: bool = False) -> ActionResult:
        try:
            if new_tab:
                data = self._get_json(f"/json/new?{parse.quote(url, safe=':/?&=%')}", method="PUT")
                tab = _tab_from_json(data)
                return ActionResult(
                    "browser_cdp_navigate",
                    True,
                    f"Opened {url} in a new Chrome tab.",
                    {**tab.summary(), "backend": "cdp"},
                )
            tab = self._current_tab()
            if tab is None:
                return ActionResult("browser_cdp_navigate", False, "No Chrome CDP page tab is available.")
            self._send(tab.web_socket_url, "Page.enable")
            self._send(tab.web_socket_url, "Page.navigate", {"url": url})
            return ActionResult(
                "browser_cdp_navigate",
                True,
                f"Opened {url} in the current Chrome tab.",
                {"url": url, "tab_id": tab.tab_id, "backend": "cdp"},
            )
        except Exception as exc:
            return ActionResult("browser_cdp_navigate", False, _friendly_cdp_error(str(exc)))

    def get_dom(self, *, max_chars: int = 12000) -> ActionResult:
        script = f"""
(() => {{
  const root = document.querySelector('main') || document.body || document.documentElement;
  const text = (root && (root.innerText || root.textContent) || '').replace(/\\n{{3,}}/g, '\\n\\n').trim();
  const controls = [...document.querySelectorAll('button,a,input,textarea,[role="button"],[role="link"],[role="textbox"],[aria-label]')]
    .slice(0, 80)
    .map((el) => {{
      const rect = el.getBoundingClientRect();
      const label = el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.title || '';
      return {{
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute('role') || '',
        label: String(label).replace(/\\s+/g, ' ').trim().slice(0, 120),
        href: el.href || '',
        visible: rect.width > 0 && rect.height > 0,
      }};
    }})
    .filter((item) => item.visible && item.label);
  return {{
    title: document.title || '',
    url: location.href,
    text: text.slice(0, {int(max_chars)}),
    controls,
  }};
}})()
"""
        return self._evaluate_result("browser_cdp_get_dom", script, result_message="I read the browser DOM.")

    def click_text(self, text: str) -> ActionResult:
        script = """
(needle) => {
  const wanted = String(needle || '').toLowerCase();
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const labelOf = (el) => String(el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.title || '').replace(/\\s+/g, ' ').trim();
  const candidates = [...document.querySelectorAll('button,a,input,textarea,[role="button"],[role="link"],[role="textbox"],[aria-label]')].filter(visible);
  const match = candidates.find((el) => labelOf(el).toLowerCase().includes(wanted));
  if (!match) return {ok: false, reason: 'not_found', text: needle};
  match.scrollIntoView({block: 'center', inline: 'center'});
  match.click();
  return {ok: true, label: labelOf(match), tag: match.tagName.toLowerCase(), url: location.href};
}
"""
        result = self._call_function("browser_cdp_click_text", script, [text])
        if not result.ok:
            return result
        payload = result.payload if isinstance(result.payload, dict) else {}
        if payload.get("ok"):
            return ActionResult("browser_cdp_click_text", True, f"Clicked {payload.get('label') or text}.", payload)
        return ActionResult("browser_cdp_click_text", False, f"I could not find {text} in the current tab.", payload)

    def type_into(self, *, text: str, field: str = "") -> ActionResult:
        script = """
(needle, value) => {
  const wanted = String(needle || '').toLowerCase();
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const labelOf = (el) => String(el.getAttribute('aria-label') || el.getAttribute('name') || el.placeholder || el.innerText || el.title || '').replace(/\\s+/g, ' ').trim();
  let match = null;
  if (needle) {
    try { match = document.querySelector(needle); } catch (error) {}
  }
  const candidates = [...document.querySelectorAll('input,textarea,[contenteditable="true"],[role="textbox"]')].filter(visible);
  if (!match && wanted) match = candidates.find((el) => labelOf(el).toLowerCase().includes(wanted));
  if (!match) match = document.activeElement && document.activeElement !== document.body ? document.activeElement : candidates[0];
  if (!match) return {ok: false, reason: 'field_not_found', field: needle};
  match.scrollIntoView({block: 'center', inline: 'center'});
  match.focus();
  if (match.isContentEditable) {
    match.textContent = value;
  } else {
    match.value = value;
  }
  match.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: value}));
  match.dispatchEvent(new Event('change', {bubbles: true}));
  return {ok: true, field: labelOf(match), tag: match.tagName.toLowerCase(), chars: String(value).length};
}
"""
        result = self._call_function("browser_cdp_type_into", script, [field, text])
        if not result.ok:
            return result
        payload = result.payload if isinstance(result.payload, dict) else {}
        if payload.get("ok"):
            return ActionResult("browser_cdp_type_into", True, "Typed into the browser field.", payload)
        return ActionResult("browser_cdp_type_into", False, "I could not find a browser field to type into.", payload)

    def media_state(self) -> ActionResult:
        script = """
(() => {
  const media = [...document.querySelectorAll('audio,video')].map((el) => ({
    tag: el.tagName.toLowerCase(),
    paused: el.paused,
    currentTime: el.currentTime,
    duration: el.duration,
    src: el.currentSrc || el.src || '',
  }));
  const buttons = [...document.querySelectorAll('button,[role="button"]')]
    .map((el) => String(el.getAttribute('aria-label') || el.innerText || '').replace(/\\s+/g, ' ').trim())
    .filter(Boolean)
    .slice(0, 60);
  return {title: document.title || '', url: location.href, media, buttons};
})()
"""
        return self._evaluate_result("browser_cdp_media_state", script, result_message="Checked browser media state.")

    def verify_state(self, *, text: str = "", url_contains: str = "", title_contains: str = "") -> ActionResult:
        script = """
(text, urlPart, titlePart) => {
  const bodyText = (document.body && (document.body.innerText || document.body.textContent) || '').toLowerCase();
  const title = (document.title || '').toLowerCase();
  const url = (location.href || '').toLowerCase();
  const checks = {
    text: !text || bodyText.includes(String(text).toLowerCase()),
    url: !urlPart || url.includes(String(urlPart).toLowerCase()),
    title: !titlePart || title.includes(String(titlePart).toLowerCase()),
  };
  return {ok: checks.text && checks.url && checks.title, checks, title: document.title || '', url: location.href};
}
"""
        result = self._call_function("browser_cdp_verify_state", script, [text, url_contains, title_contains])
        if not result.ok:
            return result
        payload = result.payload if isinstance(result.payload, dict) else {}
        if payload.get("ok"):
            return ActionResult("browser_cdp_verify_state", True, "Browser state matches.", payload)
        return ActionResult("browser_cdp_verify_state", False, "Browser state does not match yet.", payload)

    def _evaluate_result(self, action: str, expression: str, *, result_message: str) -> ActionResult:
        try:
            payload = self.evaluate(expression)
            return ActionResult(action, True, result_message, payload)
        except Exception as exc:
            return ActionResult(action, False, _friendly_cdp_error(str(exc)))

    def _call_function(self, action: str, function_body: str, args: list[Any]) -> ActionResult:
        expression = f"({function_body})(*ARGS*)"
        encoded_args = ", ".join(json.dumps(arg) for arg in args)
        expression = expression.replace("*ARGS*", encoded_args)
        return self._evaluate_result(action, expression, result_message="Browser action completed.")

    def evaluate(self, expression: str) -> Any:
        tab = self._current_tab()
        if tab is None:
            raise RuntimeError("No Chrome CDP page tab is available")
        response = self._send(
            tab.web_socket_url,
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "userGesture": True,
            },
        )
        if "exceptionDetails" in response:
            raise RuntimeError(str(response["exceptionDetails"]))
        result = response.get("result", {}).get("result", {})
        if "value" in result:
            return result["value"]
        if result.get("type") == "undefined":
            return None
        return result.get("description") or result

    def _current_tab(self) -> BrowserTab | None:
        tabs = [tab for tab in self._tabs() if tab.type == "page" and not tab.url.startswith("devtools://")]
        return tabs[0] if tabs else None

    def _tabs(self) -> list[BrowserTab]:
        data = self._get_json("/json/list")
        if not isinstance(data, list):
            return []
        tabs: list[BrowserTab] = []
        for item in data:
            try:
                tab = _tab_from_json(item)
            except Exception:
                continue
            if tab.web_socket_url:
                tabs.append(tab)
        return tabs

    def _get_json(self, path: str, *, timeout: float = 2.0, method: str = "GET") -> Any:
        req = request.Request(f"{self.base_url}{path}", method=method)
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _send(self, ws_url: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            import websocket  # type: ignore
        except Exception as exc:  # pragma: no cover - dependency/import environment
            raise RuntimeError("websocket-client is not installed") from exc
        ws = websocket.create_connection(ws_url, timeout=4)
        try:
            message_id = 1
            ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
            while True:
                raw = ws.recv()
                data = json.loads(raw)
                if data.get("id") == message_id:
                    if "error" in data:
                        raise RuntimeError(json.dumps(data["error"], sort_keys=True))
                    return data.get("result", {})
        finally:
            ws.close()


def _tab_from_json(item: dict[str, Any]) -> BrowserTab:
    return BrowserTab(
        tab_id=str(item.get("id") or ""),
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        type=str(item.get("type") or ""),
        web_socket_url=str(item.get("webSocketDebuggerUrl") or ""),
    )


def _friendly_cdp_error(detail: str) -> str:
    lowered = (detail or "").lower()
    if "connection refused" in lowered or "urlopen error" in lowered or "failed to establish" in lowered:
        return (
            "Chrome CDP is not reachable. Start Chrome with "
            "`--remote-debugging-port=9222` or set IRIS_CHROME_CDP_URL."
        )
    if "no chrome cdp page tab" in lowered:
        return "No Chrome tab is available through CDP yet."
    return detail or "Chrome CDP action failed."
