import tempfile
import unittest
from pathlib import Path

from app.ledger import Ledger
from app.timeout_operator import (
    inspect_timeout,
    abandon_timeout,
)


class TimeoutOperatorTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp.name) / "test.db")

        with self.ledger.transaction() as db:
            db.execute(
                "INSERT INTO rooms VALUES (?,?,?,?)",
                ("server", "room", "test-room",
                 str(Path(self.temp.name) / "workspace")),
            )

            db.execute(
                """
                INSERT INTO tasks
                (id,server_id,room_id,source_id,request,state,created)
                VALUES (?,?,?,?,?,?,?)
                """,
                ("task1", "server", "room", "source1",
                 "Build a function", "BLOCKED", 1.0),
            )

            db.execute(
                """
                INSERT INTO tasks
                (id,server_id,room_id,source_id,request,state,created)
                VALUES (?,?,?,?,?,?,?)
                """,
                ("task2", "server", "room", "source2",
                 "Next task", "NEW", 2.0),
            )

            db.execute(
                """
                INSERT INTO handoffs
                (id,task_id,role,revision,status,payload,created,deadline)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                ("handoff1", "task1", "reviewer", 1,
                 "timed_out", "{}", 1.0, 2.0),
            )

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_does_not_modify_state(self):
        row = inspect_timeout(
            self.ledger, "task1", "handoff1"
        )
        self.assertEqual(row["status"], "timed_out")
        self.assertEqual(row["task_state"], "BLOCKED")

    def test_abandon_records_event(self):
        result = abandon_timeout(
            self.ledger, "task1", "handoff1",
            "operator", "Model unavailable",
        )

        self.assertEqual(
            result["handoff_status"], "abandoned"
        )

        events = self.ledger.rows(
            """
            SELECT * FROM events
            WHERE task_id=? AND kind='handoff_abandoned'
            """,
            ("task1",),
        )
        self.assertEqual(len(events), 1)

    def test_duplicate_abandon_rejected(self):
        abandon_timeout(
            self.ledger, "task1", "handoff1",
            "operator", "Initial closure",
        )

        with self.assertRaises(ValueError):
            abandon_timeout(
                self.ledger, "task1", "handoff1",
                "operator", "Duplicate closure",
            )

        events = self.ledger.rows(
            """
            SELECT * FROM events
            WHERE kind='handoff_abandoned'
            """
        )
        self.assertEqual(len(events), 1)

    def test_next_task_not_started(self):
        abandon_timeout(
            self.ledger, "task1", "handoff1",
            "operator", "Closed manually",
        )

        next_task = self.ledger.rows(
            "SELECT state FROM tasks WHERE id='task2'"
        )[0]

        self.assertEqual(next_task["state"], "NEW")

    def test_wrong_task_rejected(self):
        with self.assertRaises(ValueError):
            abandon_timeout(
                self.ledger, "task2", "handoff1",
                "operator", "Wrong task",
            )


if __name__ == "__main__":
    unittest.main()
