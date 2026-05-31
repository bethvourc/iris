from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from iris.mac_controller import ActionResult
from iris.system import applescript_string, run_osascript


@dataclass(frozen=True)
class AccessibilityElement:
    app: str
    role: str
    name: str
    value: str
    description: str
    position: tuple[int, int] | None = None
    size: tuple[int, int] | None = None
    depth: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "app": self.app,
            "role": self.role,
            "name": self.name,
            "value": self.value,
            "description": self.description,
            "position": list(self.position) if self.position else None,
            "size": list(self.size) if self.size else None,
            "depth": self.depth,
        }


class AccessibilityBackend:
    """Native macOS UI inspection/control through Accessibility/System Events."""

    def list_apps_windows(self) -> ActionResult:
        script = r'''
set oldDelims to AppleScript's text item delimiters
set rows to {}
tell application "System Events"
  repeat with proc in (application processes whose background only is false)
    set appName to name of proc as text
    set winNames to {}
    try
      repeat with win in windows of proc
        set end of winNames to (name of win as text)
      end repeat
    end try
    set AppleScript's text item delimiters to " | "
    set winText to winNames as text
    set AppleScript's text item delimiters to oldDelims
    set end of rows to appName & tab & winText
  end repeat
end tell
set AppleScript's text item delimiters to linefeed
set outputText to rows as text
set AppleScript's text item delimiters to oldDelims
return outputText
'''
        result = run_osascript(script, timeout=10)
        if not result.ok:
            return ActionResult("accessibility_list_apps_windows", False, _friendly_ax_error(result.stderr))
        apps = []
        for line in result.stdout.splitlines():
            app_name, _, windows_text = line.partition("\t")
            if not app_name:
                continue
            apps.append(
                {
                    "app": app_name,
                    "windows": [item.strip() for item in windows_text.split("|") if item.strip()],
                }
            )
        return ActionResult(
            "accessibility_list_apps_windows",
            True,
            f"Found {len(apps)} visible apps.",
            {"apps": apps},
        )

    def inspect_focused_app(self, *, max_depth: int = 3, max_items: int = 120) -> ActionResult:
        max_depth = max(1, min(int(max_depth), 6))
        max_items = max(10, min(int(max_items), 300))
        pyobjc_result = _inspect_focused_app_pyobjc(max_depth=max_depth, max_items=max_items)
        if pyobjc_result.ok:
            return pyobjc_result
        script = f'''
property rows : {{}}
property itemCount : 0
property maxDepthValue : {max_depth}
property maxItemsValue : {max_items}

on safeText(obj, attrName)
  try
    if attrName is "role" then return role of obj as text
    if attrName is "name" then return name of obj as text
    if attrName is "value" then return value of obj as text
    if attrName is "description" then return description of obj as text
  end try
  return ""
end safeText

on safePoint(obj)
  try
    set p to position of obj
    return ((item 1 of p as integer) as text) & "," & ((item 2 of p as integer) as text)
  end try
  return ""
end safePoint

on safeSize(obj)
  try
    set s to size of obj
    return ((item 1 of s as integer) as text) & "," & ((item 2 of s as integer) as text)
  end try
  return ""
end safeSize

on addElement(obj, depthValue, appName)
  if itemCount is greater than or equal to maxItemsValue then return
  set rowText to (depthValue as text) & tab & appName & tab & my safeText(obj, "role") & tab & my safeText(obj, "name") & tab & my safeText(obj, "value") & tab & my safeText(obj, "description") & tab & my safePoint(obj) & tab & my safeSize(obj)
  set end of rows to rowText
  set itemCount to itemCount + 1
  if depthValue is greater than or equal to maxDepthValue then return
  try
    repeat with child in UI elements of obj
      my addElement(child, depthValue + 1, appName)
      if itemCount is greater than or equal to maxItemsValue then exit repeat
    end repeat
  end try
end addElement

set rows to {{}}
set itemCount to 0
tell application "System Events"
  set proc to first application process whose frontmost is true
  set appName to name of proc as text
  my addElement(proc, 0, appName)
end tell
set oldDelims to AppleScript's text item delimiters
set AppleScript's text item delimiters to linefeed
set outputText to rows as text
set AppleScript's text item delimiters to oldDelims
return outputText
'''
        result = run_osascript(script, timeout=12)
        if not result.ok:
            return ActionResult("accessibility_inspect_focused_app", False, _friendly_ax_error(result.stderr))
        elements = _parse_elements(result.stdout)
        app_name = elements[0].app if elements else ""
        return ActionResult(
            "accessibility_inspect_focused_app",
            True,
            f"Inspected {app_name or 'focused app'} with {len(elements)} UI elements.",
            {
                "app": app_name,
                "elements": [element.summary() for element in elements],
                "truncated": len(elements) >= max_items,
                "backend": "system_events",
            },
        )

    def find_element(self, query: str, *, max_depth: int = 5, max_items: int = 220) -> ActionResult:
        query = query.strip()
        if not query:
            return ActionResult("accessibility_find_element", False, "I need an element name or description.")
        inspected = self.inspect_focused_app(max_depth=max_depth, max_items=max_items)
        if not inspected.ok:
            return inspected
        elements = [
            AccessibilityElement(
                app=str(item.get("app") or ""),
                role=str(item.get("role") or ""),
                name=str(item.get("name") or ""),
                value=str(item.get("value") or ""),
                description=str(item.get("description") or ""),
                position=_tuple_or_none(item.get("position")),
                size=_tuple_or_none(item.get("size")),
                depth=int(item.get("depth") or 0),
            )
            for item in (inspected.payload or {}).get("elements", [])
            if isinstance(item, dict)
        ]
        match = _best_match(elements, query)
        if match is None:
            return ActionResult(
                "accessibility_find_element",
                False,
                f"I could not find a native UI element matching {query}.",
                inspected.payload,
            )
        return ActionResult(
            "accessibility_find_element",
            True,
            f"Found {match.role or 'element'} {match.name or match.description or query}.",
            {"element": match.summary(), "query": query},
        )

    def click_element(self, query: str) -> ActionResult:
        query = query.strip()
        if not query:
            return ActionResult("accessibility_click_element", False, "I need an element name or description.")
        pyobjc_result = _click_element_pyobjc(query)
        if pyobjc_result.ok:
            return pyobjc_result
        script = f'''
property needle : {applescript_string(query.lower())}
property clickedElement : false

on safeText(obj, attrName)
  try
    if attrName is "name" then return name of obj as text
    if attrName is "value" then return value of obj as text
    if attrName is "description" then return description of obj as text
    if attrName is "role" then return role of obj as text
  end try
  return ""
end safeText

on matchesElement(obj)
  set combined to (my safeText(obj, "name") & " " & my safeText(obj, "value") & " " & my safeText(obj, "description") & " " & my safeText(obj, "role"))
  ignoring case
    if combined contains needle then return true
  end ignoring
  return false
end matchesElement

on visitElement(obj)
  if clickedElement then return
  if my matchesElement(obj) then
    try
      click obj
      set clickedElement to true
      return
    end try
  end if
  try
    repeat with child in UI elements of obj
      my visitElement(child)
      if clickedElement then exit repeat
    end repeat
  end try
end visitElement

tell application "System Events"
  set proc to first application process whose frontmost is true
  my visitElement(proc)
end tell
if clickedElement then return "clicked"
return "not found"
'''
        result = run_osascript(script, timeout=12)
        if result.ok and "clicked" in result.stdout.lower():
            return ActionResult("accessibility_click_element", True, f"Clicked {query}.", {"query": query})
        if result.ok:
            return ActionResult(
                "accessibility_click_element",
                False,
                f"I could not click a native UI element matching {query}.",
                {"query": query},
            )
        return ActionResult("accessibility_click_element", False, _friendly_ax_error(result.stderr))

    def type_text(self, text: str) -> ActionResult:
        if not text:
            return ActionResult("accessibility_type_text", False, "I need text to type.")
        script = f'tell application "System Events" to keystroke {applescript_string(text)}'
        result = run_osascript(script, timeout=10)
        return ActionResult(
            "accessibility_type_text",
            result.ok,
            "Typed into the focused field." if result.ok else _friendly_ax_error(result.stderr),
            {"chars": len(text)} if result.ok else None,
        )

    def menu_select(self, app_name: str, menu_path: list[str]) -> ActionResult:
        app_name = app_name.strip()
        parts = [part.strip() for part in menu_path if part.strip()]
        if not app_name:
            return ActionResult("accessibility_menu_select", False, "I need an app name.")
        if len(parts) < 2:
            return ActionResult(
                "accessibility_menu_select",
                False,
                "I need a menu path like File > New Window.",
            )
        target = _menu_target_expression(parts)
        script = (
            f"tell application {applescript_string(app_name)} to activate\n"
            'tell application "System Events"\n'
            f"  tell application process {applescript_string(app_name)}\n"
            f"    click {target}\n"
            "  end tell\n"
            "end tell"
        )
        result = run_osascript(script, timeout=10)
        return ActionResult(
            "accessibility_menu_select",
            result.ok,
            f"Selected {' > '.join(parts)} in {app_name}." if result.ok else _friendly_ax_error(result.stderr),
            {"app": app_name, "menu_path": parts} if result.ok else None,
        )


