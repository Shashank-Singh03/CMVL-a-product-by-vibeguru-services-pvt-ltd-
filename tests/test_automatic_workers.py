"""Owned background processes, cancellation, and frozen-task stage boundaries."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/codemap/scripts"
sys.path.insert(0, str(SCRIPTS))
from codemap_lib import automatic, tasks


class AutomaticWorkersTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="codemap-owned-worker-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "app.py"
        self.source.write_text("raise RuntimeError('never run repository source')\n\ndef answer():\n    return 1\n")
        automatic.configure(self.root, "on", video=False)
        state = tasks.start(self.root, "session", "prompt")
        self.ident = state["task_id"]
        self.source.write_text(self.source.read_text().replace("return 1", "return 2"))
        tasks.freeze(self.root, self.ident)
        self.token = uuid.uuid4().hex
        tasks.update(self.root, self.ident, status="analysis_queued", stage="analysis", worker_token=self.token,
                     worker_started=__import__("time").time(), worker_pid=os.getpid(), settings=dict(automatic.DEFAULTS, video=False))

    def sleeping_process(self):
        process = automatic._spawn([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(lambda: automatic._kill_owned(process) if process.poll() is None else None)
        return process

    def test_real_stage_reads_the_frozen_source_without_running_it(self):
        self.source.write_text(self.source.read_text().replace("return 2", "return 9"))
        # The launcher may not yet have saved the supervisor PID. It must be
        # registered atomically before the real child starts its watchdog.
        tasks.update(self.root, self.ident, worker_pid=None)
        spawn = automatic._spawn
        def checked_spawn(argv):
            running = tasks.load(self.root, self.ident)
            self.assertEqual(running["status"], "analyzing")
            self.assertEqual(running["worker_pid"], os.getpid())
            return spawn(argv)
        with patch.object(automatic, "_spawn", side_effect=checked_spawn):
            self.assertEqual(automatic.worker(self.root, self.ident, "analysis", self.token), 0)
        state = tasks.load(self.root, self.ident)
        self.assertEqual(state["status"], "awaiting_review")
        ir = json.loads((Path(state["out_dir"]) / "ir.json").read_text())
        self.assertTrue(ir["analysis"]["isolated"])
        self.assertEqual(ir["root"], state["work_root"])
        difference = (Path(state["out_dir"]) / "codemap.diff").read_text()
        self.assertIn("+    return 2", difference)
        self.assertNotIn("return 9", difference)
        self.assertIn("return 9", self.source.read_text())

    def test_deadline_kills_only_the_owned_stage_process(self):
        owned, unrelated = self.sleeping_process(), self.sleeping_process()
        with patch.object(automatic, "_spawn", return_value=owned), \
                patch.object(automatic, "_settings", return_value=dict(automatic.DEFAULTS, timeout=.15)):
            self.assertEqual(automatic.worker(self.root, self.ident, "analysis", self.token), 1)
        state = tasks.load(self.root, self.ident)
        self.assertEqual(state["status"], "analysis_failed")
        self.assertEqual(state["error_type"], "TimeoutExpired")
        self.assertIsNotNone(owned.poll())
        self.assertIsNone(unrelated.poll())
        self.assertIn("return 2", self.source.read_text())

    def test_cancellation_is_cooperative_and_cannot_be_overwritten_by_worker(self):
        owned = self.sleeping_process()
        timer = threading.Timer(.2, automatic.cancel, args=(self.root, self.ident))
        self.addCleanup(timer.cancel)
        timer.start()
        with patch.object(automatic, "_spawn", return_value=owned):
            self.assertEqual(automatic.worker(self.root, self.ident, "analysis", self.token), 0)
        timer.join()
        state = tasks.load(self.root, self.ident)
        self.assertEqual(state["status"], "cancelled")
        self.assertIsNotNone(owned.poll())
        self.assertFalse(state.get("map_ready", False))

    def test_orphan_stage_watchdog_records_failure_and_exits_its_own_process(self):
        tasks.update(self.root, self.ident, status="analyzing", worker_pid=2_000_000_000)
        code = ("import sys,time;sys.path.insert(0,sys.argv[1]);"
                "from codemap_lib import automatic;"
                "automatic.pipeline.analyze=lambda *a,**k:time.sleep(30);"
                "automatic.stage_worker(sys.argv[2],sys.argv[3],'analysis',sys.argv[4])")
        process = automatic._spawn([sys.executable, "-c", code, str(SCRIPTS), str(self.root), self.ident, self.token])
        self.addCleanup(lambda: automatic._kill_owned(process) if process.poll() is None else None)
        process.wait(timeout=12)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(tasks.load(self.root, self.ident)["status"], "analysis_failed")
        history = (self.root / ".codemap/index.html").read_text(encoding="utf-8")
        self.assertIn("Map analysis failed", history)

    def test_wrong_job_identity_never_spawns_a_process(self):
        with patch.object(automatic, "_spawn") as spawn:
            self.assertEqual(automatic.worker(self.root, self.ident, "video", self.token), 0)
            self.assertEqual(automatic.worker(self.root, self.ident, "analysis", uuid.uuid4().hex), 0)
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
