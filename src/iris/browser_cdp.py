from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any
from urllib import parse, request

from iris.mac_controller import ActionResult
from iris.managed_browser import ManagedChrome
from iris.polling import poll_until


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
    def __init__(
        self, base_url: str | None = None, *, auto_start: bool = False
    ) -> None:
        self.base_url = (
            base_url or os.getenv("IRIS_CHROME_CDP_URL") or "http://127.0.0.1:9222"
        ).rstrip("/")
        self.auto_start = auto_start

    def available(self) -> bool:
        chrome = ManagedChrome()
        try:
            status = chrome.status()
            if status.ok:
                return True
        except Exception:
            pass
        if self.auto_start:
            return chrome.ensure_running().ok
        return False

    def ensure_available(self) -> ActionResult:
        if self.available():
            return ActionResult(
                "browser_cdp_ensure_available",
                True,
                "Chrome CDP is reachable.",
                {"base_url": self.base_url},
            )
        return ManagedChrome().ensure_running()

    def list_tabs(self) -> ActionResult:
        ensured = self.ensure_available() if self.auto_start else None
        if ensured is not None and not ensured.ok:
            return ensured
        try:
            tabs = self._tabs()
        except Exception as exc:
            return ActionResult(
                "browser_cdp_tabs", False, _friendly_cdp_error(str(exc))
            )
        return ActionResult(
            "browser_cdp_tabs",
            True,
            f"Found {len(tabs)} Chrome tabs.",
            {"tabs": [tab.summary() for tab in tabs], "backend": "cdp"},
        )

    def current_page(self) -> ActionResult:
        ensured = self.ensure_available() if self.auto_start else None
        if ensured is not None and not ensured.ok:
            return ensured
        tab = self._current_tab()
        if tab is None:
            return ActionResult(
                "browser_cdp_current_page",
                False,
                "No Chrome CDP page tab is available.",
            )
        return ActionResult(
            "browser_cdp_current_page",
            True,
            f"Current browser page: {tab.title or tab.url or 'unknown'}",
            {**tab.summary(), "backend": "cdp"},
        )

    def navigate(self, url: str, *, new_tab: bool = False) -> ActionResult:
        ensured = self.ensure_available() if self.auto_start else None
        if ensured is not None and not ensured.ok:
            return ensured
        try:
            if new_tab:
                data = self._get_json(
                    f"/json/new?{parse.quote(url, safe=':/?&=%')}", method="PUT"
                )
                tab = _tab_from_json(data)
                return ActionResult(
                    "browser_cdp_navigate",
                    True,
                    f"Opened {url} in a new Chrome tab.",
                    {**tab.summary(), "backend": "cdp"},
                )
            tab = self._current_tab()
            if tab is None:
                return ActionResult(
                    "browser_cdp_navigate",
                    False,
                    "No Chrome CDP page tab is available.",
                )
            self._send(tab.web_socket_url, "Page.enable")
            self._send(tab.web_socket_url, "Page.navigate", {"url": url})
            return ActionResult(
                "browser_cdp_navigate",
                True,
                f"Opened {url} in the current Chrome tab.",
                {"url": url, "tab_id": tab.tab_id, "backend": "cdp"},
            )
        except Exception as exc:
            return ActionResult(
                "browser_cdp_navigate", False, _friendly_cdp_error(str(exc))
            )

    def get_dom(self, *, max_chars: int = 12000) -> ActionResult:
        ensured = self.ensure_available() if self.auto_start else None
        if ensured is not None and not ensured.ok:
            return ensured
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
        return self._evaluate_result(
            "browser_cdp_get_dom", script, result_message="I read the browser DOM."
        )

    def click_text(self, text: str) -> ActionResult:
        return self.click_element(text=text)

    def click_element(
        self,
        *,
        text: str = "",
        selector: str = "",
        role: str = "",
        exact: bool = False,
    ) -> ActionResult:
        ensured = self.ensure_available() if self.auto_start else None
        if ensured is not None and not ensured.ok:
            return ensured
        script = """
(needle, selector, role, exact) => {
  const wanted = String(needle || '').toLowerCase();
  const wantedRole = String(role || '').toLowerCase();
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const roleOf = (el) => String(el.getAttribute('role') || el.tagName || '').toLowerCase();
  const labelOf = (el) => String(
    el.getAttribute('aria-label') ||
    el.getAttribute('name') ||
    el.getAttribute('placeholder') ||
    el.innerText ||
    el.value ||
    el.title ||
    ''
  ).replace(/\\s+/g, ' ').trim();
  let match = null;
  if (selector) {
    try {
      const selected = document.querySelector(selector);
      if (selected && visible(selected)) match = selected;
    } catch (error) {}
  }
  const candidates = [...document.querySelectorAll('button,a,input,textarea,select,[contenteditable="true"],[role],[aria-label],[placeholder],[title]')].filter(visible);
  const matchesText = (el) => {
    const label = labelOf(el).toLowerCase();
    if (!wanted) return true;
    return exact ? label === wanted : label.includes(wanted);
  };
  const matchesRole = (el) => !wantedRole || roleOf(el).includes(wantedRole);
  if (!match) match = candidates.find((el) => matchesText(el) && matchesRole(el));
  if (!match) return {ok: false, reason: 'not_found', text: needle, selector, role};
  match.scrollIntoView({block: 'center', inline: 'center'});
  match.focus({preventScroll: true});
  match.click();
  return {
    ok: true,
    label: labelOf(match),
    role: roleOf(match),
    tag: match.tagName.toLowerCase(),
    url: location.href
  };
}
"""
        result = self._call_function(
            "browser_cdp_click_element", script, [text, selector, role, exact]
        )
        if not result.ok:
            return result
        payload = result.payload if isinstance(result.payload, dict) else {}
        if payload.get("ok"):
            return ActionResult(
                "browser_cdp_click_element",
                True,
                f"Clicked {payload.get('label') or text}.",
                payload,
            )
        return ActionResult(
            "browser_cdp_click_element",
            False,
            f"I could not find {text or selector or role} in the current tab.",
            payload,
        )

    def focus_element(
        self,
        *,
        field: str = "",
        selector: str = "",
        role: str = "",
    ) -> ActionResult:
        ensured = self.ensure_available() if self.auto_start else None
        if ensured is not None and not ensured.ok:
            return ensured
        script = """
(needle, selector, role) => {
  const wanted = String(needle || '').toLowerCase();
  const wantedRole = String(role || '').toLowerCase();
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const roleOf = (el) => String(el.getAttribute('role') || el.tagName || '').toLowerCase();
  const labelOf = (el) => String(el.getAttribute('aria-label') || el.getAttribute('name') || el.placeholder || el.innerText || el.title || '').replace(/\\s+/g, ' ').trim();
  let match = null;
  if (selector) {
    try {
      const selected = document.querySelector(selector);
      if (selected && visible(selected)) match = selected;
    } catch (error) {}
  }
  const candidates = [...document.querySelectorAll('input,textarea,select,[contenteditable="true"],[role="textbox"],button,a,[role],[aria-label],[placeholder]')].filter(visible);
  if (!match) {
    match = candidates.find((el) => {
      const label = labelOf(el).toLowerCase();
      const textOk = !wanted || label.includes(wanted);
      const roleOk = !wantedRole || roleOf(el).includes(wantedRole);
      return textOk && roleOk;
    });
  }
  if (!match) return {ok: false, reason: 'not_found', field: needle, selector, role};
  match.scrollIntoView({block: 'center', inline: 'center'});
  match.focus({preventScroll: true});
  return {ok: true, label: labelOf(match), role: roleOf(match), tag: match.tagName.toLowerCase(), url: location.href};
}
"""
        result = self._call_function(
            "browser_cdp_focus_element", script, [field, selector, role]
        )
        if not result.ok:
            return result
        payload = result.payload if isinstance(result.payload, dict) else {}
        if payload.get("ok"):
            return ActionResult(
                "browser_cdp_focus_element",
                True,
                f"Focused {payload.get('label') or field or selector or role}.",
                payload,
            )
        return ActionResult(
            "browser_cdp_focus_element",
            False,
            f"I could not focus {field or selector or role} in the current tab.",
            payload,
        )

    def submit(self, *, field: str = "", selector: str = "") -> ActionResult:
        ensured = self.ensure_available() if self.auto_start else None
        if ensured is not None and not ensured.ok:
            return ensured
        script = """
(needle, selector) => {
  const wanted = String(needle || '').toLowerCase();
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const labelOf = (el) => String(el.getAttribute('aria-label') || el.getAttribute('name') || el.placeholder || el.innerText || el.title || '').replace(/\\s+/g, ' ').trim();
  let match = null;
  if (selector) {
    try { match = document.querySelector(selector); } catch (error) {}
  }
  const active = document.activeElement && document.activeElement !== document.body ? document.activeElement : null;
  const fields = [...document.querySelectorAll('input,textarea,[contenteditable="true"],[role="textbox"]')].filter(visible);
  if (!match && wanted) match = fields.find((el) => labelOf(el).toLowerCase().includes(wanted));
  if (!match) match = active || fields[0];
  if (match) {
    const form = match.closest && match.closest('form');
    if (form) {
      if (form.requestSubmit) form.requestSubmit();
      else form.submit();
      return {ok: true, action: 'form_submitted', label: labelOf(match), url: location.href};
    }
    match.dispatchEvent(new KeyboardEvent('keydown', {bubbles: true, key: 'Enter', code: 'Enter'}));
    match.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: 'Enter', code: 'Enter'}));
    return {ok: true, action: 'enter_pressed', label: labelOf(match), url: location.href};
  }
  const submit = [...document.querySelectorAll('button,input[type="submit"],[role="button"]')]
    .filter(visible)
    .find((el) => /submit|send|search|go|continue|next|save/i.test(labelOf(el)));
  if (submit) {
    submit.click();
    return {ok: true, action: 'clicked_submit', label: labelOf(submit), url: location.href};
  }
  return {ok: false, reason: 'submit_target_not_found', field: needle, selector};
}
"""
        result = self._call_function("browser_cdp_submit", script, [field, selector])
        if not result.ok:
            return result
        payload = result.payload if isinstance(result.payload, dict) else {}
        if payload.get("ok"):
            return ActionResult(
                "browser_cdp_submit", True, "Submitted the browser form.", payload
            )
        return ActionResult(
            "browser_cdp_submit",
            False,
            "I could not find a browser form to submit.",
            payload,
        )

    def type_into(self, *, text: str, field: str = "") -> ActionResult:
        ensured = self.ensure_available() if self.auto_start else None
        if ensured is not None and not ensured.ok:
            return ensured
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
            return ActionResult(
                "browser_cdp_type_into", True, "Typed into the browser field.", payload
            )
        return ActionResult(
            "browser_cdp_type_into",
            False,
            "I could not find a browser field to type into.",
            payload,
        )

    def media_state(self) -> ActionResult:
        ensured = self.ensure_available() if self.auto_start else None
        if ensured is not None and not ensured.ok:
            return ensured
        script = """
(() => {
  const sessionMetadata = navigator.mediaSession && navigator.mediaSession.metadata
    ? {
        title: navigator.mediaSession.metadata.title || '',
        artist: navigator.mediaSession.metadata.artist || '',
        album: navigator.mediaSession.metadata.album || '',
      }
    : null;
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
  return {title: document.title || '', url: location.href, mediaSession: sessionMetadata, media, buttons};
})()
"""
        return self._evaluate_result(
            "browser_cdp_media_state",
            script,
            result_message="Checked browser media state.",
        )

    def spotify_play_search(self, query: str | None = None) -> ActionResult:
        ensured = self.ensure_available() if self.auto_start else None
        if ensured is not None and not ensured.ok:
            return ensured
        terms = [part for part in (query or "").lower().split() if len(part) > 1]
        if query:
            opened = self.navigate(
                f"https://open.spotify.com/search/{parse.quote_plus(query)}",
                new_tab=False,
            )
            if not opened.ok:
                return opened
        script = """
(terms) => {
  const normalize = (value) => String(value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const labelOf = (el) => normalize(el.getAttribute('aria-label') || el.innerText || el.title || '');
  const scoreText = (text) => terms.reduce((score, term) => score + (text.includes(term) ? 1 : 0), 0);
  const buttons = [...document.querySelectorAll('button,[role="button"]')].filter(visible);
  const playButtons = buttons.filter((button) => {
    const label = labelOf(button);
    return label.includes('play') && !label.includes('pause');
  });
  const scoredButtons = playButtons
    .map((button, index) => {
      const container = button.closest('[data-testid], [role="row"], li, section, article') || button.parentElement || button;
      const text = normalize(container.innerText || labelOf(button));
      return {button, index, text, label: labelOf(button), score: scoreText(text)};
    })
    .sort((a, b) => (b.score - a.score) || (a.index - b.index));
  const preferred = scoredButtons[0];
  if (preferred && (preferred.score > 0 || terms.length === 0 || scoredButtons.length === 1)) {
    preferred.button.scrollIntoView({block: 'center', inline: 'center'});
    preferred.button.click();
    return {ok: true, action: 'clicked_play', label: preferred.label, matched_text: preferred.text, score: preferred.score};
  }
  const trackLinks = [...document.querySelectorAll('a[href*="/track/"], a[href*="/album/"]')].filter(visible)
    .map((link, index) => ({link, index, text: normalize(link.innerText || link.getAttribute('aria-label') || ''), score: scoreText(normalize(link.innerText || link.getAttribute('aria-label') || ''))}))
    .sort((a, b) => (b.score - a.score) || (a.index - b.index));
  const result = trackLinks[0];
  if (result && (result.score > 0 || terms.length === 0 || trackLinks.length === 1)) {
    result.link.scrollIntoView({block: 'center', inline: 'center'});
    result.link.click();
    return {ok: true, action: 'opened_result', matched_text: result.text, score: result.score};
  }
  return {ok: false, reason: 'no_playable_result', play_buttons: playButtons.length, terms};
}
"""
        result = poll_until(
            lambda: self._call_function("browser_cdp_spotify_play", script, [terms]),
            _action_payload_ok,
            timeout_seconds=2.5 if query else 0.1,
            interval_seconds=0.25,
        )
        if not result.ok:
            return result
        payload = result.payload if isinstance(result.payload, dict) else {}
        attempts = [payload]
        if payload.get("ok") and payload.get("action") == "opened_result":
            result = poll_until(
                lambda: self._call_function(
                    "browser_cdp_spotify_play", script, [terms]
                ),
                _action_payload_ok,
                timeout_seconds=1.5,
                interval_seconds=0.25,
            )
            if not result.ok:
                return result
            payload = result.payload if isinstance(result.payload, dict) else {}
            attempts.append(payload)
        if payload.get("ok"):
            state = poll_until(
                self.media_state,
                _action_media_playing,
                timeout_seconds=1.0,
                interval_seconds=0.2,
            )
            state_payload = (
                state.payload if state.ok and isinstance(state.payload, dict) else {}
            )
            verified = _browser_media_is_playing(state_payload)
            return ActionResult(
                "browser_cdp_spotify_play",
                True,
                "Spotify playback started."
                if verified
                else "I clicked play in Spotify, but I could not verify audio yet.",
                {
                    "query": query,
                    "backend": "cdp",
                    "verified_playback": verified,
                    "attempts": attempts,
                    "media_state": state_payload,
                    **payload,
                },
            )
        return ActionResult(
            "browser_cdp_spotify_play",
            False,
            "I opened Spotify, but I could not find a playable result yet.",
            {"query": query, "backend": "cdp", **payload},
        )

    def youtube_play(self, query: str | None = None) -> ActionResult:
        ensured = self.ensure_available() if self.auto_start else None
        if ensured is not None and not ensured.ok:
            return ensured
        terms = [part for part in (query or "").lower().split() if len(part) > 1]
        if query:
            opened = self.navigate(
                f"https://www.youtube.com/results?search_query={parse.quote_plus(query)}",
                new_tab=False,
            )
            if not opened.ok:
                return opened
        script = """
(terms) => {
  const normalize = (value) => String(value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const scoreText = (text) => terms.reduce((score, term) => score + (text.includes(term) ? 1 : 0), 0);
  const video = document.querySelector('video');
  if (video) {
    const playButton = document.querySelector('.ytp-play-button, button[aria-label*="Play"], button[title*="Play"]');
    if (video.paused) {
      if (playButton && visible(playButton)) {
        playButton.click();
      } else {
        video.play().catch(() => {});
      }
    }
    return {
      ok: !video.paused,
      action: video.paused ? 'play_requested' : 'playing',
      title: document.title || '',
      currentTime: video.currentTime || 0,
      duration: video.duration || 0,
      url: location.href || ''
    };
  }
  const links = [...document.querySelectorAll('a#video-title, ytd-video-renderer a[href*="/watch"], a[href*="/watch"]')]
    .filter(visible)
    .map((link, index) => {
      const container = link.closest('ytd-video-renderer, ytd-rich-item-renderer, ytd-compact-video-renderer') || link;
      const text = normalize(container.innerText || link.getAttribute('aria-label') || link.title || link.textContent);
      return {link, index, text, score: scoreText(text)};
    })
    .filter((item) => item.text || item.link.href)
    .sort((a, b) => (b.score - a.score) || (a.index - b.index));
  const result = links[0];
  if (result && (result.score > 0 || terms.length === 0 || links.length === 1)) {
    result.link.scrollIntoView({block: 'center', inline: 'center'});
    result.link.click();
    return {ok: true, action: 'opened_video', matched_text: result.text, score: result.score};
  }
  const playButtons = [...document.querySelectorAll('button,[role="button"]')].filter(visible)
    .filter((button) => normalize(button.getAttribute('aria-label') || button.title || button.textContent).includes('play'));
  if (playButtons[0]) {
    playButtons[0].click();
    return {ok: true, action: 'clicked_play_button'};
  }
  return {ok: false, reason: 'no_video_or_play_button', title: document.title || '', url: location.href || ''};
}
"""
        attempts = []
        result = poll_until(
            lambda: self._call_function("browser_cdp_youtube_play", script, [terms]),
            _action_payload_ok,
            timeout_seconds=2.0 if query else 0.1,
            interval_seconds=0.25,
        )
        if not result.ok:
            return result
        payload = result.payload if isinstance(result.payload, dict) else {}
        attempts.append(payload)
        if payload.get("action") == "opened_video":
            result = poll_until(
                lambda: self._call_function("browser_cdp_youtube_play", script, [[]]),
                _action_payload_ok,
                timeout_seconds=2.5,
                interval_seconds=0.25,
            )
            if not result.ok:
                return result
            payload = result.payload if isinstance(result.payload, dict) else {}
            attempts.append(payload)
        if payload.get("ok"):
            state = poll_until(
                self.media_state,
                _action_media_playing,
                timeout_seconds=0.8,
                interval_seconds=0.2,
            )
            state_payload = (
                state.payload if state.ok and isinstance(state.payload, dict) else {}
            )
            verified = bool(
                payload.get("action") == "playing"
                or _browser_media_is_playing(state_payload)
            )
            return ActionResult(
                "browser_cdp_youtube_play",
                True,
                "YouTube playback started."
                if verified
                else "I clicked play on YouTube, but I could not verify audio yet.",
                {
                    "query": query,
                    "backend": "cdp",
                    "verified_playback": verified,
                    "attempts": attempts,
                    "media_state": state_payload,
                    **payload,
                },
            )
        return ActionResult(
            "browser_cdp_youtube_play",
            False,
            "I could not find a YouTube video or play button on the current page.",
            {"query": query, "backend": "cdp", **payload},
        )

    def verify_state(
        self, *, text: str = "", url_contains: str = "", title_contains: str = ""
    ) -> ActionResult:
        ensured = self.ensure_available() if self.auto_start else None
        if ensured is not None and not ensured.ok:
            return ensured
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
        result = self._call_function(
            "browser_cdp_verify_state", script, [text, url_contains, title_contains]
        )
        if not result.ok:
            return result
        payload = result.payload if isinstance(result.payload, dict) else {}
        if payload.get("ok"):
            return ActionResult(
                "browser_cdp_verify_state", True, "Browser state matches.", payload
            )
        return ActionResult(
            "browser_cdp_verify_state",
            False,
            "Browser state does not match yet.",
            payload,
        )

    def _evaluate_result(
        self, action: str, expression: str, *, result_message: str
    ) -> ActionResult:
        try:
            payload = self.evaluate(expression)
            return ActionResult(action, True, result_message, payload)
        except Exception as exc:
            return ActionResult(action, False, _friendly_cdp_error(str(exc)))

    def _call_function(
        self, action: str, function_body: str, args: list[Any]
    ) -> ActionResult:
        expression = f"({function_body})(*ARGS*)"
        encoded_args = ", ".join(json.dumps(arg) for arg in args)
        expression = expression.replace("*ARGS*", encoded_args)
        return self._evaluate_result(
            action, expression, result_message="Browser action completed."
        )

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
        tabs = [
            tab
            for tab in self._tabs()
            if tab.type == "page" and not tab.url.startswith("devtools://")
        ]
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

    def _send(
        self, ws_url: str, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            import websocket  # type: ignore
        except Exception as exc:  # pragma: no cover - dependency/import environment
            raise RuntimeError("websocket-client is not installed") from exc
        try:
            ws = websocket.create_connection(ws_url, timeout=4, origin=self.base_url)
        except Exception as exc:
            raise RuntimeError(_friendly_cdp_error(str(exc))) from exc
        try:
            message_id = 1
            ws.send(
                json.dumps({"id": message_id, "method": method, "params": params or {}})
            )
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
    if (
        "connection refused" in lowered
        or "urlopen error" in lowered
        or "failed to establish" in lowered
    ):
        return (
            "Chrome CDP is not reachable. Start Chrome with "
            "`--remote-debugging-port=9222` or set IRIS_CHROME_CDP_URL."
        )
    if "handshake status 403" in lowered or "remote-allow-origins" in lowered:
        return (
            "Chrome CDP is running without the Iris origin allowlist. "
            "Quit the Iris-managed Chrome window, then run `./iris browser start-cdp`."
        )
    if "no chrome cdp page tab" in lowered:
        return "No Chrome tab is available through CDP yet."
    return detail or "Chrome CDP action failed."


def _action_payload_ok(result: ActionResult) -> bool:
    payload = result.payload if isinstance(result.payload, dict) else {}
    return bool(result.ok and payload.get("ok"))


def _action_media_playing(result: ActionResult) -> bool:
    payload = result.payload if result.ok and isinstance(result.payload, dict) else {}
    return _browser_media_is_playing(payload)


def _browser_media_is_playing(payload: dict[str, Any]) -> bool:
    metadata = payload.get("mediaSession")
    if isinstance(metadata, dict) and (metadata.get("title") or metadata.get("artist")):
        return True
    media_items = payload.get("media")
    if isinstance(media_items, list) and any(
        isinstance(item, dict) and item.get("paused") is False for item in media_items
    ):
        return True
    buttons = payload.get("buttons")
    if isinstance(buttons, list) and any(
        "pause" in str(button).lower() for button in buttons
    ):
        return True
    return False