def _parse_elements(output: str) -> list[AccessibilityElement]:
    elements: list[AccessibilityElement] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        elements.append(
            AccessibilityElement(
                depth=_safe_int(parts[0]),
                app=parts[1],
                role=parts[2],
                name=parts[3],
                value=parts[4],
                description=parts[5],
                position=_parse_pair(parts[6]),
                size=_parse_pair(parts[7]),
            )
        )
    return elements


def _inspect_focused_app_pyobjc(*, max_depth: int, max_items: int) -> ActionResult:
    loaded = _load_pyobjc_ax()
    if loaded is None:
        return ActionResult("accessibility_inspect_focused_app", False, "PyObjC Accessibility is unavailable.")
    appkit, quartz = loaded
    if hasattr(quartz, "AXIsProcessTrusted") and not quartz.AXIsProcessTrusted():
        return ActionResult(
            "accessibility_inspect_focused_app",
            False,
            "Accessibility permission is missing for the app running Iris.",
        )
    frontmost = appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if frontmost is None:
        return ActionResult("accessibility_inspect_focused_app", False, "No focused app is available.")
    app_name = str(frontmost.localizedName() or "")
    pid = int(frontmost.processIdentifier())
    root = quartz.AXUIElementCreateApplication(pid)
    elements: list[AccessibilityElement] = []
    seen: set[int] = set()

    def visit(element: Any, depth: int) -> None:
        if len(elements) >= max_items:
            return
        identity = id(element)
        if identity in seen:
            return
        seen.add(identity)
        elements.append(_element_from_ax(quartz, element, app_name, depth))
        if depth >= max_depth:
            return
        children = _ax_attr(quartz, element, "AXChildren")
        if not isinstance(children, (list, tuple)):
            return
        for child in children:
            visit(child, depth + 1)
            if len(elements) >= max_items:
                break

    try:
        visit(root, 0)
    except Exception as exc:
        return ActionResult("accessibility_inspect_focused_app", False, f"PyObjC Accessibility failed: {exc}")
    return ActionResult(
        "accessibility_inspect_focused_app",
        True,
        f"Inspected {app_name or 'focused app'} with {len(elements)} UI elements.",
        {
            "app": app_name,
            "pid": pid,
            "elements": [element.summary() for element in elements],
            "truncated": len(elements) >= max_items,
            "backend": "pyobjc",
        },
    )


