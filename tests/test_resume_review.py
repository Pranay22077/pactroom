import hashlib
from contextlib import closing
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.ledger import Ledger
from app.workspace_manager import WorkspaceManager
from app.resume_review import plan, resume


class ResumeReviewTests(unittest.TestCase):

    def setUp(self):
        runtime = Path(__file__).resolve().parents[1] / '.runtime'
        runtime.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=runtime)
        self.base = Path(self.tmp.name)
        self.ledger = Ledger(self.base / "state" / "relay.db")
        self.ws = WorkspaceManager(self.base / "projects", self.ledger)

        repo = self.ws.ensure_workspace("s", "r", "general")
        (repo / "add.py").write_text(
            "def add(a, b):\n    return a + b\n"
        )

        def git(*args):
            result = subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True, text=True, check=True
            )
            return result.stdout.strip()

        git("add", ".")
        git(
            "-c", "user.name=Relay Test",
            "-c", "user.email=relay-test@example.invalid",
            "commit", "-m", "Candidate"
        )

        sha = git("rev-parse", "HEAD")
        brief = "Implement addition with unittest"
        brief_hash = hashlib.sha256(brief.encode()).hexdigest()

        self.repo = repo
        self.sha = sha
        self.brief_hash = brief_hash

        with self.ledger.transaction() as db:
            db.execute(
                """
                INSERT INTO tasks
                (id,server_id,room_id,source_id,request,state,
                 revision,brief,brief_hash,commit_sha,created)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                ("t1", "s", "r", "source1", "Add function",
                 "BLOCKED", 1, brief, brief_hash, sha, 1.0)
            )

            db.execute(
                """
                INSERT INTO tasks
                (id,server_id,room_id,source_id,request,state,created)
                VALUES (?,?,?,?,?,?,?)
                """,
                ("t2", "s", "r", "source2", "Next task", "NEW", 2.0)
            )

            payload = {
                "commit_sha": sha,
                "brief_hash": brief_hash,
            }

            db.execute(
                """
                INSERT INTO handoffs
                (id,task_id,role,revision,status,payload,created,deadline)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                ("h1", "t1", "reviewer", 1, "timed_out",
                 json.dumps(payload), 1.0, 2.0)
            )

            self.ledger.event(
                db, "t1", "task_blocked",
                {"handoff_id": "h1"}, 3.0
            )

            self.ledger.event(
                db, "t1", "tests_recorded", {
                    "commit_sha": sha,
                    "brief_hash": brief_hash,
                    "revision": 1,
                    "passed": True,
                    "exit_code": 0,
                    "test_count": 8,
                    "timed_out": False,
                }, 2.0
            )

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self):
        return resume(
            self.ledger, self.ws,
            "t1", "h1", "operator", "Retry review"
        )

    def test_preview_preserves_state(self):
        with closing(self.ledger.connect()) as db:
            preview, _ = plan(db, "t1", "h1")

        self.assertTrue(preview["eligible"])
        self.assertEqual(
            self.ledger.rows(
                "SELECT status FROM handoffs WHERE id='h1'"
            )[0]["status"],
            "timed_out"
        )

    def test_resume_queues_without_dispatch(self):
        result = self.apply()
        self.assertEqual(result["task_state"], "REVIEWING")
        self.assertFalse(result["dispatched"])

        rows = self.ledger.rows(
            "SELECT status,role,payload FROM handoffs "
            "WHERE task_id='t1' ORDER BY created"
        )

        self.assertEqual(rows[0]["status"], "abandoned")
        self.assertEqual(rows[1]["status"], "pending")
        self.assertEqual(rows[1]["role"], "reviewer")

        payload = json.loads(rows[1]["payload"])
        self.assertEqual(payload["commit_sha"], self.sha)
        self.assertEqual(payload["test_evidence"]["test_count"], 8)

    def test_duplicate_resume_rejected(self):
        self.apply()

        with self.assertRaises(ValueError):
            self.apply()

    def test_dirty_repository_rejected(self):
        (self.repo / "untracked.txt").write_text("change")

        with self.assertRaises(ValueError):
            self.apply()

        state = self.ledger.rows(
            "SELECT state FROM tasks WHERE id='t1'"
        )[0]["state"]

        self.assertEqual(state, "BLOCKED")

    def test_second_task_remains_new(self):
        self.apply()

        state = self.ledger.rows(
            "SELECT state FROM tasks WHERE id='t2'"
        )[0]["state"]

        self.assertEqual(state, "NEW")


if __name__ == "__main__":
    unittest.main()
