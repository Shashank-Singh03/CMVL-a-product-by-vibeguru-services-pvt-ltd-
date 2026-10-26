"""Fixture-only privacy, readiness and path-boundary checks for task history."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "codemap" / "scripts"))
from codemap_lib import history


class TaskHistoryTest(unittest.TestCase):
    A, B = "a" * 32, "b" * 32

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codemap-history-")
        self.addCleanup(self.temp.cleanup)
        # Keep patched path identities consistent with production canonical roots.
        self.root = Path(self.temp.name).resolve() / "fixture-project"
        self.root.mkdir()

    def state(self, ident=None, **fields):
        return dict(task_id=ident or self.A, created_at="2026-10-24T09:00:00Z",
                    updated_at="2026-10-24T09:01:00Z", **fields)

    def output(self, ident, name, content="fixture output"):
        path = self.root / ".codemap" / "tasks" / ident / "output" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def save(self, state):
        path = self.root / ".codemap" / "tasks" / state["task_id"] / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
        return path

    def render(self, states=None):
        return history.write_history(self.root, states).read_text(encoding="utf-8")

    def test_empty_history_is_local_accessible_and_idle(self):
        text = self.render()
        self.assertIn("No task history yet", text)
        self.assertIn('lang="en"', text)
        self.assertIn('id="refresh"', text)
        self.assertIn("prefers-color-scheme", text)
        self.assertNotIn('http-equiv="refresh"', text)
        self.assertNotIn('src="http', text)
        self.assertNotIn('href="http', text)
        self.assertEqual([p.name for p in (self.root / ".codemap").iterdir()], ["index.html"])

    def test_two_disk_jobs_keep_map_and_finished_video_separate(self):
        self.output(self.A, "codemap.html")
        self.output(self.B, "codemap.html")
        self.output(self.B, "codemap.mp4")
        self.output(self.B, "codemap.webm")
        self.save(self.state(status="awaiting_review", map_ready=True, reviewed_files=0,
                             attribution="ambiguous", prompt="NEVER_PUBLISH_THIS_PROMPT"))
        self.save(self.state(self.B, status="complete", map_ready=True, video_status="ready", reviewed_files=3))
        text = self.render()
        self.assertEqual(text.count('class="card '), 2)
        self.assertEqual(text.count('>Open map</a>'), 2)
        self.assertEqual(text.count('>Play video</a>'), 1)
        self.assertIn(f'href="tasks/{self.B}/output/codemap.mp4" download="codemap.mp4"', text)
        self.assertIn("Source review recorded for 3 file(s)", text)
        self.assertIn("Concurrent edits may be included", text)
        self.assertIn('http-equiv="refresh" content="5"', text)
        self.assertNotIn("NEVER_PUBLISH_THIS_PROMPT", text)
        for forbidden in ("state.json", "baseline/", "work/", "cache.json", "review.json"):
            self.assertNotIn('href="' + forbidden, text)

    def test_raw_errors_prompts_paths_and_unknown_statuses_are_not_published(self):
        state = self.state(status="analysis_failed", error="RAW_ERROR api_key='PRIVATE_CREDENTIAL_01'",
                           prompt="RAW_PROMPT", source_root="C:/Users/PRIVATE_OWNER/source",
                           worker={"arguments": "PRIVATE_WORKER_ARGUMENT"})
        text = self.render([state])
        self.assertIn("Map analysis failed", text)
        self.assertIn("This stage did not finish", text)
        for marker in ("RAW_ERROR", "PRIVATE_CREDENTIAL_01", "RAW_PROMPT", "PRIVATE_OWNER", "PRIVATE_WORKER_ARGUMENT"):
            self.assertNotIn(marker, text)
        text = self.render([self.state(status='<script>alert("x")</script>')])
        self.assertIn("Task status unavailable", text)
        self.assertNotIn('alert("x")', text)
        self.assertNotIn('http-equiv="refresh"', text)

    def test_metadata_is_escaped_and_recognized_credentials_redacted(self):
        state = self.state(status="active")
        state["created_at"] = "<img src=x onerror=alert(1)> api_key='CREDENTIAL_3456789012345'"
        state["updated_at"] = '2026-10-24 <script>attack()</script> & "quotes"'
        text = self.render([state])
        self.assertIn("&lt;img", text)
        self.assertIn("&lt;script&gt;attack()&lt;/script&gt;", text)
        self.assertIn("&amp; &quot;quotes&quot;", text)
        self.assertIn("[REDACTED]", text)
        self.assertNotIn("CREDENTIAL_3456789012345", text)
        self.assertNotIn("<img", text)

    def test_partial_or_disabled_video_is_never_linked(self):
        self.output(self.A, "codemap.html")
        self.output(self.A, "codemap.mp4", "partially written video")
        for status, video_status in (("rendering_video", "ready"), ("video_failed", "ready"),
                                     ("complete", "disabled"), ("complete", "queued")):
            with self.subTest(status=status, video_status=video_status):
                text = self.render([self.state(status=status, video_status=video_status, map_ready=True)])
                self.assertIn('>Open map</a>', text)
                self.assertNotIn('>Play video</a>', text)
                self.assertNotIn('>Download MP4</a>', text)

    def test_missing_or_empty_finished_video_does_not_claim_readiness(self):
        for exists in (False, True):
            if exists:
                self.output(self.A, "codemap.mp4", "")
            text = self.render([self.state(status="complete", video_status="ready")])
            self.assertIn("MP4 file unavailable", text)
            self.assertIn("finished MP4 file is unavailable", text)
            self.assertNotIn('>Play video</a>', text)
            self.assertNotIn('>Download MP4</a>', text)
            self.assertNotIn('http-equiv="refresh"', text)

    def test_webm_can_play_without_advertising_missing_mp4(self):
        self.output(self.A, "codemap.webm")
        text = self.render([self.state(status="complete", video_status="ready")])
        self.assertIn(f'href="tasks/{self.A}/output/codemap.webm">Play video', text)
        self.assertNotIn('>Download MP4</a>', text)
        self.assertIn("finished MP4 file is unavailable", text)

    def test_expanded_stage_statuses_and_refresh_are_explicit(self):
        cases = (("review_queued", "Review queued", True), ("reviewing", "Review in progress", True),
                 ("render_queued", "Map render queued", True), ("finishing", "Finishing map", True),
                 ("map_reviewed", "Map reviewed", False), ("review_failed", "Graph review failed", False),
                 ("render_failed", "Map rendering failed", False))
        for status, label, active in cases:
            with self.subTest(status=status):
                text = self.render([self.state(status=status)])
                self.assertIn(label, text)
                self.assertEqual('http-equiv="refresh"' in text, active)
                if active:
                    self.assertIn('<noscript><meta http-equiv="refresh" content="5"></noscript>', text)
                self.assertEqual("This stage did not finish" in text, status.endswith("_failed"))

    def test_omitted_file_count_warns_without_exposing_private_values(self):
        text = self.render([self.state(status="map_reviewed", capture_omitted_count=3)])
        self.assertIn("3 unchanged binary or oversized file(s)", text)
        for count in (0, -1, True, "PRIVATE_OMISSION_DATA"):
            with self.subTest(count=count):
                text = self.render([self.state(status="skipped", capture_omitted_count=count)])
                self.assertNotIn("binary or oversized", text)
                self.assertNotIn("PRIVATE_OMISSION_DATA", text)

    def test_promised_missing_map_warns_even_when_finished_video_exists(self):
        self.output(self.A, "codemap.mp4")
        text = self.render([self.state(status="complete", video_status="ready", map_ready=True)])
        self.assertIn("Map file unavailable", text)
        self.assertIn("saved map is marked ready, but its file is unavailable", text)
        self.assertNotIn('>Complete</span>', text)
        self.assertNotIn('>Open map</a>', text)
        self.assertIn('>Play video</a>', text)
        text = self.render([self.state(status="review_failed", map_ready=True)])
        self.assertIn("Graph review failed", text)
        self.assertIn("saved map is marked ready", text)

    def test_promised_missing_mp4_warns_even_when_map_exists(self):
        self.output(self.A, "codemap.html")
        text = self.render([self.state(status="complete", video_status="ready", map_ready=True)])
        self.assertIn("MP4 file unavailable", text)
        self.assertIn('>Open map</a>', text)
        self.assertNotIn('>Complete</span>', text)
        self.assertNotIn('>Play video</a>', text)

    def test_code_free_skipped_task_is_neither_failure_nor_success(self):
        text = self.render([self.state(status="skipped", video_status="disabled")])
        self.assertIn('>Skipped</span>', text)
        self.assertNotIn("This stage did not finish", text)
        self.assertNotIn("reports completion", text)
        self.assertNotIn("file is unavailable", text)
        self.assertNotIn('http-equiv="refresh"', text)

    def test_state_output_paths_are_ignored(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "codemap.html").write_text("outside", encoding="utf-8")
        (outside / "codemap.mp4").write_text("outside", encoding="utf-8")
        text = self.render([self.state(status="complete", video_status="ready", map_ready=True,
                                      out_dir=str(outside), output=str(outside),
                                      artifacts={"html": "https://example.invalid/map", "video": "../escape.mp4"})])
        self.assertNotIn('>Open map</a>', text)
        self.assertNotIn('>Play video</a>', text)
        self.assertNotIn(str(outside), text)
        self.assertNotIn("example.invalid", text)

    def test_reparse_output_component_is_not_linked(self):
        path = self.output(self.A, "codemap.html")
        self.output(self.A, "codemap.mp4")
        original = history._is_reparse
        with patch.object(history, "_is_reparse", side_effect=lambda p: p == path.parent or original(p)):
            text = self.render([self.state(status="complete", video_status="ready", map_ready=True)])
        self.assertNotIn('>Open map</a>', text)
        self.assertNotIn('>Play video</a>', text)

    def test_real_symlink_output_cannot_escape(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "codemap.mp4").write_text("private", encoding="utf-8")
        task = self.root / ".codemap" / "tasks" / self.A
        task.mkdir(parents=True)
        try:
            (task / "output").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks are unavailable for this user")
        text = self.render([self.state(status="complete", video_status="ready")])
        self.assertNotIn('>Play video</a>', text)

    def test_unsafe_codemap_directory_is_refused_before_publication(self):
        base = self.root / ".codemap"
        base.mkdir()
        original = history._is_reparse
        with patch.object(history, "_is_reparse", side_effect=lambda p: p == base or original(p)):
            with self.assertRaisesRegex(ValueError, "unsafe"):
                self.render()
        self.assertFalse((base / "index.html").exists())

    def test_corrupt_oversized_and_mismatched_state_records_are_generic(self):
        for content in ("{", " " * (history.MAX_STATE_BYTES + 1),
                        json.dumps(self.state(self.B, status="complete", video_status="ready"))):
            with self.subTest(length=len(content)):
                path = self.save(self.state(status="active"))
                path.write_text(content, encoding="utf-8")
                text = self.render()
                self.assertIn("Task state unavailable", text)
                self.assertNotIn('>Play video</a>', text)
                self.assertNotIn('http-equiv="refresh"', text)

    def test_numeric_task_timestamps_and_large_private_inventories(self):
        state = self.state(status="active")
        state.update(created_at=1_700_000_000, updated_at=1_700_000_060,
                     before={"files": {"private-name": "x" * 150_000}})
        self.save(state)
        text = self.render()
        self.assertIn("2023-11-14 22:13:20 UTC", text)
        self.assertIn("2023-11-14 22:14:20 UTC", text)
        self.assertIn("Task in progress", text)
        self.assertNotIn("private-name", text)
        self.assertNotIn("x" * 100, text)

    def test_unsafe_task_identity_is_ignored_and_mapping_states_work(self):
        text = self.render([self.state("../outside", status="active"), self.state(status="skipped")])
        self.assertNotIn("../outside", text)
        self.assertEqual(text.count('class="card '), 1)
        self.assertNotIn('http-equiv="refresh"', text)
        self.assertIn("Some task records are unavailable", text)
        self.assertIn("Skipped", self.render({self.A: {"status": "skipped"}}))

    def test_publication_is_atomic_and_failure_keeps_previous_history(self):
        before = self.render([])
        output = self.root / ".codemap" / "index.html"
        with patch.object(history.os, "replace", wraps=os.replace) as replace:
            self.render([self.state(status="active")])
        replace.assert_called_once()
        self.assertEqual(replace.call_args.args[1], output)
        saved = output.read_bytes()
        with patch.object(history.os, "replace", side_effect=OSError("fixture publication failure")):
            with self.assertRaises(OSError):
                self.render([])
        self.assertEqual(output.read_bytes(), saved)
        self.assertFalse(list(output.parent.glob(".history-*.tmp")))
        self.assertNotEqual(before, saved.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
