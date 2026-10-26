"""Actual pipeline acceptance for task snapshots nested inside live Git trees."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "codemap" / "scripts"))
from codemap_lib import pipeline, tasks
from codemap_lib.render import html_payload


@unittest.skipUnless(shutil.which("git"), "git is needed to prove snapshot/live checkout separation")
class FrozenTaskPipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codemap-isolated-pipeline-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "actual-project"
        self.root.mkdir()
        self.write("app.py", "raise RuntimeError('Target source must never execute')\n\n"
                   "from helpers import removed\n\nBEFORE_ONLY = 1\n\ndef caller():\n    return removed()\n")
        self.write("helpers.py", "def removed():\n    return 1\n")
        self.git("init")
        self.git("config", "user.name", "Task fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("add", ".")
        self.git("commit", "-m", "Initial fixture")

    def git(self, *args):
        return subprocess.run(["git", "-c", "core.hooksPath=" + str(self.base / "no-hooks"), *args],
                              cwd=self.root, capture_output=True, check=True).stdout.decode("utf-8", errors="replace")

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def start(self, prompt="prompt-one"):
        return tasks.start(self.root, "fixture-session", prompt)

    def analyze(self, frozen):
        return pipeline.analyze(frozen["work_root"], out=frozen["out_dir"], isolated=True,
                                isolated_files=sorted(frozen["after"]["files"]), project_name=self.root.name,
                                tier="video", structural_review=True, log=lambda _: None)

    def empty_review(self, frozen):
        out = Path(frozen["out_dir"])
        request = json.loads((out / "review-request.json").read_text(encoding="utf-8"))
        patch = {"version": 1, "analysis_id": request["analysis_id"], "reviewed_files": [],
                 "symbols": [], "edges": [], "uncertain": []}
        (out / "review.json").write_text(json.dumps(patch), encoding="utf-8")
        return pipeline.review(out, log=lambda _: None)

    def test_dirty_tracked_untracked_and_deleted_callee_keep_task_relative_identity(self):
        # This dirty state existed before the task and must become its baseline.
        original = (self.root / "app.py").read_text()
        self.write("app.py", original.replace("BEFORE_ONLY = 1", "BEFORE_ONLY = 7"))
        self.write("scratch.py", "def scratch():\n    return 3\n")
        state = self.start()
        self.write("app.py", (self.root / "app.py").read_text().replace("return removed()", "return removed() + 1"))
        self.write("scratch.py", "def scratch():\n    return 4\n")
        self.write("fresh.py", "def fresh():\n    return 5\n")
        (self.root / "helpers.py").unlink()
        frozen = tasks.freeze(self.root, state["task_id"])
        ir = self.analyze(frozen)
        self.assertEqual(ir["root"], frozen["work_root"])
        self.assertNotEqual(ir["root"], str(self.root))
        self.assertEqual(ir["sub"], "")
        self.assertEqual(ir["repo"], self.root.name)
        self.assertEqual(ir["scope"]["mode"], "diff")
        self.assertEqual(ir["analysis"]["scope"]["scope"]["kind"], "files")
        self.assertTrue(ir["analysis"]["isolated"])
        self.assertEqual(ir["analysis"]["isolated_files"], sorted(frozen["after"]["files"]))
        self.assertEqual(ir["nodes"]["f:app.py#caller"]["status"], "modified")
        self.assertEqual(ir["nodes"]["f:scratch.py#scratch"]["status"], "modified")
        self.assertEqual(ir["nodes"]["f:fresh.py#fresh"]["status"], "added")
        self.assertEqual(ir["nodes"]["f:helpers.py#removed"]["status"], "deleted")
        self.assertIn(["f:helpers.py#removed", "removed"], ir["nodes"]["f:app.py#caller"]["calls"])
        diff = (Path(frozen["out_dir"]) / "codemap.diff").read_text()
        self.assertIn("-    return 3", diff)
        self.assertNotIn("BEFORE_ONLY = 1", diff)
        self.assertNotIn(".codemap/tasks/", "\n".join(n.get("file", "") for n in ir["nodes"].values()))
        self.assertTrue(tasks.verify(self.root, state["task_id"]))

    def test_live_edits_after_freeze_cannot_change_analysis_review_or_render_sources(self):
        state = self.start()
        self.write("app.py", (self.root / "app.py").read_text().replace("return removed()", "return removed() + 2"))
        frozen = tasks.freeze(self.root, state["task_id"])
        frozen_bytes = (Path(frozen["work_root"]) / "app.py").read_bytes()
        self.write("app.py", "raise RuntimeError('Late live source must never execute')\n\ndef live_only():\n    return 99\n")
        self.write("late.py", "def late_only():\n    return 100\n")
        ir = self.analyze(frozen)
        self.assertIn("f:app.py#caller", ir["nodes"])
        self.assertNotIn("f:app.py#live_only", ir["nodes"])
        self.assertNotIn("f:late.py#late_only", ir["nodes"])
        reviewed = self.empty_review(frozen)
        out = Path(frozen["out_dir"])
        pipeline.render(out, log=None)
        rendered = json.loads((out / "ir.json").read_text())
        self.assertEqual(reviewed["root"], frozen["work_root"])
        self.assertEqual(rendered["root"], frozen["work_root"])
        self.assertEqual(rendered["review"]["status"], "reviewed")
        self.assertTrue(rendered["analysis"]["isolated"])
        self.assertEqual(rendered["analysis"]["isolated_files"], sorted(frozen["after"]["files"]))
        self.assertNotIn("live_only", (out / "codemap.diff").read_text())
        self.assertEqual((Path(frozen["work_root"]) / "app.py").read_bytes(), frozen_bytes)
        self.assertTrue(tasks.verify(self.root, state["task_id"]))

    def test_source_backed_correction_uses_frozen_evidence_and_survives_label_render(self):
        self.write("ui.jsx", "import React from 'react';\nexport function App() { return null; }\n")
        state = self.start()
        text = ("import React from 'react';\n"
                "export const Wrapped = React.memo(() => null);\n"
                "export function App() { return <Wrapped />; }\n")
        self.write("ui.jsx", text)
        frozen = tasks.freeze(self.root, state["task_id"])
        initial = self.analyze(frozen)
        self.assertNotIn("f:ui.jsx#Wrapped", initial["nodes"])
        out = Path(frozen["out_dir"])
        request = json.loads((out / "review-request.json").read_text())
        patch = {"version": 1, "analysis_id": request["analysis_id"], "reviewed_files": ["ui.jsx"],
                 "symbols": [{"kind": "function", "name": "Wrapped", "qual": "Wrapped", "file": "ui.jsx",
                              "line": 2, "end": 2, "certainty": "supported", "reason": "Memo wrapper owns the named component.",
                              "evidence": [{"file": "ui.jsx", "line": 2, "quote": text.splitlines()[1]}]}],
                 "edges": [{"op": "add", "from": "f:ui.jsx#App", "to": "f:ui.jsx#Wrapped", "kind": "calls",
                            "certainty": "supported", "reason": "App renders the component.",
                            "evidence": [{"file": "ui.jsx", "line": 3, "quote": text.splitlines()[2]}]}], "uncertain": []}
        (out / "review.json").write_text(json.dumps(patch))
        self.write("ui.jsx", "THIS LIVE SOURCE IS NOW INVALID JAVASCRIPT\n")
        reviewed = pipeline.review(out, log=lambda _: None)
        self.assertEqual(len(reviewed["review"]["accepted_symbols"]), 1)
        self.assertEqual(len(reviewed["review"]["accepted_edges"]), 1)
        (out / "labels.json").write_text(json.dumps({"items": {"f:ui.jsx#Wrapped": ["Memoized view", "Renders the task's view."]},
                                                      "why": "Add the wrapped task view."}))
        pipeline.render(out, log=None)
        rendered = json.loads((out / "ir.json").read_text())
        self.assertEqual(rendered["nodes"]["f:ui.jsx#Wrapped"]["title"], "Memoized view")
        self.assertEqual(rendered["review"]["status"], "reviewed")
        self.assertEqual(rendered["nodes"]["f:ui.jsx#App"]["review_links"][0]["evidence"][0]["quote"], text.splitlines()[2])
        self.assertTrue(tasks.verify(self.root, state["task_id"]))

    def test_relative_inherited_ts_aliases_and_tracked_hidden_files_use_captured_inventory(self):
        self.write("tsconfig.json", '{"extends":"./configs/base.json"}')
        self.write("configs/base.json", '{"compilerOptions":{"baseUrl":"..","paths":{"@core/*":["src/core/*"]}}}')
        self.write("src/core/lib.ts", "export function resolveValue() { return 1; }\n")
        self.write("src/app.ts", "import { resolveValue } from '@core/lib';\nexport function start() { return resolveValue(); }\n")
        self.write(".claude/tools/hidden.py", "def hidden_helper():\n    return 1\n")
        self.git("add", ".")
        self.git("commit", "-m", "Alias and hidden fixture")
        state = self.start()
        self.write("src/app.ts", "import { resolveValue } from '@core/lib';\nexport function start() { return resolveValue() + 2; }\n")
        self.write(".claude/tools/hidden.py", "def hidden_helper():\n    return 2\n")
        frozen = tasks.freeze(self.root, state["task_id"])
        self.assertIn(".claude/tools/hidden.py", frozen["after"]["files"])
        self.assertIn("configs/base.json", frozen["after"]["files"])
        self.write("configs/base.json", '{"compilerOptions":{"baseUrl":"wrong-location"}}')
        self.write("src/core/lib.ts", "export function lateOnly() { return 99; }\n")
        ir = self.analyze(frozen)
        self.assertIn("f:.claude/tools/hidden.py#hidden_helper", ir["nodes"])
        self.assertEqual(ir["nodes"]["f:.claude/tools/hidden.py#hidden_helper"]["status"], "modified")
        request = json.loads((Path(frozen["out_dir"]) / "review-request.json").read_text())
        self.assertTrue(any(edge["from"] == "f:src/app.ts#start" and edge["to"] == "f:src/core/lib.ts#resolveValue"
                            for edge in request["edges"]))
        reviewed = self.empty_review(frozen)
        self.assertIn(".claude/tools/hidden.py", reviewed["analysis"]["isolated_files"])
        self.assertTrue(tasks.verify(self.root, state["task_id"]))

    def test_snapshot_metadata_and_custom_output_worksheet_survive_review(self):
        state = self.start()
        self.write("app.py", (self.root / "app.py").read_text().replace("return removed()", "return removed() + 3"))
        frozen = tasks.freeze(self.root, state["task_id"])
        ir = self.analyze(frozen)
        out = Path(frozen["out_dir"])
        expected = out.as_posix() + "/labels.json"
        self.assertIn(expected, (out / "worksheet.md").read_text())
        self.assertNotIn("write .codemap/labels.json", (out / "worksheet.md").read_text())
        self.assertEqual(html_payload(ir)["artifacts"]["source_kind"], "snapshot")
        self.assertEqual(html_payload(ir)["artifacts"]["source_base"], "../work/")
        reviewed = self.empty_review(frozen)
        self.assertEqual(html_payload(reviewed)["artifacts"]["source_kind"], "snapshot")
        html = (out / "codemap.html").read_text(encoding="utf-8")
        self.assertIn('"source_kind":"snapshot"', html)
        self.assertIn("Open captured source", html)
        self.assertIn("Opens the private source snapshot captured for this task", html)
        self.assertIn(expected, (out / "worksheet.md").read_text())

    def test_no_op_task_snapshot_never_falls_back_to_last_git_commit(self):
        self.write("old_commit.py", "def old_commit_only():\n    return 1\n")
        self.git("add", ".")
        self.git("commit", "-m", "Previous unrelated coding task")
        state = self.start()
        frozen = tasks.freeze(self.root, state["task_id"])
        self.assertFalse(tasks.changed(frozen))
        with self.assertRaisesRegex(pipeline.CodemapError, "nothing to explain"):
            self.analyze(frozen)
        self.assertFalse((Path(frozen["out_dir"]) / "ir.json").exists())
        self.assertTrue(tasks.verify(self.root, state["task_id"]))


if __name__ == "__main__":
    unittest.main()
