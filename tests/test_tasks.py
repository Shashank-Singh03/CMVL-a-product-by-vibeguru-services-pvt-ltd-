"""Private task captures are repeatable and never execute project programs."""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "codemap" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from codemap_lib import tasks


class TaskSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codemap-tasks-tests-")
        self.addCleanup(self.temp.cleanup)
        # Windows TEMP may use an 8.3 alias; runtime roots are canonicalized.
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.source = self.root / "app.py"
        self.source.write_bytes(b"raise RuntimeError('Fixture must never execute')\r\n\r\ndef value():\r\n    return 1\r\n")

    def start(self, session="session-one", prompt="prompt-one"):
        return tasks.start(self.root, session, prompt)

    def test_start_preserves_raw_bytes_and_captures_tests_and_resolver_configs(self):
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_app.py").write_text("def test_value():\n    pass\n")
        (self.root / "tsconfig.json").write_text('{"extends":"./config/base.json"}')
        (self.root / "config").mkdir()
        (self.root / "config" / "base.json").write_text('{"compilerOptions":{"baseUrl":".."}}')
        original = self.source.read_bytes()
        state = self.start()
        self.assertEqual(state["status"], "active")
        self.assertEqual(Path(state["before_root"]) / "app.py", Path(state["out_dir"]) / "baseline" / "app.py")
        self.assertEqual((Path(state["before_root"]) / "app.py").read_bytes(), original)
        self.assertTrue({"app.py", "tests/test_app.py", "tsconfig.json", "config/base.json"} <= set(state["before"]["files"]))
        self.assertEqual(self.source.read_bytes(), original)
        self.assertIsNone(tasks.changed(state))
        self.assertNotEqual(self.source.stat().st_ino, (Path(state["before_root"]) / "app.py").stat().st_ino)

    def test_prompt_dedup_and_session_paths_use_only_hashes(self):
        session, prompt = "../../PRIVATE-SESSION-ID", "PRIVATE-PROMPT-ID"
        first = self.start(session, prompt)
        self.source.write_text("def value():\n    return 2\n")
        repeated = self.start(session, prompt)
        self.assertEqual(first["task_id"], repeated["task_id"])
        self.assertEqual(first["before"]["digest"], repeated["before"]["digest"])
        self.assertEqual(tasks.current(self.root, session)["task_id"], first["task_id"])
        for path in (self.root / ".codemap" / "tasks").rglob("*.json"):
            text = path.read_text()
            self.assertNotIn(session, text)
            self.assertNotIn(prompt, text)
            self.assertNotIn("PRIVATE", path.name)

    def test_no_prompt_id_is_a_new_task_and_supersedes_unfrozen_old_task(self):
        first = tasks.start(self.root, "session")
        second = tasks.start(self.root, "session")
        self.assertNotEqual(first["task_id"], second["task_id"])
        previous = tasks.load(self.root, first["task_id"])
        self.assertEqual(previous["status"], "interrupted")
        self.assertFalse(previous["coding_active"])
        self.assertEqual(previous["superseded_by"], second["task_id"])
        with self.assertRaises(tasks.TaskError):
            tasks.freeze(self.root, first["task_id"])

    def test_replayed_older_prompt_does_not_replace_current_pointer(self):
        first = self.start(prompt="one")
        second = self.start(prompt="two")
        repeated = self.start(prompt="one")
        self.assertEqual(repeated["task_id"], first["task_id"])
        self.assertEqual(tasks.current(self.root, "session-one")["task_id"], second["task_id"])

    def test_freeze_is_immutable_and_idempotent_after_later_live_edits(self):
        started = self.start()
        self.source.write_text("def value():\n    return 2\n")
        (self.root / "new.py").write_text("def added():\n    return True\n")
        frozen = tasks.freeze(self.root, started["task_id"])
        work = Path(frozen["work_root"])
        self.assertEqual(frozen["status"], "frozen")
        self.assertFalse(frozen["coding_active"])
        self.assertTrue(tasks.changed(frozen))
        baseline = json.loads((Path(frozen["out_dir"]) / "baseline.json").read_text())
        self.assertEqual(baseline["kind"], "files")
        self.assertEqual(baseline["files"], ["app.py"])
        self.assertRegex(baseline["created"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        saved = (work / "app.py").read_bytes()
        self.source.write_text("def value():\n    return 99\n")
        repeated = tasks.freeze(self.root, started["task_id"])
        self.assertEqual(repeated["revision"], frozen["revision"])
        self.assertEqual((work / "app.py").read_bytes(), saved)
        self.assertTrue(tasks.verify(self.root, started["task_id"]))

    def test_no_op_and_real_deletions_are_distinct(self):
        no_op = self.start()
        self.assertFalse(tasks.changed(tasks.freeze(self.root, no_op["task_id"])))
        deletion = self.start(prompt="delete")
        self.source.unlink()
        frozen = tasks.freeze(self.root, deletion["task_id"])
        self.assertEqual(frozen["after"]["missing"], ["app.py"])
        self.assertEqual(frozen["after"]["skipped"], [])
        self.assertTrue(tasks.changed(frozen))
        self.assertTrue(tasks.verify(self.root, deletion["task_id"]))

    def test_unreadable_previous_source_is_unknown_never_deleted(self):
        state = self.start()
        original = tasks._read_bytes_status
        def unreadable(root, name):
            return (None, "unreadable") if root == self.root and name == "app.py" else original(root, name)
        with patch.object(tasks, "_read_bytes_status", side_effect=unreadable):
            frozen = tasks.freeze(self.root, state["task_id"])
        self.assertEqual(frozen["after"]["missing"], [])
        self.assertEqual(frozen["after"]["skipped"], [{"path": "app.py", "reason": "unreadable"}])
        self.assertIsNone(tasks.changed(frozen))

    def test_binary_and_oversized_sources_are_explicit_omissions(self):
        (self.root / "binary.py").write_bytes(b"data\x00tail")
        (self.root / "large.py").write_bytes(b"x" * (tasks.MAX_BYTES + 1))
        state = self.start()
        reasons = {item["path"]: item["reason"] for item in state["before"]["skipped"]}
        self.assertEqual(reasons, {"binary.py": "binary", "large.py": "too_large"})
        frozen = tasks.freeze(self.root, state["task_id"])
        self.assertFalse(tasks.changed(frozen))
        self.assertTrue(tasks.verify(self.root, state["task_id"]))

    def test_unchanged_opaque_files_allow_readable_changes_but_opaque_changes_remain_unknown(self):
        opaque = self.root / "large.py"
        opaque.write_bytes(b"x" * (tasks.MAX_BYTES + 1))
        state = self.start(prompt="readable-change")
        self.source.write_text("def changed():\n    return 2\n")
        frozen = tasks.freeze(self.root, state["task_id"])
        self.assertTrue(tasks.changed(frozen))
        self.assertIn("sha256", frozen["before"]["skipped"][0])
        for mutation in ("opaque-change", "opaque-shrink", "opaque-deletion"):
            with self.subTest(mutation=mutation):
                opaque.write_bytes(b"x" * (tasks.MAX_BYTES + 1))
                state = self.start(prompt=mutation)
                if mutation == "opaque-change":
                    opaque.write_bytes(b"y" * (tasks.MAX_BYTES + 1))
                elif mutation == "opaque-shrink":
                    opaque.write_text("def small():\n    pass\n")
                else:
                    opaque.unlink()
                self.assertIsNone(tasks.changed(tasks.freeze(self.root, state["task_id"])))

    def test_opaque_hash_budget_is_bounded_and_does_not_assert_no_changes(self):
        (self.root / "large.py").write_bytes(b"x" * (tasks.MAX_BYTES + 1))
        with patch.object(tasks, "MAX_OPAQUE_FILE_BYTES", 10):
            state = self.start()
            frozen = tasks.freeze(self.root, state["task_id"])
        self.assertNotIn("sha256", state["before"]["skipped"][0])
        self.assertEqual(state["before"]["skipped"][0]["hash_reason"], "too_large")
        self.assertIsNone(tasks.changed(frozen))

    def test_overlap_marks_both_sessions_ambiguous_and_does_not_claim_authorship(self):
        one = self.start("one", "p1")
        two = self.start("two", "p2")
        one = tasks.load(self.root, one["task_id"])
        self.assertEqual(one["attribution"], "ambiguous")
        self.assertEqual(two["attribution"], "ambiguous")
        self.assertEqual(one["overlaps"], [two["task_id"]])
        self.assertEqual(two["overlaps"], [one["task_id"]])
        self.assertIn("do not verify isolated authorship", one["capture_note"])
        tasks.freeze(self.root, one["task_id"])
        tasks.freeze(self.root, two["task_id"])
        three = self.start("three", "p3")
        self.assertEqual(three["attribution"], "window")

    def test_compare_and_swap_revision_and_protected_fields(self):
        state = self.start()
        self.assertGreaterEqual(state["updated_at"], state["started_at"])
        with patch.object(tasks.time, "time", return_value=state["updated_at"] + 10):
            updated = tasks.transition(self.root, state["task_id"], {"revision": state["revision"]}, status="queued", worker_token="a")
        self.assertEqual(updated["updated_at"], state["updated_at"] + 10)
        self.assertEqual(updated["revision"], state["revision"] + 1)
        self.assertIsNone(tasks.transition(self.root, state["task_id"], {"revision": state["revision"]}, status="stale"))
        self.assertIsNone(tasks.transition(self.root, state["task_id"], {"worker_token": "wrong"}, status="stale"))
        with self.assertRaises(tasks.TaskError):
            tasks.update(self.root, state["task_id"], work_root=str(self.base))
        with self.assertRaises(tasks.TaskError):
            tasks.update(self.root, state["task_id"], updated_at=0)
        self.assertEqual(tasks.load(self.root, state["task_id"])["worker_token"], "a")
        self.assertEqual(list(Path(state["task_dir"]).glob(".task-save-*")), [])

    def test_missing_corrupt_and_unsafe_state_is_not_silently_replaced(self):
        self.assertIsNone(tasks.current(self.root, "empty"))
        state = self.start()
        path = Path(state["task_dir"]) / "state.json"
        original = path.read_bytes()
        path.write_text("{")
        with self.assertRaises(tasks.TaskError):
            tasks.current(self.root, "session-one")
        path.write_bytes(original)
        tampered = json.loads(original)
        tampered["work_root"] = str(self.base)
        path.write_text(json.dumps(tampered))
        with self.assertRaises(tasks.TaskError):
            tasks.load(self.root, state["task_id"])
        for identifier in ("../outside", "A" * 32, "0" * 31):
            with self.subTest(identifier=identifier), self.assertRaises(tasks.TaskError):
                tasks.load(self.root, identifier)

    def test_verify_detects_work_addition_change_deletion_and_baseline_corruption(self):
        for mutation in ("add", "change", "delete", "baseline", "baseline-json"):
            with self.subTest(mutation=mutation):
                state = self.start(prompt=mutation)
                frozen = tasks.freeze(self.root, state["task_id"])
                work = Path(frozen["work_root"])
                if mutation == "add":
                    (work / "extra.py").write_text("pass\n")
                elif mutation == "change":
                    (work / "app.py").write_text("pass\n")
                elif mutation == "delete":
                    (work / "app.py").unlink()
                elif mutation == "baseline":
                    (Path(frozen["before_root"]) / "app.py").write_text("pass\n")
                else:
                    (Path(frozen["out_dir"]) / "baseline.json").write_text('{"kind":"files","files":[]}')
                with self.assertRaises(tasks.TaskError):
                    tasks.verify(self.root, state["task_id"])

    def test_interrupted_end_capture_cannot_recapture_later_source(self):
        state = self.start()
        with patch.object(tasks, "_capture", side_effect=OSError("PRIVATE_ERROR_CONTENT")):
            with self.assertRaises(tasks.TaskError):
                tasks.freeze(self.root, state["task_id"])
        saved = tasks.load(self.root, state["task_id"])
        self.assertEqual(saved["status"], "capture_failed")
        self.assertFalse(saved["coding_active"])
        self.assertNotIn("PRIVATE_ERROR_CONTENT", json.dumps(saved))
        self.source.write_text("def later():\n    pass\n")
        with self.assertRaises(tasks.TaskError):
            tasks.freeze(self.root, state["task_id"])

    def test_private_storage_and_source_symlink_escapes_are_rejected(self):
        outside = self.base / "outside.py"
        outside.write_text("PRIVATE_SOURCE_MUST_NOT_BE_COPIED\n")
        link = self.root / "escape.py"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("creating symlinks requires platform permissions")
        state = self.start()
        self.assertIn({"path": "escape.py", "reason": "unsafe_path"}, state["before"]["skipped"])
        self.assertFalse((Path(state["before_root"]) / "escape.py").exists())
        self.assertEqual(outside.read_text(), "PRIVATE_SOURCE_MUST_NOT_BE_COPIED\n")
        other = self.base / "other"
        other.mkdir()
        (other / ".codemap").symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(tasks.TaskError):
            tasks.start(other, "session", "prompt")

    def test_project_lock_is_bounded_and_cas_avoids_nested_locks(self):
        with tasks.locked(self.root):
            with patch.object(tasks, "LOCK_SECONDS", .05), self.assertRaises(tasks.TaskError):
                tasks.start(self.root, "other", "prompt")
        self.assertEqual(self.start()["status"], "active")

    def test_terminal_status_closes_coding_window_and_finite_metadata_is_required(self):
        state = self.start()
        cancelled = tasks.update(self.root, state["task_id"], status="cancelled")
        self.assertFalse(cancelled["coding_active"])
        with self.assertRaises(tasks.TaskError):
            tasks.freeze(self.root, state["task_id"])
        with self.assertRaises(tasks.TaskError):
            tasks.update(self.root, state["task_id"], duration=float("nan"))
        self.assertEqual(tasks.load(self.root, state["task_id"])["status"], "cancelled")

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_windows_parent_junction_never_exposes_outside_source_bytes(self):
        outside = self.base / "outside-directory"
        outside.mkdir()
        (outside / "private.py").write_text("PRIVATE_SOURCE_NOT_TO_COPY\n")
        junction = self.root / "junction"
        self.root.resolve().relative_to(self.base.resolve())
        outside.resolve().relative_to(self.base.resolve())
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True)
        if result.returncode:
            self.skipTest("junction creation unavailable")
        try:
            raw, reason = tasks._read_bytes_status(self.root, "junction/private.py")
            self.assertIsNone(raw)
            self.assertEqual(reason, "unsafe_path")
            state = self.start()
            self.assertNotIn("junction/private.py", state["before"]["files"])
            self.assertEqual((outside / "private.py").read_text(), "PRIVATE_SOURCE_NOT_TO_COPY\n")
        finally:
            junction.resolve().relative_to(self.base.resolve())
            junction.rmdir()

    def test_cross_process_duplicate_begin_creates_only_one_snapshot(self):
        code = ("import sys; sys.path.insert(0,sys.argv[1]); from codemap_lib import tasks; "
                "print(tasks.start(sys.argv[2], 'same-session', 'same-prompt')['task_id'])")
        command = [sys.executable, "-c", code, str(SCRIPTS), str(self.root)]
        children = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        outputs = [child.communicate(timeout=25) for child in children]
        self.assertEqual([child.returncode for child in children], [0, 0], outputs)
        self.assertEqual(outputs[0][0].strip(), outputs[1][0].strip())
        self.assertEqual(len(list((self.root / ".codemap" / "tasks").glob("*/state.json"))), 1)

    def test_concurrent_first_start_waits_for_private_ignore_writer(self):
        opened, release, second_progress = threading.Event(), threading.Event(), threading.Event()
        original_open, original_sleep = Path.open, tasks.time.sleep

        @contextlib.contextmanager
        def paused_writer(stream):
            with stream:
                opened.set()
                if not release.wait(5):
                    raise AssertionError("ignore writer was not released")
                yield stream

        def open_with_paused_ignore(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            if path.name == ".gitignore" and args[:1] == ("xb",) and not opened.is_set():
                return paused_writer(stream)
            return stream

        def sleep_while_waiting(seconds):
            if seconds == .025:
                second_progress.set()
            original_sleep(seconds)

        def second_start():
            try:
                return self.start()
            finally:
                # An early failure must wake the test instead of hiding behind
                # a timeout; only a pending future demonstrates lock waiting.
                second_progress.set()

        with patch.object(Path, "open", open_with_paused_ignore), patch.object(tasks.time, "sleep", sleep_while_waiting):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.start)
                try:
                    self.assertTrue(opened.wait(3), "first writer did not create the ignore file")
                    ignore = self.root / ".codemap" / "tasks" / ".gitignore"
                    self.assertEqual(ignore.read_bytes(), b"")
                    second = pool.submit(second_start)
                    self.assertTrue(second_progress.wait(3), "second caller did not reach storage initialization")
                    self.assertFalse(second.done(), "second caller read the unfinished ignore instead of waiting")
                    self.assertEqual(list(ignore.parent.glob("*/state.json")), [])
                finally:
                    release.set()
                first_state, second_state = first.result(timeout=10), second.result(timeout=10)
        self.assertEqual(first_state["task_id"], second_state["task_id"])
        self.assertEqual(ignore.read_bytes(), b"*\n")
        self.assertEqual(len(list(ignore.parent.glob("*/state.json"))), 1)

    def test_invalid_existing_private_ignore_is_not_replaced(self):
        store = self.root / ".codemap" / "tasks"
        store.mkdir(parents=True)
        ignore = store / ".gitignore"
        for raw in (b"", b"!state.json\n", b"*\n" + b" " * 17):
            with self.subTest(raw=raw):
                ignore.write_bytes(raw)
                with self.assertRaisesRegex(tasks.TaskError, "private task ignore file is invalid"):
                    self.start()
                self.assertEqual(ignore.read_bytes(), raw)
                self.assertEqual(list(store.glob("*/state.json")), [])

    @unittest.skipUnless(shutil.which("git"), "git needed for tracked/untracked snapshot fixture")
    def test_git_baseline_copies_dirty_tracked_and_untracked_without_stash(self):
        def git(*args):
            return subprocess.run(["git", "-c", "core.hooksPath=" + str(self.base / "no-hooks"), *args],
                                  cwd=self.root, capture_output=True, check=True)
        git("init")
        git("config", "user.name", "Fixture")
        git("config", "user.email", "fixture@example.invalid")
        git("add", "app.py")
        git("commit", "-m", "Fixture baseline")
        self.source.write_text("def value():\n    return 2\n")
        (self.root / "untracked.py").write_text("def extra():\n    pass\n")
        with patch.object(tasks.pipeline, "git", wraps=tasks.pipeline.git) as calls:
            state = self.start()
        self.assertTrue({"app.py", "untracked.py"} <= set(state["before"]["files"]))
        self.assertEqual((Path(state["before_root"]) / "app.py").read_bytes(), self.source.read_bytes())
        self.assertFalse(any("stash" in call.args for call in calls.call_args_list))
        self.assertNotIn(".codemap", git("status", "--porcelain", "--untracked-files=all").stdout.decode())
        frozen = tasks.freeze(self.root, state["task_id"])
        self.assertFalse(tasks.changed(frozen))
        self.assertTrue(tasks.verify(self.root, state["task_id"]))

    @unittest.skipUnless(shutil.which("git"), "git needed for hidden tracked/untracked source discovery")
    def test_project_installed_codemap_is_excluded_without_hiding_user_policies_or_skills(self):
        subprocess.run(["git", "init", str(self.root)], capture_output=True, check=True)
        for folder in (".claude", ".agents", ".gemini", ".cursor", ".github"):
            path = self.root / folder / "skills" / "codemap" / "scripts" / "tool.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("raise RuntimeError('Installed tool sentinel must never execute')\n")
        retained = [".claude/policies.ts", ".claude/skills/custom/helper.py",
                    ".github/skills/custom/helper.py", "skills/codemap/application.py"]
        for name in retained:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("def application_helper():\n    return 1\n")
        output = self.root / ".codemap" / "generated.py"
        output.parent.mkdir(exist_ok=True)
        output.write_text("raise RuntimeError('Output sentinel must never execute')\n")
        state = self.start()
        self.assertTrue(set(retained) <= set(state["before"]["files"]))
        self.assertFalse(any("skills/codemap/scripts/tool.py" in name for name in state["before"]["files"]))
        self.assertNotIn(".codemap/generated.py", state["before"]["files"])
        frozen = tasks.freeze(self.root, state["task_id"])
        self.assertFalse(tasks.changed(frozen))
        self.assertTrue(tasks.verify(self.root, state["task_id"]))

    def test_plain_project_own_skill_filter_matches_frozen_snapshot_verification(self):
        tool = self.root / ".github" / "skills" / "codemap" / "tool.py"
        user = self.root / ".github" / "skills" / "custom" / "helper.py"
        tool.parent.mkdir(parents=True)
        user.parent.mkdir(parents=True)
        tool.write_text("raise RuntimeError('Installed tool sentinel must never execute')\n")
        user.write_text("def helper():\n    return 1\n")
        state = self.start()
        self.assertNotIn(".github/skills/codemap/tool.py", state["before"]["files"])
        self.assertIn(".github/skills/custom/helper.py", state["before"]["files"])
        frozen = tasks.freeze(self.root, state["task_id"])
        self.assertNotIn(".github/skills/codemap/tool.py", frozen["after"]["files"])
        self.assertTrue(tasks.verify(self.root, state["task_id"]))


if __name__ == "__main__":
    unittest.main()
