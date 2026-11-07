"""Operator-authorized, offline Reviewer resume.

Preparing a new handoff does not dispatch it.
No Switch or AI provider is contacted by this module.
"""

import argparse
import hashlib
import json
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path

from .ledger import Ledger
from .runner import Runner
from .workspace_manager import WorkspaceManager
from .git_manager import verify_candidate

ACTIVE = ("pending", "sending", "sent", "uncertain", "timed_out")


def plan(db, task_id, handoff_id):
    issues = []

    task = db.execute(
        "SELECT * FROM tasks WHERE id=?", (task_id,)
    ).fetchone()

    old = db.execute(
        "SELECT * FROM handoffs WHERE id=? AND task_id=?",
        (handoff_id, task_id),
    ).fetchone()

    if task is None or old is None:
        raise ValueError("Task/handoff pair not found")

    task = dict(task)
    old = dict(old)

    if task["state"] != "BLOCKED":
        issues.append("Task must be BLOCKED")

    if old["role"] != "reviewer":
        issues.append("Only Reviewer handoffs can be resumed here")

    if old["status"] != "timed_out":
        issues.append("Original handoff must be timed_out")

    if old["revision"] != task["revision"]:
        issues.append("Specification revision changed")

    if not task["brief"] or not task["brief_hash"]:
        issues.append("Planner specification missing")
    elif hashlib.sha256(
        task["brief"].encode()
    ).hexdigest() != task["brief_hash"]:
        issues.append("Specification hash mismatch")

    if not task["commit_sha"]:
        issues.append("Implementation commit missing")

    # Resume only a task blocked by this exact handoff timeout.
    blocked = db.execute(
        """
        SELECT data FROM events
        WHERE task_id=? AND kind='task_blocked'
        ORDER BY id DESC LIMIT 1
        """,
        (task_id,),
    ).fetchone()

    try:
        blocked_data = json.loads(blocked["data"]) if blocked else {}
    except (ValueError, TypeError):
        blocked_data = {}

    if blocked_data.get("handoff_id") != handoff_id:
        issues.append("Task blocker does not match this handoff")

    # Prevent a concurrent or unrelated active handoff.
    active = db.execute(
        """
        SELECT id FROM handoffs
        WHERE status IN ('pending','sending','sent','uncertain','timed_out')
          AND id != ?
        LIMIT 1
        """,
        (handoff_id,),
    ).fetchone()

    if active:
        issues.append("Another active or unresolved handoff exists")

    room = db.execute(
        """
        SELECT path FROM rooms
        WHERE server_id=? AND room_id=?
        """,
        (task["server_id"], task["room_id"]),
    ).fetchone()

    evidence = None

    events = db.execute(
        """
        SELECT kind,data FROM events
        WHERE task_id=?
          AND kind IN ('tests_recorded','operator_recheck')
        ORDER BY id DESC
        """,
        (task_id,),
    ).fetchall()

    for event in events:
        try:
            record = json.loads(event["data"])
            if event["kind"] == "operator_recheck":
                record = record["evidence"]
        except (ValueError, KeyError, TypeError):
            continue

        if not isinstance(record, dict):
            continue

        if (
            record.get("commit_sha") == task["commit_sha"]
            and record.get("brief_hash") == task["brief_hash"]
            and record.get("revision") == task["revision"]
        ):
            evidence = record
            break

    if evidence is None:
        issues.append("Matching controlled test evidence missing")
    elif not (
        evidence.get("passed") is True
        and evidence.get("exit_code") == 0
        and isinstance(evidence.get("test_count"), int)
        and not isinstance(evidence.get("test_count"), bool)
        and evidence["test_count"] > 0
        and evidence.get("timed_out") is False
    ):
        issues.append("Latest matching test evidence is not passing")

    if room is None:
        issues.append("Room workspace mapping missing")
    elif task["commit_sha"]:
        try:
            verify_candidate(Path(room["path"]), task["commit_sha"])
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            issues.append("Git candidate validation: " + str(error))

    try:
        old_payload = json.loads(old["payload"])
    except (ValueError, TypeError):
        old_payload = {}

    if (
        old_payload.get("commit_sha") != task["commit_sha"]
        or old_payload.get("brief_hash") != task["brief_hash"]
    ):
        issues.append("Original Reviewer payload does not match candidate")

    return {
        "task_id": task_id,
        "old_handoff_id": handoff_id,
        "task_state": task["state"],
        "handoff_status": old["status"],
        "commit_sha": task["commit_sha"],
        "evidence": evidence,
        "issues": issues,
        "eligible": not issues,
    }, task


