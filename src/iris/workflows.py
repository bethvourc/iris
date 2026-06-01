from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import sqlite3
import uuid

from iris.actions import LocalAction, RiskLevel
from iris.approvals import create_approval
from iris.safety import classify_action


@dataclass(frozen=True)
class Workflow:
    name: str
    description: str
    action: LocalAction


WORKFLOWS = [
    Workflow(
        "find-download", "Find recently downloaded files.", LocalAction("find_file")
    ),
    Workflow(
        "summarize-pdf-email",
        "Summarize a PDF and draft an email.",
        LocalAction("draft_email"),
    ),
    Workflow(
        "clean-downloads",
        "Organize Downloads, ask before deleting.",
        LocalAction("move_file"),
    ),
    Workflow(
        "prep-meeting",
        "Prepare notes for the next meeting.",
        LocalAction("read_calendar"),
    ),
    Workflow(
        "meeting-followup",
        "Draft post-meeting follow-up email and tasks.",
        LocalAction("draft_email"),
    ),
    Workflow(
        "watch-change",
        "Watch a page/app/file and notify on changes.",
        LocalAction("watch"),
    ),
    Workflow(
        "calendar-book",
        "Schedule or book with approval.",
        LocalAction("create_calendar_event", risk=RiskLevel.SENSITIVE),
    ),
    Workflow(
        "research-brief", "Research a topic and create a brief.", LocalAction("search")
    ),
    Workflow(
        "repo-debug-pr",
        "Debug a repo and prepare a PR.",
        LocalAction("edit_file", risk=RiskLevel.SENSITIVE),
    ),
    Workflow(
        "daily-brief",
        "Summarize calendar, inbox, tasks, and watchers.",
        LocalAction("daily_brief"),
    ),
]


def list_workflows() -> list[dict[str, object]]:
    return [
        {
            "name": workflow.name,
            "description": workflow.description,
            "action": workflow.action.name,
        }
        for workflow in WORKFLOWS
    ]


def run_workflow(db: sqlite3.Connection, name: str) -> dict[str, object]:
    workflow = next(
        (candidate for candidate in WORKFLOWS if candidate.name == name), None
    )
    if workflow is None:
        raise ValueError(f"Unknown workflow: {name}")
    run_id = uuid.uuid4().hex
    decision = classify_action(workflow.action)
    now = datetime.now(timezone.utc).isoformat()
    if decision.risk == RiskLevel.SENSITIVE:
        approval_id = create_approval(
            db,
            run_id=run_id,
            action_name=workflow.action.name,
            preview=f"Run workflow '{workflow.name}': {workflow.description}",
            details=asdict(workflow),
        )
        status = "pending_approval"
    else:
        approval_id = None
        status = "completed"
    db.execute(
        """
        INSERT INTO workflow_runs (run_id, workflow_name, status, started_at, finished_at, result_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            workflow.name,
            status,
            now,
            now if status == "completed" else None,
            json.dumps({"risk": str(decision.risk), "reason": decision.reason}),
        ),
    )
    db.commit()
    return {
        "run_id": run_id,
        "workflow": workflow.name,
        "status": status,
        "approval_id": approval_id,
        "message": decision.reason,
    }
