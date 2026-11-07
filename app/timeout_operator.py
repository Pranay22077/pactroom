"""Offline, operator-controlled resolution of timed-out handoffs.

This module never calls Switch, OpenCode, Codex, or Runner.schedule().
"""

import argparse
from contextlib import closing
import json
import time
from pathlib import Path

from .ledger import Ledger


def _get_handoff(db, task_id, handoff_id):
    row = db.execute(
        """
        SELECT
            h.id AS handoff_id,
            h.task_id,
            h.role,
            h.status,
            h.revision AS handoff_revision,
            t.revision AS task_revision,
            t.state AS task_state,
            t.commit_sha
        FROM handoffs h
        JOIN tasks t ON t.id = h.task_id
        WHERE h.id = ? AND t.id = ?
        """,
        (handoff_id, task_id),
    ).fetchone()

    if row is None:
        raise ValueError("Task/handoff pair not found")

    return dict(row)


def inspect_timeout(ledger, task_id, handoff_id):
    with closing(ledger.connect()) as db:
        return _get_handoff(db, task_id, handoff_id)


def abandon_timeout(ledger, task_id, handoff_id, actor, reason):
    if not actor or not actor.strip() or len(actor) > 200:
        raise ValueError("Valid operator identity required")

    if not reason or not reason.strip() or len(reason) > 1000:
        raise ValueError("Explicit reason required")

    with ledger.transaction() as db:
        row = _get_handoff(db, task_id, handoff_id)

        if row["status"] != "timed_out":
            raise ValueError(
                "Only timed_out handoffs can be abandoned"
            )

        if row["task_state"] != "BLOCKED":
            raise ValueError(
                "Parent task must be BLOCKED"
            )

        if row["handoff_revision"] != row["task_revision"]:
            raise ValueError(
                "Handoff belongs to a stale revision"
            )

        other = db.execute(
            """
            SELECT id FROM handoffs
            WHERE task_id = ?
              AND id != ?
              AND status IN (
                  'pending', 'sending', 'sent',
                  'uncertain', 'timed_out'
              )
            """,
            (task_id, handoff_id),
        ).fetchone()

        if other is not None:
            raise ValueError(
                "Another unresolved handoff exists for this task"
            )

        changed = db.execute(
            """
            UPDATE handoffs
            SET status = 'abandoned'
            WHERE id = ? AND task_id = ? AND status = 'timed_out'
            """,
            (handoff_id, task_id),
        ).rowcount

        if changed != 1:
            raise ValueError("Handoff changed during resolution")

        details = {
            "handoff_id": handoff_id,
            "role": row["role"],
            "actor": actor.strip(),
            "reason": reason.strip(),
            "previous_status": "timed_out",
            "new_status": "abandoned",
        }

        ledger.event(
            db,
            task_id,
            "handoff_abandoned",
            details,
            time.time(),
        )

    return {
        "task_id": task_id,
        "task_state": "BLOCKED",
        "handoff_id": handoff_id,
        "handoff_status": "abandoned",
        "actor": actor.strip(),
        "next_task_dispatched": False,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Offline Relay timeout recovery"
    )

    parser.add_argument("task_id")
    parser.add_argument("handoff_id")
    parser.add_argument("--state", type=Path,
                        default=Path.home() / "relay-state")
    parser.add_argument("--actor")
    parser.add_argument("--reason")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually abandon timeout; default is inspection only",
    )

    args = parser.parse_args()

    if args.apply:
        if not args.actor or not args.reason:
            parser.error("--apply requires --actor and --reason")

        ledger = Ledger(args.state / "relay.db")
        result = abandon_timeout(
            ledger, args.task_id, args.handoff_id,
            args.actor, args.reason,
        )
    else:
        # Explicit read-only SQLite connection for preview.
        import sqlite3

        db_path = args.state / "relay.db"

        with closing(sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True
        )) as db:
            db.row_factory = sqlite3.Row
            result = _get_handoff(
                db, args.task_id, args.handoff_id
            )

        result["note"] = (
            "PREVIEW ONLY. No task or handoff was changed."
        )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
