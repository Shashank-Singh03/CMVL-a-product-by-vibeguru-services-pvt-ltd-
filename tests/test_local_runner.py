"""Bounded corpus stages, resumable checkpoints, and read-only target guards."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))
spec = importlib.util.spec_from_file_location("codemap_local_runner", ROOT / "benchmarks" / "run_local_repos.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class LocalRunnerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codemap-runner-tests-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.target = self.base / "project"
        self.target.mkdir()
        self.source = self.target / "app.py"
        self.source.write_text("raise RuntimeError('This fixture must never execute')\n\ndef hello():\n    return 7\n", encoding="utf-8")
        self.out = self.base / "reports"
        self.manifest = self.base / "manifest.json"
        self.manifest.write_text(json.dumps([{"id": "tiny", "path": str(self.target)}]), encoding="utf-8")
        self.code = ({"app.py": "app-version-1"}, {"runner.py": "runner-version-1"})

    def main(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return runner.main(["--manifest", str(self.manifest), "--out", str(self.out), *args])

    def report(self):
        return json.loads((self.out / "results.json").read_text(encoding="utf-8"))

    def test_real_isolated_stage_succeeds_and_only_prepares_pending_review(self):
        original = self.source.read_bytes()
        result = runner.run_map(self.target, self.out / "maps" / "tiny", timeout=10,
                                repo_mode=True, structural_review=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["symbols"], 1)
        self.assertEqual(result["review_status"], "pending")
        self.assertEqual(result["timeout_seconds"], 10)
        self.assertTrue((self.out / "maps" / "tiny" / "review-request.json").is_file())
        self.assertEqual(self.source.read_bytes(), original)

    def test_timeout_terminates_owned_test_worker_and_returns_structured_failure(self):
        started = time.perf_counter()
        with patch.object(runner, "_worker_argv", return_value=[sys.executable, "-c", "import time; time.sleep(20)"]):
            result = runner.run_map(self.target, self.out / "maps" / "tiny", timeout=.1, repo_mode=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "TimeoutExpired")
        self.assertEqual(result["timeout_seconds"], .1)
        self.assertLess(time.perf_counter() - started, 8)
        self.assertFalse((self.out / "maps" / "tiny" / "graph.json").exists())

    def test_worker_failure_never_copies_stderr_into_reports(self):
        marker = "PRIVATE_SOURCE_AND_CREDENTIAL_MARKER"
        command = [sys.executable, "-c", "import sys; sys.stderr.write('" + marker + "'); sys.exit(9)"]
        with patch.object(runner, "_worker_argv", return_value=command):
            result = runner.run_map(self.target, self.out / "maps" / "tiny", timeout=5)
        self.assertFalse(result["ok"])
        self.assertEqual(result["worker_exit_code"], 9)
        self.assertNotIn(marker, json.dumps(result))

    def test_default_execution_remains_in_process_and_errors_are_source_free(self):
        sentinel = {"ok": True}
        with patch.object(runner, "_run_map_inprocess", return_value=sentinel) as direct:
            self.assertIs(runner.run_map(self.target, self.out, repo_mode=True), sentinel)
            direct.assert_called_once_with(self.target.resolve(), self.out.resolve(), repo_mode=True)
        with patch.object(runner.pipeline, "analyze", side_effect=RuntimeError("UNTRUSTED_ERROR_CONTENT")):
            result = runner.run_map(self.target, self.out)
        self.assertEqual(result["error_type"], "RuntimeError")
        self.assertNotIn("UNTRUSTED_ERROR_CONTENT", json.dumps(result))

    def test_inventory_streaming_preserves_original_hash_and_digest_schema(self):
        content = b"# " + b"x" * (2 * 1024 * 1024) + b"\n"
        self.source.write_bytes(content)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file reads prohibited")):
            inventory = runner.inventory(self.target)
        expected = {"app.py": hashlib.sha256(content).hexdigest()}
        self.assertEqual(inventory["source_sha256"], expected)
        self.assertEqual(inventory["source_bytes"], {"app.py": len(content)})
        self.assertEqual(inventory["digest"], hashlib.sha256(json.dumps(expected, sort_keys=True).encode("utf-8")).hexdigest())
        self.assertEqual(inventory["read_errors"], [])

    def test_inventory_read_errors_are_bounded_and_cannot_claim_unchanged(self):
        with patch.object(runner, "file_sha256", side_effect=PermissionError("PRIVATE_ERROR_CONTENT")):
            inventory = runner.inventory(self.target)
        self.assertEqual(inventory["read_errors"], [{"file": "app.py", "error_type": "PermissionError"}])
        self.assertNotIn("PRIVATE_ERROR_CONTENT", json.dumps(inventory))
        self.assertFalse(runner._inventory_matches(inventory, inventory))

    def test_entire_manifest_guard_precedes_all_analysis_and_output_creation(self):
        guarded = self.base / "guarded"
        guarded.mkdir()
        self.out = guarded / "reports"
        self.manifest.write_text(json.dumps([{"id": "safe-first", "path": str(self.target)},
                                            {"id": "blocked-second", "path": str(guarded)}]))
        with patch.object(runner, "run_map") as analysis, self.assertRaises(SystemExit) as error:
            self.main()
        self.assertEqual(error.exception.code, 2)
        analysis.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_missing_or_file_roots_and_duplicate_ids_are_rejected_before_writes(self):
        manifests = [[{"id": "tiny", "path": str(self.base / "missing")}],
                     [{"id": "tiny", "path": str(self.source)}],
                     [{"id": "tiny", "path": str(self.target)}, {"id": "tiny", "path": str(self.target)}]]
        for items in manifests:
            with self.subTest(items=items):
                self.manifest.write_text(json.dumps(items))
                with patch.object(runner, "run_map") as analysis, self.assertRaises(SystemExit):
                    self.main()
                analysis.assert_not_called()
                self.assertFalse(self.out.exists())

    @unittest.skipUnless(shutil.which("git"), "git is needed for checkout-parent guard")
    def test_subfolder_target_cannot_write_output_inside_its_parent_checkout(self):
        subprocess.run(["git", "init", str(self.target)], capture_output=True, check=True)
        subfolder = self.target / "src"
        subfolder.mkdir()
        self.out = self.target / "reports"
        self.manifest.write_text(json.dumps([{"id": "tiny", "path": str(subfolder)}]))
        with patch.object(runner, "run_map") as analysis, self.assertRaises(SystemExit):
            self.main()
        analysis.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_resume_reuses_complete_results_when_app_and_inputs_still_match(self):
        with patch.object(runner, "_fingerprints", return_value=self.code):
            self.assertEqual(self.main("--review-requests"), 0)
            with patch.object(runner, "run_map", side_effect=AssertionError("should reuse")):
                self.assertEqual(self.main("--resume", "--review-requests", "--timeout", "120"), 0)
        project = self.report()["projects"][0]
        self.assertTrue(project["completed"])
        self.assertTrue(project["resumed"])
        self.assertTrue(project["input_unchanged"])
        self.assertEqual(project["map"]["review_status"], "pending")
        self.assertEqual(project["audit"]["review_status"], "not_requested")

    def test_resume_invalidates_changed_source_even_outside_a_declaration(self):
        with patch.object(runner, "_fingerprints", return_value=self.code):
            self.assertEqual(self.main(), 0)
            before = self.report()["projects"][0]["before"]["digest"]
            self.source.write_text(self.source.read_text() + "\nCONFIG = 3\n")
            with patch.object(runner, "run_map", wraps=runner.run_map) as stages:
                self.assertEqual(self.main("--resume"), 0)
                self.assertEqual(stages.call_count, 2)
        project = self.report()["projects"][0]
        self.assertFalse(project["resumed"])
        self.assertNotEqual(project["before"]["digest"], before)

    def test_resume_invalidates_app_or_runner_and_review_mode_changes(self):
        changes = [({"app.py": "app-version-2"}, self.code[1]),
                   (self.code[0], {"runner.py": "runner-version-2"})]
        for fingerprints in changes:
            with self.subTest(fingerprints=fingerprints):
                with patch.object(runner, "_fingerprints", return_value=self.code):
                    self.assertEqual(self.main(), 0)
                with patch.object(runner, "_fingerprints", return_value=fingerprints), patch.object(runner, "run_map", wraps=runner.run_map) as stages:
                    self.assertEqual(self.main("--resume"), 0)
                    self.assertEqual(stages.call_count, 2)
                self.assertFalse(self.report()["projects"][0]["resumed"])
        with patch.object(runner, "_fingerprints", return_value=self.code):
            self.assertEqual(self.main(), 0)
            with patch.object(runner, "run_map", wraps=runner.run_map) as stages:
                self.assertEqual(self.main("--resume", "--review-requests"), 0)
                self.assertEqual(stages.call_count, 2)
        self.assertEqual(self.report()["projects"][0]["map"]["review_status"], "pending")

    def test_resume_checks_revision_status_and_missing_artifacts(self):
        with patch.object(runner, "_fingerprints", return_value=self.code):
            self.assertEqual(self.main(), 0)
            project = self.report()["projects"][0]
            current = runner.inventory(self.target)
            self.assertTrue(runner._can_resume(project, self.target, current, self.out))
            for key in ("status", "revision", "digest"):
                changed = dict(current, **{key: "changed"})
                self.assertFalse(runner._can_resume(project, self.target, changed, self.out))
            (self.out / "maps" / "tiny" / "codemap.html").unlink()
            with patch.object(runner, "run_map", wraps=runner.run_map) as stages:
                self.assertEqual(self.main("--resume"), 0)
                self.assertEqual(stages.call_count, 2)

    def test_interruption_checkpoints_stage_results_and_resume_retries_partial_project(self):
        original = runner.run_map
        calls = []
        def interrupt_second(root, out, **options):
            calls.append(out)
            if len(calls) == 2:
                raise KeyboardInterrupt
            return original(root, out, **options)
        with patch.object(runner, "_fingerprints", return_value=self.code):
            with patch.object(runner, "run_map", side_effect=interrupt_second):
                self.assertEqual(self.main(), 130)
            report = self.report()
            self.assertEqual(report["status"], "interrupted")
            self.assertTrue(report["projects"][0]["map"]["ok"])
            self.assertIsNone(report["projects"][0]["audit"])
            self.assertFalse(report["projects"][0]["completed"])
            self.assertTrue((self.out / "report.md").is_file())
            with patch.object(runner, "run_map", wraps=original) as stages:
                self.assertEqual(self.main("--resume"), 0)
                self.assertEqual(stages.call_count, 2)

    def test_real_timeout_does_not_block_remaining_stages_or_projects_and_is_retried(self):
        second = self.base / "second-project"
        second.mkdir()
        (second / "main.py").write_text("def other():\n    return 2\n")
        self.manifest.write_text(json.dumps([{"id": "tiny", "path": str(self.target)},
                                            {"id": "second", "path": str(second)}]))
        original = runner.run_map
        calls = []
        def timeout_first(root, out, **options):
            calls.append(out)
            if len(calls) == 1:
                with patch.object(runner, "_worker_argv", return_value=[sys.executable, "-c", "import time; time.sleep(20)"]):
                    return original(root, out, timeout=.1, repo_mode=True)
            return original(root, out, **options)
        with patch.object(runner, "_fingerprints", return_value=self.code):
            with patch.object(runner, "run_map", side_effect=timeout_first):
                self.assertEqual(self.main(), 1)
            report = self.report()
            self.assertEqual(len(calls), 4)
            self.assertEqual(report["projects"][0]["map"]["error_type"], "TimeoutExpired")
            self.assertTrue(report["projects"][1]["map"]["ok"])
            self.assertTrue(all(p["completed"] and p["input_unchanged"] for p in report["projects"]))
            with patch.object(runner, "run_map", wraps=original) as stages:
                self.assertEqual(self.main("--resume"), 0)
                self.assertEqual(stages.call_count, 2, "only the failed project should be retried")
        self.assertTrue(self.report()["projects"][1]["resumed"])

    def test_invalid_deadlines_are_rejected_before_output_creation(self):
        for value in ("-1", "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                self.main("--timeout", value)
            self.assertFalse(self.out.exists())

    def second_project(self):
        second = self.base / "second-project"
        second.mkdir()
        (second / "main.py").write_text("def other():\n    return 2\n")
        self.manifest.write_text(json.dumps([{"id": "tiny", "path": str(self.target)},
                                            {"id": "second", "path": str(second)}]))
        return second

    def move_owned_target(self):
        destination = self.base / "moved-project"
        # Both sides of this simulated layout change are disposable fixtures.
        self.target.resolve().relative_to(self.base.resolve())
        destination.resolve().relative_to(self.base.resolve())
        self.target.rename(destination)
        return destination

    def test_directory_disappearing_after_manifest_validation_is_checkpointed_and_skipped(self):
        second = self.second_project()
        original_validation = runner._validated_projects
        def disappear(items, out):
            projects = original_validation(items, out)
            self.move_owned_target()
            return projects
        with patch.object(runner, "_fingerprints", return_value=self.code), \
             patch.object(runner, "_validated_projects", side_effect=disappear), \
             patch.object(runner, "run_map", wraps=runner.run_map) as stages:
            self.assertEqual(self.main(), 1)
        report = self.report()
        first, following = report["projects"]
        self.assertEqual(report["status"], "complete")
        self.assertEqual(first["status"], "unavailable")
        self.assertEqual(first["integrity"], "unknown")
        self.assertIsNone(first["input_unchanged"])
        self.assertFalse(first["completed"])
        self.assertIsNone(first["map"])
        self.assertEqual(first["inventory_errors"][0]["phase"], "before")
        self.assertTrue(following["completed"] and following["input_unchanged"])
        self.assertEqual(stages.call_count, 2)
        self.assertTrue(all(call.args[0] == second for call in stages.call_args_list))
        markdown = (self.out / "report.md").read_text(encoding="utf-8")
        self.assertIn("Unavailable input inventories", markdown)
        self.assertIn("unknown", markdown)
        self.assertIn("not run because the input inventory was unavailable", markdown)

    def test_directory_disappearing_after_stages_keeps_results_and_continues(self):
        self.second_project()
        original_stage = runner.run_map
        first_calls = []
        def disappear_after_audit(root, out, **options):
            result = original_stage(root, out, **options)
            if root == self.target:
                first_calls.append(out)
                if len(first_calls) == 2:
                    self.move_owned_target()
            return result
        with patch.object(runner, "_fingerprints", return_value=self.code), \
             patch.object(runner, "run_map", side_effect=disappear_after_audit):
            self.assertEqual(self.main(), 1)
        first, following = self.report()["projects"]
        self.assertTrue(first["map"]["ok"] and first["audit"]["ok"])
        self.assertTrue((self.out / "maps" / "tiny" / "codemap.html").is_file())
        self.assertEqual(first["status"], "unverified")
        self.assertEqual(first["integrity"], "unknown")
        self.assertIsNone(first["input_unchanged"])
        self.assertFalse(first["completed"])
        self.assertEqual(first["inventory_errors"][0]["phase"], "after")
        self.assertTrue(following["completed"] and following["input_unchanged"])

    def test_inventory_io_failures_before_and_after_do_not_leak_errors_or_stop_corpus(self):
        self.second_project()
        original_inventory = runner.inventory
        for failed_phase in ("before", "after"):
            with self.subTest(phase=failed_phase):
                calls = []
                def unavailable(root):
                    if root == self.target:
                        calls.append(root)
                        if len(calls) == (1 if failed_phase == "before" else 2):
                            raise PermissionError("PRIVATE_ERROR_SOURCE_CONTENT")
                    return original_inventory(root)
                with patch.object(runner, "_fingerprints", return_value=self.code), \
                     patch.object(runner, "inventory", side_effect=unavailable):
                    self.assertEqual(self.main(), 1)
                report = self.report()
                first, following = report["projects"]
                self.assertEqual(first["inventory_errors"][0]["phase"], failed_phase)
                self.assertEqual(first["inventory_errors"][0]["error_type"], "PermissionError")
                self.assertIsNone(first["input_unchanged"])
                self.assertTrue(following["completed"] and following["input_unchanged"])
                self.assertNotIn("PRIVATE_ERROR_SOURCE_CONTENT", json.dumps(report))
                self.assertNotIn("PRIVATE_ERROR_SOURCE_CONTENT", (self.out / "report.md").read_text())

    def test_partial_source_read_failures_are_unknown_before_or_after_not_changed(self):
        self.second_project()
        original_inventory = runner.inventory
        for failed_phase in ("before", "after"):
            with self.subTest(phase=failed_phase):
                calls = []
                def partial(root):
                    snapshot = original_inventory(root)
                    if root == self.target:
                        calls.append(root)
                        if len(calls) == (1 if failed_phase == "before" else 2):
                            snapshot["read_errors"] = [{"file": "app.py", "error_type": "PermissionError"}]
                    return snapshot
                with patch.object(runner, "_fingerprints", return_value=self.code), \
                     patch.object(runner, "inventory", side_effect=partial):
                    self.assertEqual(self.main(), 1)
                first, following = self.report()["projects"]
                self.assertEqual(first["integrity"], "unknown")
                self.assertIsNone(first["input_unchanged"])
                self.assertFalse(first["completed"])
                self.assertEqual(first["inventory_errors"][0]["error_type"], "SourceReadError")
                self.assertEqual(first["inventory_errors"][0]["read_error_count"], 1)
                self.assertTrue(following["completed"] and following["input_unchanged"])
                if failed_phase == "before":
                    self.assertIsNone(first["map"])
                else:
                    self.assertTrue(first["map"]["ok"] and first["audit"]["ok"])

    def test_unavailable_project_is_retried_on_resume_after_directory_returns(self):
        self.second_project()
        original_validation = runner._validated_projects
        destinations = []
        def disappear(items, out):
            projects = original_validation(items, out)
            destinations.append(self.move_owned_target())
            return projects
        with patch.object(runner, "_fingerprints", return_value=self.code):
            with patch.object(runner, "_validated_projects", side_effect=disappear):
                self.assertEqual(self.main(), 1)
            destinations[0].resolve().relative_to(self.base.resolve())
            self.target.resolve().relative_to(self.base.resolve())
            destinations[0].rename(self.target)
            with patch.object(runner, "run_map", wraps=runner.run_map) as stages:
                self.assertEqual(self.main("--resume"), 0)
                self.assertEqual(stages.call_count, 2)
        first, following = self.report()["projects"]
        self.assertFalse(first["resumed"])
        self.assertTrue(first["completed"] and first["input_unchanged"])
        self.assertEqual(first["inventory_errors"], [])
        self.assertTrue(following["resumed"])

    def test_git_inventory_without_status_is_unverified_but_unborn_revision_is_allowed(self):
        snapshot = runner.inventory(self.target)
        snapshot.update(git=True, revision=None, status="")
        with patch.object(runner, "inventory", return_value=snapshot):
            self.assertIsNone(runner.inventory_snapshot(self.target, "before")[1])
        snapshot["status"] = None
        with patch.object(runner, "inventory", return_value=snapshot):
            _, issue = runner.inventory_snapshot(self.target, "after")
        self.assertEqual(issue["error_type"], "GitStatusUnavailable")
        self.assertEqual(issue["phase"], "after")

    def test_directory_disappearing_during_inventory_cannot_return_a_valid_snapshot(self):
        original_inventory = runner.inventory
        def disappear(root):
            snapshot = original_inventory(root)
            self.move_owned_target()
            return snapshot
        with patch.object(runner, "inventory", side_effect=disappear):
            snapshot, issue = runner.inventory_snapshot(self.target, "after")
        self.assertIsNone(snapshot)
        self.assertEqual(issue["phase"], "after")
        self.assertEqual(issue["error_type"], "FileNotFoundError")


if __name__ == "__main__":
    unittest.main()
