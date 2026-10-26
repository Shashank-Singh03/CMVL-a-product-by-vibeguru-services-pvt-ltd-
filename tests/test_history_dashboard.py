"""Public dashboard payload checks, using only disposable task fixtures."""
from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "codemap" / "scripts"))
from codemap_lib import history


class _DataIsland(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.chunks = []
        self.islands = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("id") == "codemap-history-data":
            self.active = True
            self.islands += 1
            if attrs.get("type") != "application/json":
                raise AssertionError("Dashboard data must be inert JSON")

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.chunks.append(data)


class HistoryDashboardTest(unittest.TestCase):
    A, B = "a" * 32, "b" * 32

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codemap-dashboard-")
        self.addCleanup(self.temp.cleanup)
        # Keep reparse mocks effective when Windows TEMP is an 8.3 alias.
        self.root = Path(self.temp.name).resolve() / "fixture-project"
        self.root.mkdir()

    def state(self, ident=None, **fields):
        return dict(task_id=ident or self.A, created_at=1_700_000_000,
                    updated_at=1_700_000_060, **fields)

    def output(self, name, content, ident=None):
        path = self.root / ".codemap" / "tasks" / (ident or self.A) / "output" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def ir(self, value=None, ident=None):
        value = value if value is not None else {
            "version": "0.2.0", "repo": "fixture-project", "nodes": {},
            "why": [{"source": "agent", "text": "Improve the local task dashboard."}],
            "stats": {"files": 3, "symbols": 7, "edges": 4,
                      "risks": {"high": 1, "medium": 2, "low": 0}},
            "scope": {"mode": "diff", "files": 2, "add": 9, "del": 3},
        }
        return self.output("ir.json", json.dumps(value), ident)

    def render(self, states):
        text = history.write_history(self.root, states).read_text(encoding="utf-8")
        parser = _DataIsland()
        parser.feed(text)
        self.assertEqual(parser.islands, 1)
        data = json.loads("".join(parser.chunks),
                          parse_constant=lambda value: self.fail("Non-finite JSON: " + value))
        self.assertIsInstance(data, dict)
        return text, data

    def public_text(self, data):
        return json.dumps(data, ensure_ascii=False, sort_keys=True)

    def test_script_close_and_html_payload_cannot_escape_the_json_island(self):
        attack = '</script><script>window.dashboardAttack=1</script><img src=x onerror="window.dashboardAttack=2">'
        self.ir({"nodes": {}, "why": [{"source": "agent", "text": attack}],
                 "stats": {}, "scope": {}})
        text, data = self.render([self.state(status="complete")])
        self.assertNotIn(attack, text)
        self.assertNotIn("<script>window.dashboardAttack", text)
        self.assertNotIn('<img src=x onerror="window.dashboardAttack', text)
        self.assertEqual(data["tasks"][0]["id"], self.A)

    def test_private_state_and_ir_fields_are_not_promoted_to_dashboard_metadata(self):
        value = {
            "nodes": {"private-node": {"sig": "SOURCE_BODY_MARKER", "file": "PRIVATE_SOURCE_FILE"}},
            "why": [{"source": "agent", "text": "Improve search and task discovery."}],
            "stats": {"files": 3, "parse_errors": ["PRIVATE_PARSER_ERROR"],
                      "coverage": {"omitted_files": ["PRIVATE_OMITTED_FILE"]}},
            "scope": {"mode": "diff", "label": "PRIVATE_SCOPE_LABEL", "files": 2},
            "analysis": {"root": "C:/Users/PRIVATE_OWNER/repo", "target": "PRIVATE_ANALYSIS_TARGET"},
            "artifacts": {"source_base": "../work/PRIVATE_SNAPSHOT", "html": "https://private.invalid/"},
            "review": {"reviewed_files": ["PRIVATE_REVIEW_PATH"]},
        }
        self.ir(value)
        _, data = self.render([self.state(status="complete", prompt="PRIVATE_PROMPT",
                                         error="PRIVATE_WORKER_ERROR", source_root="PRIVATE_SOURCE_ROOT",
                                         reviewed_files=["PRIVATE_STATE_REVIEW_PATH"])])
        public = self.public_text(data)
        for marker in ("SOURCE_BODY_MARKER", "PRIVATE_SOURCE_FILE", "PRIVATE_PARSER_ERROR",
                       "PRIVATE_OMITTED_FILE", "PRIVATE_SCOPE_LABEL", "PRIVATE_OWNER",
                       "PRIVATE_ANALYSIS_TARGET", "PRIVATE_SNAPSHOT", "private.invalid",
                       "PRIVATE_REVIEW_PATH", "PRIVATE_PROMPT", "PRIVATE_WORKER_ERROR",
                       "PRIVATE_SOURCE_ROOT", "PRIVATE_STATE_REVIEW_PATH"):
            self.assertNotIn(marker, public)
        self.assertIn("Improve search and task discovery", public)

    def test_title_credentials_and_absolute_paths_are_removed(self):
        self.ir({"nodes": {}, "why": [{"source": "agent", "text":
            "Fix api_key='LIVE_CREDENTIAL_987654321' in C:\\Users\\PRIVATE_OWNER\\project\\auth.py "
            "and /home/PRIVATE_LINUX/project/auth.py using \\\\private-server\\share\\auth.py."}],
            "stats": {}, "scope": {}})
        _, data = self.render([self.state(status="complete")])
        public = self.public_text(data)
        for marker in ("LIVE_CREDENTIAL_987654321", "PRIVATE_OWNER", "PRIVATE_LINUX", "private-server"):
            self.assertNotIn(marker, public)

    def test_invalid_and_nonfinite_timestamps_do_not_enter_searchable_metadata(self):
        state = self.state(status="active")
        state.update(created_at="C:/Users/PRIVATE_TIMESTAMP/source", updated_at=math.inf)
        _, data = self.render([state])
        public = self.public_text(data)
        self.assertNotIn("PRIVATE_TIMESTAMP", public)
        self.assertNotIn("Infinity", public)
        self.assertNotIn("NaN", public)

    def test_corrupt_or_nonobject_ir_does_not_discard_the_task(self):
        for content in ("{", "null", '"PRIVATE_NONOBJECT_IR"', "[]"):
            with self.subTest(content=content):
                self.output("ir.json", content)
                _, data = self.render([self.state(status="skipped")])
                public = self.public_text(data)
                self.assertIn(self.A, public)
                self.assertNotIn("PRIVATE_NONOBJECT_IR", public)

    def test_ir_size_and_shared_read_budget_bound_metadata_reads(self):
        self.ir({"nodes": {}, "why": [{"source": "agent", "text": "PRIVATE_OVERSIZE_TITLE"}],
                 "padding": "x" * 5000})
        with patch.object(history, "MAX_IR_BYTES", 1000):
            _, data = self.render([self.state(status="complete")])
        self.assertNotIn("PRIVATE_OVERSIZE_TITLE", self.public_text(data))
        for ident in (self.A, self.B):
            self.ir({"nodes": {}, "why": [{"source": "agent", "text": "BUDGET_TITLE_" + ident[0]}],
                     "padding": "x" * 700}, ident)
        with patch.object(history, "MAX_HISTORY_IR_BYTES", 1000):
            _, data = self.render([self.state(status="complete"), self.state(self.B, status="complete")])
        public = self.public_text(data)
        self.assertIn(self.A, public)
        self.assertIn(self.B, public)
        self.assertLessEqual(sum("BUDGET_TITLE_" + letter in public for letter in "ab"), 1)

    def test_reparse_ir_or_parent_is_never_used_for_public_metadata(self):
        path = self.ir({"nodes": {}, "why": [{"source": "agent", "text": "PRIVATE_REPARSE_TITLE"}]})
        original = history._is_reparse
        for unsafe in (path, path.parent):
            with self.subTest(unsafe=unsafe.name):
                with patch.object(history, "_is_reparse", side_effect=lambda p: p == unsafe or original(p)):
                    _, data = self.render([self.state(status="complete")])
                self.assertNotIn("PRIVATE_REPARSE_TITLE", self.public_text(data))

    def test_public_counts_and_links_are_typed_and_use_only_canonical_artifacts(self):
        self.ir()
        self.output("codemap.html", "map")
        self.output("codemap.mp4", "finished video")
        _, data = self.render([self.state(status="complete", video_status="ready", map_ready=True,
                                         reviewed_files=["private-a.py", "private-b.py"],
                                         out_dir="https://private.invalid/", artifacts={"html": "../outside.html"})])
        item = data["tasks"][0]
        self.assertEqual(item["category"], "complete")
        self.assertEqual(item["reviewedFiles"], 2)
        self.assertEqual(item["changedFiles"], 2)
        self.assertEqual(item["symbols"], 7)
        self.assertEqual(item["edges"], 4)
        self.assertEqual(item["added"], 9)
        self.assertEqual(item["removed"], 3)
        self.assertEqual(item["mapUrl"], f"tasks/{self.A}/output/codemap.html")
        self.assertEqual(item["videoUrl"], f"tasks/{self.A}/output/codemap.mp4")
        self.assertEqual(item["downloadUrl"], item["videoUrl"])
        self.assertNotIn("private-", self.public_text(data))

    def test_partial_video_and_missing_promised_outputs_are_not_reported_complete(self):
        self.output("codemap.mp4", "partially written video")
        for status in ("rendering_video", "video_failed"):
            with self.subTest(status=status):
                _, data = self.render([self.state(status=status, video_status="ready")])
                item = data["tasks"][0]
                self.assertIsNone(item["videoUrl"])
                self.assertIsNone(item["downloadUrl"])
                self.assertNotEqual(item["category"], "complete")
        _, data = self.render([self.state(status="complete", map_ready=True, video_status="disabled")])
        item = data["tasks"][0]
        self.assertIsNone(item["mapUrl"])
        self.assertEqual(item["category"], "attention")
        self.assertTrue(item["warnings"])
        _, data = self.render([self.state(status="complete", video_status="disabled")])
        self.assertEqual(data["tasks"][0]["category"], "attention")

    def test_unknown_status_is_not_promoted_to_searchable_prose(self):
        _, data = self.render([self.state(status="PRIVATE_UNKNOWN_STATUS")])
        item = data["tasks"][0]
        self.assertNotIn("PRIVATE_UNKNOWN_STATUS", self.public_text(data))
        self.assertEqual(item["category"], "attention")

    def test_invalid_count_types_are_not_displayed_as_facts(self):
        self.ir({"nodes": {}, "stats": {"symbols": True, "edges": -4},
                 "scope": {"files": "PRIVATE_NOT_A_NUMBER", "add": math.inf, "del": -7}})
        _, data = self.render([self.state(status="active", reviewed_files=True)])
        item = data["tasks"][0]
        for field in ("reviewedFiles", "changedFiles", "symbols", "edges", "added", "removed"):
            self.assertIsNone(item[field], field)
        self.assertNotIn("PRIVATE_NOT_A_NUMBER", self.public_text(data))


if __name__ == "__main__":
    unittest.main()
