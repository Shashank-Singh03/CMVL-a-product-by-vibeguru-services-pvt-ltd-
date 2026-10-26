"""Bounded lessons, honest generation progress, and public review metadata."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/codemap/scripts"))
from codemap_lib import history


class LearningHistoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codemap-learning-history-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / ".codemap"

    def state(self, letter="a", status="awaiting_review", **extra):
        return dict(task_id=letter * 32, status=status, **extra)

    def ir(self, letter="a", goal="Update a recorded return value.", reviewed=None):
        value = {"why": [{"text": goal}], "nodes": {
            "f:app.py#answer": {"kind": "function", "file": "app.py", "name": "answer",
                                  "lang": "python", "domain": "code", "status": "modified",
                                  "diff": ["- return 1", "+ return 2"]}},
            "stats": {"files": 1, "symbols": 1, "edges": 0, "languages": {"python": 1}, "domains": {"code": 1}},
            "scope": {"files": 1, "add": 1, "del": 1},
            "review": {"status": "pending", "reviewed_files": [] if reviewed is None else reviewed}}
        path = self.base / "tasks" / (letter * 32) / "output" / "ir.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return value, path

    def data(self, states):
        return history._dashboard_data(self.root, self.base, states)

    def test_page_learning_budget_keeps_other_metadata_available(self):
        self.ir()
        self.ir("b")
        single = self.data([self.state()])["tasks"][0]["learning"]
        size = len(json.dumps(single, ensure_ascii=True, separators=(",", ":")).encode())
        with patch.object(history, "MAX_HISTORY_LEARNING_BYTES", size + 100):
            tasks = self.data([self.state(), self.state("b")])["tasks"]
        self.assertEqual(sum(task["learning"]["available"] for task in tasks), 1)
        self.assertTrue(all(task["languages"] for task in tasks))
        limited = next(task for task in tasks if not task["learning"]["available"])
        self.assertIn("size limit", limited["learning"]["reason"])

    def test_disabled_learning_budget_does_not_invent_an_empty_completed_course(self):
        self.ir()
        with patch.object(history, "MAX_HISTORY_LEARNING_BYTES", 0):
            task = self.data([self.state()])["tasks"][0]
        self.assertFalse(task["learning"]["available"])
        self.assertEqual(task["learning"]["steps"], [])
        self.assertEqual(task["changedFiles"], 1)

    def test_generation_percent_counts_stages_and_excludes_skipped_or_unknown(self):
        tasks = self.data([self.state(), self.state("b", "complete", video_status="disabled"),
                           self.state("c", "skipped"), self.state("d", "cancelled"),
                           self.state("e", "unknown")])["tasks"]
        by_id = {task["id"][0]: task for task in tasks}
        self.assertEqual(by_id["a"]["progress"]["percent"], 40)
        self.assertEqual(by_id["b"]["progress"]["done"], 4)
        self.assertEqual(by_id["b"]["progress"]["percent"], 100)
        for key in "cde":
            self.assertIsNone(by_id[key]["progress"]["percent"])
            self.assertFalse(by_id[key]["progress"]["available"])

    def test_review_ratio_is_not_fabricated_when_scopes_do_not_match(self):
        self.ir(reviewed=["app.py", "helper.py", "app.py", "../private.py", "C:/private.py"])
        coverage = self.data([self.state()])["tasks"][0]["reviewCoverage"]
        self.assertEqual(coverage["reviewed"], 2)
        self.assertEqual(coverage["total"], 1)
        self.assertIsNone(coverage["percent"])

    def test_credentials_that_match_schema_words_do_not_break_lesson_navigation(self):
        self.ir(goal="Update password='changes' and api_key='function'.")
        lesson = self.data([self.state()])["tasks"][0]["learning"]
        self.assertTrue(lesson["available"])
        self.assertEqual(lesson["steps"][2]["id"], "changes")
        self.assertEqual(lesson["steps"][2]["kind"], "changes")
        ids = {concept["id"] for concept in lesson["concepts"]}
        self.assertIn("function", ids)
        self.assertIn("[REDACTED]", lesson["steps"][0]["body"])
        self.assertTrue(all(set(step["conceptIds"]) <= ids for step in lesson["steps"]))


if __name__ == "__main__":
    unittest.main()
