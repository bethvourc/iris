from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable
import uuid

from iris.recipes import ActionRecipeRegistry
from iris.tools import ToolRegistry


@dataclass(frozen=True)
class AgentEvalCase:
    case_id: str
    category: str
    input_text: str
    expected_tools: tuple[str, ...] = ()
    expected_recipe: str = ""
    approval_expected: bool = False
    connector: str = ""
    live_safe: bool = False
    notes: str = ""

    def schema(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "input": self.input_text,
            "expected_tools": list(self.expected_tools),
            "expected_recipe": self.expected_recipe,
            "approval_expected": self.approval_expected,
            "connector": self.connector,
            "live_safe": self.live_safe,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class AgentEvalResult:
    case_id: str
    mode: str
    passed: bool
    score: float
    checks: dict[str, Any]
    message: str = ""
    payload: Any | None = None

    def schema(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "mode": self.mode,
            "passed": self.passed,
            "score": self.score,
            "checks": self.checks,
            "message": self.message,
            "payload": self.payload,
        }


def default_eval_file(project_root: Path | None = None) -> Path:
    root = project_root or Path(__file__).resolve().parents[2]
    return root / "evals" / "agent_tasks.json"


def load_eval_cases(path: Path) -> list[AgentEvalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("cases") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    cases = []
    for item in items:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("id") or item.get("case_id") or "").strip()
        input_text = str(item.get("input") or item.get("input_text") or "").strip()
        if not case_id or not input_text:
            continue
        cases.append(
            AgentEvalCase(
                case_id=case_id,
                category=str(item.get("category") or "general"),
                input_text=input_text,
                expected_tools=tuple(
                    str(tool).strip() for tool in item.get("expected_tools", []) if str(tool).strip()
                ),
                expected_recipe=str(item.get("expected_recipe") or ""),
                approval_expected=bool(item.get("approval_expected")),
                connector=str(item.get("connector") or ""),
                live_safe=bool(item.get("live_safe")),
                notes=str(item.get("notes") or ""),
            )
        )
    return cases


def list_eval_cases(path: Path) -> list[dict[str, Any]]:
    return [case.schema() for case in load_eval_cases(path)]


def run_static_evals(
    cases: list[AgentEvalCase],
    *,
    project_root: Path,
) -> list[AgentEvalResult]:
    tool_registry = ToolRegistry.default()
    recipes = ActionRecipeRegistry.default(project_root)
    recipe_names = {schema["name"] for schema in recipes.schemas()}
    results = []
    for case in cases:
        missing_tools = [tool for tool in case.expected_tools if tool_registry.get(tool) is None]
        recipe_ok = not case.expected_recipe or case.expected_recipe in recipe_names
        checks = {
            "missing_tools": missing_tools,
            "recipe_ok": recipe_ok,
            "approval_expected": case.approval_expected,
        }
        passed = not missing_tools and recipe_ok
        total = max(1, len(case.expected_tools) + (1 if case.expected_recipe else 0))
        score = (total - len(missing_tools) - (0 if recipe_ok else 1)) / total
        results.append(
            AgentEvalResult(
                case_id=case.case_id,
                mode="static",
                passed=passed,
                score=max(0.0, score),
                checks=checks,
                message="static checks passed" if passed else "static checks failed",
            )
        )
    return results


def run_live_evals(
    cases: list[AgentEvalCase],
    *,
    router_factory: Callable[[], Any],
    include_unsafe: bool = False,
) -> list[AgentEvalResult]:
    results: list[AgentEvalResult] = []
    router = router_factory()
    for case in cases:
        if not include_unsafe and not case.live_safe:
            results.append(
                AgentEvalResult(
                    case_id=case.case_id,
                    mode="live",
                    passed=True,
                    score=1.0,
                    checks={"skipped": True, "reason": "case is not marked live_safe"},
                    message="skipped",
                )
            )
            continue
        try:
            result = router.handle_text(case.input_text)
            approval_observed = "approval" in result.message.lower()
            passed = bool(result.ok) or (case.approval_expected and approval_observed)
            results.append(
                AgentEvalResult(
                    case_id=case.case_id,
                    mode="live",
                    passed=passed,
                    score=1.0 if passed else 0.0,
                    checks={
                        "ok": result.ok,
                        "approval_observed": approval_observed,
                        "approval_expected": case.approval_expected,
                    },
                    message=result.message,
                    payload=result.payload,
                )
            )
        except Exception as exc:
            results.append(
                AgentEvalResult(
                    case_id=case.case_id,
                    mode="live",
                    passed=False,
                    score=0.0,
                    checks={"exception": type(exc).__name__},
                    message=str(exc),
                )
            )
    return results


def write_eval_report(
    *,
    project_root: Path,
    results: list[AgentEvalResult],
    mode: str,
    output_path: Path | None = None,
) -> Path:
    report = {
        "run_id": uuid.uuid4().hex,
        "mode": mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": summarize_results(results),
        "results": [result.schema() for result in results],
    }
    path = output_path or (
        project_root
        / "build"
        / "evals"
        / f"agent-eval-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path


def summarize_results(results: list[AgentEvalResult]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for result in results if result.passed)
    skipped = sum(1 for result in results if result.checks.get("skipped"))
    score = sum(result.score for result in results) / total if total else 0.0
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "skipped": skipped,
        "score": round(score, 3),
    }