def resume(ledger, workspace_manager, task_id, handoff_id, actor, reason):
    if not actor or not actor.strip() or len(actor) > 200:
        raise ValueError("Operator identity required")

    if not reason or not reason.strip() or len(reason) > 1000:
        raise ValueError("Explicit recovery reason required")

    runner = Runner(ledger, workspace_manager, None)

    with ledger.transaction() as db:
        preview, task = plan(db, task_id, handoff_id)

        if not preview["eligible"]:
            raise ValueError("; ".join(preview["issues"]))

        now = time.time()

        changed = db.execute(
            """
            UPDATE handoffs SET status='abandoned'
            WHERE id=? AND status='timed_out'
            """,
            (handoff_id,),
        ).rowcount

        if changed != 1:
            raise ValueError("Handoff changed during recovery")

        ledger.event(
            db, task_id, "handoff_abandoned", {
                "handoff_id": handoff_id,
                "actor": actor.strip(),
                "reason": reason.strip(),
                "resolution": "resume_review",
            }, now,
        )

        # Creates a pending handoff; does NOT send it.
        runner._handoff(
            db, task, "reviewer", {
                "test_evidence": preview["evidence"],
                "recovery_of": handoff_id,
            },
        )

        new = db.execute(
            """
            SELECT id FROM handoffs
            WHERE task_id=? AND role='reviewer'
              AND revision=? AND status='pending'
            ORDER BY created DESC LIMIT 1
            """,
            (task_id, task["revision"]),
        ).fetchone()

        if new is None:
            raise RuntimeError("New Reviewer handoff was not created")

        ledger.event(
            db, task_id, "review_resume_queued", {
                "previous_handoff": handoff_id,
                "new_handoff": new["id"],
                "actor": actor.strip(),
                "reason": reason.strip(),
                "commit_sha": task["commit_sha"],
            }, now,
        )

    return {
        "task_id": task_id,
        "task_state": "REVIEWING",
        "old_handoff": "abandoned",
        "new_handoff_id": new["id"],
        "new_handoff_status": "pending",
        "dispatched": False,
    }


def require_offline():
    check = subprocess.run(
        ["pgrep", "-af", "python3.*-m app[.]cli serve"],
        capture_output=True,
        text=True,
    )

    if check.returncode == 0:
        raise SystemExit("Stop Relay workers before applying recovery")

    check = subprocess.run(
        ["systemctl", "is-active", "relay-runner"],
        capture_output=True,
        text=True,
    )

    if check.stdout.strip() == "active":
        raise SystemExit("Stop Relay systemd service before recovery")


def main():
    parser = argparse.ArgumentParser(
        description="Inspect or prepare a safe Reviewer resume"
    )

    parser.add_argument("task_id")
    parser.add_argument("handoff_id")
    parser.add_argument(
        "--state", type=Path,
        default=Path.home() / "relay-state"
    )
    parser.add_argument("--actor")
    parser.add_argument("--reason")
    parser.add_argument("--apply", action="store_true")

    args = parser.parse_args()

    database = args.state / "relay.db"

    if args.apply:
        require_offline()

        ledger = Ledger(database)
        workspace_manager = WorkspaceManager(
            Path.home() / "relay-projects", ledger
        )

        result = resume(
            ledger,
            workspace_manager,
            args.task_id,
            args.handoff_id,
            args.actor,
            args.reason,
        )
    else:
        with closing(sqlite3.connect(
            f"file:{database}?mode=ro", uri=True
        )) as db:
            db.row_factory = sqlite3.Row
            result, _ = plan(
                db, args.task_id, args.handoff_id
            )

        result.pop("evidence", None)
        result["note"] = "PREVIEW ONLY. No changes made."

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