def _click_element_pyobjc(query: str) -> ActionResult:
    loaded = _load_pyobjc_ax()
    if loaded is None:
        return ActionResult("accessibility_click_element", False, "PyObjC Accessibility is unavailable.")
    appkit, quartz = loaded
    if hasattr(quartz, "AXIsProcessTrusted") and not quartz.AXIsProcessTrusted():
        return ActionResult(
            "accessibility_click_element",
            False,
            "Accessibility permission is missing for the app running Iris.",
        )
    frontmost = appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if frontmost is None:
        return ActionResult("accessibility_click_element", False, "No focused app is available.")
    app_name = str(frontmost.localizedName() or "")
    root = quartz.AXUIElementCreateApplication(int(frontmost.processIdentifier()))
    match = _find_ax_match(quartz, root, app_name, query)
    if match is None:
        return ActionResult("accessibility_click_element", False, f"I could not find a native UI element matching {query}.")
    element, summary = match
    try:
        outcome = quartz.AXUIElementPerformAction(element, getattr(quartz, "kAXPressAction", "AXPress"))
    except Exception as exc:
        return ActionResult("accessibility_click_element", False, f"Accessibility press failed: {exc}")
    ok = outcome == 0 or outcome is None
    return ActionResult(
        "accessibility_click_element",
        ok,
        f"Clicked {summary.name or summary.description or query}." if ok else "Accessibility press did not complete.",
        {"query": query, "element": summary.summary(), "backend": "pyobjc"},
    )


def _find_ax_match(
    quartz: Any,
    root: Any,
    app_name: str,
    query: str,
    *,
    max_depth: int = 6,
    max_items: int = 300,
) -> tuple[Any, AccessibilityElement] | None:
    elements: list[tuple[Any, AccessibilityElement]] = []
    seen: set[int] = set()

    def visit(element: Any, depth: int) -> None:
        if len(elements) >= max_items:
            return
        identity = id(element)
        if identity in seen:
            return
        seen.add(identity)
        summary = _element_from_ax(quartz, element, app_name, depth)
        elements.append((element, summary))
        if depth >= max_depth:
            return
        children = _ax_attr(quartz, element, "AXChildren")
        if not isinstance(children, (list, tuple)):
            return
        for child in children:
            visit(child, depth + 1)

    visit(root, 0)
    summaries = [summary for _, summary in elements]
    match = _best_match(summaries, query)
    if match is None:
        return None
    for element, summary in elements:
        if summary == match:
            return element, summary
    return None


