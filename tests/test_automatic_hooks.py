"""Automatic hook/control-plane regressions using private, temporary captures.

No Claude session, model, source application, valid stage watchdog or real worker
is started here. The root integration smoke covers real background processing.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "codemap" / "scripts"))
from codemap_lib import automatic, cli, tasks

INSTALL_SPEC = importlib.util.spec_from_file_location("automatic_test_installer", ROOT / "install.py")
install = importlib.util.module_from_spec(INSTALL_SPEC)
INSTALL_SPEC.loader.exec_module(install)


class FinishedProcess:
    pid = 987_654_321

    def poll(self):
        return 0


class AutomaticHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codemap-automatic-hook-tests-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.source = self.root / "app.py"
        self.source.write_text("raise RuntimeError('Source fixture must never execute')\n\ndef value():\n    return 1\n", encoding="utf-8")
        self.env = patch.dict(os.environ, {"CODEMAP_INTERNAL": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def payload(self, **extra):
        return {"session_id": "fixture-session", "cwd": str(self.root), **extra}

    def start(self, **extra):
        automatic.hook("start", self.payload(**extra))
        return tasks.current(self.root, "fixture-session")

    def frozen(self):
        state = self.start(prompt_id="fixture-prompt")
        self.source.write_text("raise RuntimeError('Do not execute')\n\ndef value():\n    return 2\n", encoding="utf-8")
        return tasks.freeze(self.root, state["task_id"])

    def analysis_ready(self, status="awaiting_review"):
        """Saved protocol fixture; snapshots and baseline verification are real."""
        state = self.frozen()
        work, out = Path(state["work_root"]), Path(state["out_dir"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "ir.json").write_text(json.dumps({
            "root": str(work), "analysis": {"isolated": True, "target": str(work)},
            "plan": [{"sid": "fixture-beat"}],
        }), encoding="utf-8")
        (out / "review-request.json").write_text(json.dumps({"analysis_id": "fixture-analysis"}), encoding="utf-8")
        return tasks.update(self.root, state["task_id"], status=status, analysis_id="fixture-analysis", map_ready=True)

    def project_hook_settings(self, *, kind="command", command=None):
        script = self.root / ".claude/skills/codemap/scripts/codemap.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("# Installer identity fixture; never executed.\n", encoding="utf-8")
        hooks = {name: [{"hooks": [{"type": kind, "command": command or install._hook_command(script, action)}]}]
                 for name, (action, _) in install.AUTO_EVENTS.items()}
        settings = self.root / ".claude/settings.local.json"
        settings.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
        return script

    @unittest.skipUnless(shutil.which("git"), "Git fixture")
    def test_no_op_turn_skips_instead_of_explaining_an_old_commit(self):
        # No user/global Git hooks or templates may run, even in the fixture.
        empty = self.base / "empty-git-hooks-and-template"
        empty.mkdir()
        environment = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        def git(*args):
            subprocess.run(["git", "-c", "core.hooksPath=" + str(empty), "-C", str(self.root), *args],
                           check=True, capture_output=True, env=environment, timeout=15)
        git("init", "--template=" + str(empty))
        git("add", "app.py")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "Existing change before this turn")
        with patch.object(automatic, "_spawn") as spawn:
            started = self.start(prompt_id="no-op-turn")
            response = automatic.hook("stop", self.payload(stop_hook_active=False))
        state = tasks.load(self.root, started["task_id"])
        self.assertEqual(response, {})
        self.assertEqual(state["status"], "skipped")
        self.assertFalse(tasks.changed(state))
        self.assertFalse(state["coding_active"])
        spawn.assert_not_called()

    def test_concurrent_duplicate_stops_spawn_and_handoff_only_once(self):
        started = self.start(prompt_id="one-turn", prompt="PRIVATE-PROMPT-MUST-NOT-BE-STORED")
        self.source.write_text("def value():\n    return 2\n", encoding="utf-8")
        barrier = threading.Barrier(2)
        original_current = tasks.current
        def same_initial_state(*args):
            state = original_current(*args)
            barrier.wait(timeout=10)
            return state
        with patch.object(tasks, "current", side_effect=same_initial_state), \
                patch.object(automatic, "_spawn", return_value=FinishedProcess()) as spawn:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: automatic.hook("stop", self.payload(stop_hook_active=False)), range(2)))
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(sum(r.get("decision") == "block" for r in responses), 1)
        self.assertEqual(sum(r == {} for r in responses), 1)
        state = tasks.load(self.root, started["task_id"])
        self.assertTrue(state["handoff_sent"])
        self.assertEqual(state["status"], "analysis_queued")
        self.assertFalse(state["coding_active"])
        self.assertNotIn("PRIVATE-PROMPT", json.dumps(state))
        self.assertNotIn("PRIVATE-PROMPT", json.dumps(responses))
        self.assertEqual(automatic.hook("stop", self.payload(stop_hook_active=True)), {})

    def test_no_event_id_does_not_coalesce_legitimate_identical_new_turns(self):
        # Claude versions without an event ID can deliver an identical new turn.
        payload = {"prompt": "Do the same thing", "transcript_path": str(self.base / "not-created.jsonl")}
        first = self.start(**payload)
        second = self.start(**payload)
        self.assertNotEqual(first["task_id"], second["task_id"])
        self.assertEqual(tasks.load(self.root, first["task_id"])["status"], "interrupted")
        self.assertEqual(tasks.current(self.root, "fixture-session")["task_id"], second["task_id"])

    def test_project_owned_hooks_win_over_global_for_each_event(self):
        script = self.project_hook_settings()
        global_script = self.base / "mock-home/.claude/skills/codemap/scripts/codemap.py"
        with patch.object(automatic, "_entry", return_value=str(global_script)):
            self.assertEqual(automatic.hook("start", self.payload()), {})
            self.assertIsNone(tasks.current(self.root, "fixture-session"))
        with patch.object(automatic, "_entry", return_value=str(script)):
            started = self.start(prompt_id="project-owned-turn")
        self.source.write_text("def value():\n    return 3\n", encoding="utf-8")
        with patch.object(automatic, "_entry", return_value=str(global_script)), patch.object(automatic, "_spawn") as spawn:
            self.assertEqual(automatic.hook("stop", self.payload()), {})
            self.assertEqual(automatic.hook("abort", self.payload()), {})
            spawn.assert_not_called()
        self.assertEqual(tasks.load(self.root, started["task_id"])["status"], "active")
        with patch.object(automatic, "_entry", return_value=str(script)), \
                patch.object(automatic, "_spawn", return_value=FinishedProcess()) as spawn:
            self.assertEqual(automatic.hook("stop", self.payload())["decision"], "block")
            self.assertEqual(spawn.call_count, 1)

    def test_noncommand_handler_cannot_suppress_global_capture(self):
        self.project_hook_settings(kind="prompt")
        with patch.object(automatic, "_entry", return_value=str(self.base / "global-codemap.py")):
            started = self.start(prompt_id="actual-command-turn")
        self.assertIsNotNone(started)
        self.assertEqual(started["status"], "active")

    def test_disabled_auto_still_closes_active_session_on_abort(self):
        started = self.start(prompt_id="interrupted-turn")
        automatic.configure(self.root, "off")
        self.assertEqual(automatic.hook("abort", self.payload(reason="prompt_input_exit")), {})
        interrupted = tasks.load(self.root, started["task_id"])
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertFalse(interrupted["coding_active"])
        self.assertIsNone(interrupted["after"])

    def test_malformed_hook_stdin_fails_open_without_echoing_source_or_prompt(self):
        secret = "PRIVATE-SOURCE-AND-PROMPT-CANARY"
        for raw in ('{"prompt":"' + secret, json.dumps({"cwd": secret, "session_id": "fixture"})):
            with self.subTest(raw=raw), patch.object(sys, "stdin", io.StringIO(raw)):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    self.assertEqual(cli.main(["hook", "start"]), 0)
                self.assertIn("systemMessage", json.loads(out.getvalue()))
                self.assertNotIn(secret, out.getvalue() + err.getvalue())
        with patch.object(sys, "stdin", io.StringIO(json.dumps(self.payload()))), \
                patch.object(automatic, "hook", side_effect=RuntimeError(secret)):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(cli.main(["hook", "start"]), 0)
            self.assertIn("systemMessage", json.loads(out.getvalue()))
            self.assertNotIn(secret, out.getvalue() + err.getvalue())

    def test_invalid_configuration_preserves_previous_bytes(self):
        automatic.configure(self.root, "on", timeout=37, video=False)
        path = self.root / ".codemap/config.json"
        original = path.read_bytes()
        for values in ({"timeout": 0}, {"budget": True}, {"jobs": 1.5}, {"minutes": float("nan")},
                       {"tts": "cmd:run-untrusted-command"}, {"unexpected": "setting"}):
            with self.subTest(values=values), self.assertRaises(automatic.AutomaticError):
                automatic.configure(self.root, "on", **values)
            self.assertEqual(path.read_bytes(), original)
        path.write_bytes(b'{"PRIVATE-CORRUPT-CONFIG":')
        malformed = path.read_bytes()
        with self.assertRaises(automatic.AutomaticError):
            automatic.configure(self.root, "off")
        self.assertEqual(path.read_bytes(), malformed)

    def test_task_settings_are_pinned_when_project_configuration_changes(self):
        automatic.configure(self.root, "on", video=True, timeout=23, tts="os")
        started = self.start(prompt_id="pin-configuration")
        automatic.configure(self.root, "off", video=False, timeout=59, tts="none")
        saved = tasks.load(self.root, started["task_id"])
        self.assertTrue(saved["settings"]["video"])
        self.assertEqual(automatic._settings(saved)["timeout"], 23)
        self.assertEqual(automatic._settings(saved)["tts"], "os")
        self.assertFalse(automatic.config(self.root)["enabled"])
        self.assertFalse(automatic.config(self.root)["video"])

    def test_wrong_stage_token_or_status_exits_before_stage_processing(self):
        state = self.frozen()
        token = "a" * 32
        tasks.update(self.root, state["task_id"], status="analyzing", stage="analysis", worker_token=token)
        guarded_threading = types.SimpleNamespace(Event=threading.Event,
            Thread=Mock(side_effect=AssertionError("Never start an in-process watchdog")))
        with patch.object(automatic, "threading", guarded_threading), \
                patch.object(automatic.pipeline, "analyze") as analyze, patch.object(automatic.pipeline, "video") as video:
            self.assertNotEqual(automatic.stage_worker(self.root, state["task_id"], "video", token), 0)
            self.assertNotEqual(automatic.stage_worker(self.root, state["task_id"], "analysis", "b" * 32), 0)
            tasks.update(self.root, state["task_id"], status="analysis_queued")
            self.assertNotEqual(automatic.stage_worker(self.root, state["task_id"], "analysis", token), 0)
            analyze.assert_not_called()
            video.assert_not_called()

    def test_supervisor_rejects_forged_fields_stage_and_token(self):
        frozen = self.frozen()
        baseline_settings = frozen["settings"]
        for case in ("extra-field", "wrong-stage", "wrong-token"):
            with self.subTest(case=case):
                token = {"extra-field": "a", "wrong-stage": "b", "wrong-token": "c"}[case] * 32
                tasks.update(self.root, frozen["task_id"], status="analysis_queued", stage="analysis", worker_token=token)
                def forged_child(_argv):
                    fields = {"status": "awaiting_review", "map_ready": True,
                              "analysis_id": "forged-analysis", "parse_error_count": 0}
                    if case == "extra-field":
                        fields["settings"] = {"tts": "cmd:untrusted"}
                    result = {"ok": True, "fields": fields,
                              "stage": "video" if case == "wrong-stage" else "analysis",
                              "token": "d" * 32 if case == "wrong-token" else token}
                    (Path(frozen["task_dir"]) / ("stage-analysis-" + token + ".json")).write_text(json.dumps(result), encoding="utf-8")
                    return FinishedProcess()
                with patch.object(automatic, "_spawn", side_effect=forged_child):
                    self.assertNotEqual(automatic.worker(self.root, frozen["task_id"], "analysis", token), 0)
                rejected = tasks.load(self.root, frozen["task_id"])
                self.assertEqual(rejected["status"], "analysis_failed")
                self.assertEqual(rejected["error_type"], "InvalidWorkerResult")
                self.assertEqual(rejected["settings"], baseline_settings)
                self.assertNotEqual(rejected.get("analysis_id"), "forged-analysis")
                self.assertNotEqual(rejected.get("map_ready"), True)
                self.assertTrue(tasks.verify(self.root, frozen["task_id"]))

    def test_cancellation_while_supervisor_waits_cannot_be_overwritten(self):
        frozen = self.frozen()
        token = "e" * 32
        tasks.update(self.root, frozen["task_id"], status="analysis_queued", stage="analysis", worker_token=token)
        process = Mock(pid=987_654_321)
        process.poll.return_value = None
        def cancel_on_child_start(_argv):
            automatic.cancel(self.root, frozen["task_id"])
            return process
        with patch.object(automatic, "_spawn", side_effect=cancel_on_child_start), \
                patch.object(automatic, "_kill_owned") as kill:
            automatic.worker(self.root, frozen["task_id"], "analysis", token)
        kill.assert_called_once_with(process)
        self.assertEqual(tasks.load(self.root, frozen["task_id"])["status"], "cancelled")

    def test_missing_or_empty_mp4_is_not_reported_as_ready(self):
        state = self.frozen()
        state = tasks.update(self.root, state["task_id"], status="complete", video_status="ready", map_ready=True)
        out = Path(state["out_dir"])
        (out / "codemap.html").write_text("<!doctype html><title>Fixture map</title>", encoding="utf-8")
        self.assertIn("map", automatic.summary(state))
        self.assertNotIn("video", automatic.summary(state))
        (out / "codemap.mp4").write_bytes(b"")
        self.assertNotIn("video", automatic.summary(state))

    def test_dead_supervisor_refresh_preserves_capture_and_allows_retry(self):
        frozen = self.frozen()
        old_token = "f" * 32
        tasks.update(self.root, frozen["task_id"], status="analyzing", stage="analysis", worker_token=old_token,
                     worker_pid=987_654_321, worker_started=time.time() - 10)
        with patch.object(automatic, "_alive", return_value=False):
            recovered = automatic.refresh(self.root, frozen["task_id"])
        self.assertEqual(recovered["status"], "analysis_failed")
        self.assertEqual(recovered["after"], frozen["after"])
        self.assertTrue(tasks.verify(self.root, frozen["task_id"]))
        with patch.object(automatic, "_spawn", return_value=FinishedProcess()) as spawn:
            retried = automatic.retry(self.root, frozen["task_id"], "analysis")
        self.assertEqual(retried["status"], "analysis_queued")
        self.assertEqual(spawn.call_count, 1)
        self.assertNotEqual(tasks.load(self.root, frozen["task_id"])["worker_token"], old_token)

    def test_review_input_is_pinned_before_agent_edits_the_output_proposal(self):
        state = self.analysis_ready()
        out = Path(state["out_dir"])
        proposal = {"version": 1, "analysis_id": "fixture-analysis", "reviewed_files": ["app.py"],
                    "symbols": [], "edges": [], "uncertain": []}
        (out / "review.json").write_text(json.dumps(proposal), encoding="utf-8")
        with patch.object(automatic, "_spawn", return_value=FinishedProcess()):
            queued = automatic.review_task(self.root, state["task_id"], wait=0)
        self.assertEqual(queued["status"], "review_queued")
        saved = tasks.load(self.root, state["task_id"])
        pinned = Path(saved["task_dir"]) / ("input-" + saved["worker_token"] + ".json")
        (out / "review.json").write_text('{"agent_edited_after_queue": true}', encoding="utf-8")
        self.assertEqual(json.loads(pinned.read_text(encoding="utf-8")), proposal)
        self.assertEqual(hashlib.sha256(pinned.read_bytes()).hexdigest(), saved["input_sha256"])

    def test_labels_input_is_pinned_before_agent_edits_the_output_labels(self):
        state = self.analysis_ready(status="awaiting_labels")
        out = Path(state["out_dir"])
        labels = {"why": "Fixture function now returns the revised value.", "beats": {"fixture-beat": "The function result changed."}}
        (out / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
        with patch.object(automatic, "_spawn", return_value=FinishedProcess()):
            queued = automatic.finish(self.root, state["task_id"], wait=0)
        self.assertEqual(queued["status"], "render_queued")
        saved = tasks.load(self.root, state["task_id"])
        pinned = Path(saved["task_dir"]) / ("input-" + saved["worker_token"] + ".json")
        (out / "labels.json").write_text('{"why":"Later edit"}', encoding="utf-8")
        self.assertEqual(json.loads(pinned.read_text(encoding="utf-8")), labels)
        self.assertEqual(hashlib.sha256(pinned.read_bytes()).hexdigest(), saved["input_sha256"])


if __name__ == "__main__":
    unittest.main()