def _element_from_ax(quartz: Any, element: Any, app_name: str, depth: int) -> AccessibilityElement:
    role = _ax_string(quartz, element, "AXRole")
    name = _ax_string(quartz, element, "AXTitle") or _ax_string(quartz, element, "AXIdentifier")
    value = _ax_string(quartz, element, "AXValue")
    description = _ax_string(quartz, element, "AXDescription") or _ax_string(quartz, element, "AXHelp")
    return AccessibilityElement(
        app=app_name,
        role=role,
        name=name,
        value=value,
        description=description,
        position=_ax_pair(quartz, element, "AXPosition"),
        size=_ax_pair(quartz, element, "AXSize"),
        depth=depth,
    )


def _load_pyobjc_ax() -> tuple[Any, Any] | None:
    try:  # pragma: no cover - depends on macOS PyObjC runtime
        import AppKit  # type: ignore
        import Quartz  # type: ignore

        return AppKit, Quartz
    except Exception:
        return None


def _ax_attr(quartz: Any, element: Any, attribute: str) -> Any:
    try:
        result = quartz.AXUIElementCopyAttributeValue(element, attribute, None)
    except TypeError:
        try:
            result = quartz.AXUIElementCopyAttributeValue(element, attribute)
        except Exception:
            return None
    except Exception:
        return None
    if isinstance(result, tuple):
        if not result:
            return None
        if len(result) >= 2:
            err, value = result[0], result[1]
            return value if err == 0 else None
        return result[0]
    return result


def _ax_string(quartz: Any, element: Any, attribute: str) -> str:
    value = _ax_attr(quartz, element, attribute)
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _ax_pair(quartz: Any, element: Any, attribute: str) -> tuple[int, int] | None:
    value = _ax_attr(quartz, element, attribute)
    if value is None:
        return None
    try:
        if hasattr(quartz, "AXValueGetValue") and hasattr(quartz, "kAXValueCGPointType") and attribute == "AXPosition":
            ok, point = quartz.AXValueGetValue(value, quartz.kAXValueCGPointType, None)
            if ok and point is not None:
                return int(point.x), int(point.y)
        if hasattr(quartz, "AXValueGetValue") and hasattr(quartz, "kAXValueCGSizeType") and attribute == "AXSize":
            ok, size = quartz.AXValueGetValue(value, quartz.kAXValueCGSizeType, None)
            if ok and size is not None:
                return int(size.width), int(size.height)
    except Exception:
        return None
    return None


def _best_match(elements: list[AccessibilityElement], query: str) -> AccessibilityElement | None:
    needle = _normalize(query)
    if not needle:
        return None
    best: tuple[int, AccessibilityElement] | None = None
    for element in elements:
        haystacks = [
            element.name,
            element.value,
            element.description,
            element.role,
            " ".join([element.role, element.name, element.value, element.description]),
        ]
        score = 0
        for text in haystacks:
            normalized = _normalize(text)
            if not normalized:
                continue
            if normalized == needle:
                score = max(score, 100)
            elif needle in normalized:
                score = max(score, 75)
            else:
                tokens = [token for token in needle.split() if token]
                if tokens and all(token in normalized for token in tokens):
                    score = max(score, 55)
        if element.position and element.size:
            score += 4
        if element.role.lower() in {"button", "checkbox", "radio button", "menu item", "text field"}:
            score += 6
        if score and (best is None or score > best[0]):
            best = (score, element)
    return best[1] if best else None


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_pair(value: str) -> tuple[int, int] | None:
    if not value or "," not in value:
        return None
    left, _, right = value.partition(",")
    try:
        return int(left), int(right)
    except ValueError:
        return None


def _tuple_or_none(value: Any) -> tuple[int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _menu_target_expression(parts: list[str]) -> str:
    first = applescript_string(parts[0])
    last = applescript_string(parts[-1])
    if len(parts) == 2:
        return f"menu item {last} of menu {first} of menu bar item {first} of menu bar 1"
    target = f"menu item {last}"
    for label in reversed(parts[1:-1]):
        quoted = applescript_string(label)
        target = f"{target} of menu {quoted} of menu item {quoted}"
    return f"{target} of menu {first} of menu bar item {first} of menu bar 1"


def _friendly_ax_error(detail: str) -> str:
    lowered = (detail or "").lower()
    if "not authorized" in lowered or "assistive access" in lowered or "not allowed" in lowered:
        return "Accessibility permission is missing for the app running Iris."
    if "system events got an error" in lowered:
        return "macOS Accessibility could not read the focused app yet."
    return detail or "Accessibility action failed."
