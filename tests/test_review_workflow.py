"""The user's existing agent can correct graph structure without an API key."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "codemap" / "scripts"))
from codemap_lib import cli, pipeline


class ReviewWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codemap-model-workflow-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / ".codemap"
        self.source = self.root / "ui.jsx"
        self.text = ("import React from 'react';\n"
                     "export const Wrapped = React.memo(() => null);\n"
                     "export function App() { return <Wrapped />; }\n")
        self.source.write_text(self.text, encoding="utf-8")

    def analyze(self, **options):
        return pipeline.analyze(self.root, out=self.out, repo_mode=True, tier="free", log=lambda _: None, **options)

    def patch(self, edges=True):
        req = json.loads((self.out / "review-request.json").read_text(encoding="utf-8"))
        return {"version": 1, "analysis_id": req["analysis_id"], "reviewed_files": ["ui.jsx"],
                "symbols": [{"kind": "function", "name": "Wrapped", "qual": "Wrapped", "file": "ui.jsx",
                             "line": 2, "end": 2, "certainty": "supported", "reason": "React memo returns this component.",
                             "evidence": [{"file": "ui.jsx", "line": 2, "quote": self.text.splitlines()[1]}]}],
                "edges": ([{"op": "add", "from": "f:ui.jsx#App", "to": "f:ui.jsx#Wrapped", "kind": "calls",
                            "certainty": "supported", "reason": "App renders Wrapped in JSX.",
                            "evidence": [{"file": "ui.jsx", "line": 3, "quote": self.text.splitlines()[2]}]}] if edges else []),
                "uncertain": [{"file": "ui.jsx", "line": 2, "reason": "React's internal implementation is outside this source."}]}

    def write_patch(self, patch):
        (self.out / "review.json").write_text(json.dumps(patch), encoding="utf-8")

    def empty_patch(self):
        patch = self.patch()
        patch["symbols"], patch["edges"], patch["uncertain"] = [], [], []
        return patch

    def test_free_is_parser_only_but_review_flag_and_full_tiers_request_review(self):
        ir = self.analyze()
        self.assertEqual(ir["review"]["status"], "not_requested")
        ir = self.analyze(structural_review=True)
        self.assertEqual(ir["review"]["status"], "pending")
        self.assertIn("actual local source", (self.out / "review.md").read_text(encoding="utf-8"))
        self.assertNotIn(self.text, (self.out / "review-request.json").read_text(encoding="utf-8"))
        ir = pipeline.analyze(self.root, tier="full", repo_mode=True, log=lambda _: None)
        self.assertEqual(ir["review"]["status"], "pending")

    def test_agent_correction_reaches_map_and_survives_label_render(self):
        before = self.analyze(structural_review=True)
        self.assertNotIn("f:ui.jsx#Wrapped", before["nodes"])
        self.write_patch(self.patch())
        ir = pipeline.review(self.out, log=lambda _: None)
        self.assertEqual(ir["review"]["status"], "reviewed")
        self.assertEqual(len(ir["review"]["accepted_symbols"]), 1)
        self.assertEqual(len(ir["review"]["accepted_edges"]), 1)
        self.assertEqual(len(ir["review"]["uncertain"]), 1)
        self.assertEqual(ir["stats"]["symbols"], before["stats"]["symbols"] + 1)
        self.assertEqual(ir["stats"]["edges"], before["stats"]["edges"] + 1)
        node = ir["nodes"]["f:ui.jsx#Wrapped"]
        self.assertEqual(node["provenance"]["label"], "source-backed model inference")
        self.assertEqual(ir["nodes"]["f:ui.jsx#App"]["review_links"][0]["evidence"][0]["line"], 3)
        self.assertTrue(any(e[0:2] == ["f:ui.jsx#App", "f:ui.jsx#Wrapped"] and e[3] == .55
                            for view in ir["views"] for e in view["edges"]))
        self.assertTrue(any(e[:2] == ["f:ui.jsx#App", "f:ui.jsx#Wrapped"] and e[2] == 1
                            for view in ir["views"] for e in view.get("review_edges", [])))
        (self.out / "labels.json").write_text(json.dumps({"items": {node["id"]: ["Memoized view", "Renders a stable view."]}}))
        pipeline.render(self.out, log=None)
        stored = json.loads((self.out / "ir.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["nodes"][node["id"]]["title"], "Memoized view")
        self.assertEqual(stored["review"]["status"], "reviewed")
        html = (self.out / "codemap.html").read_text(encoding="utf-8")
        self.assertNotIn(str(self.root), html)
        self.assertIn("review-report.json", html)
        self.assertEqual(self.source.read_text(encoding="utf-8"), self.text)

    def test_new_node_does_not_silently_trigger_unproposed_edges(self):
        before = self.analyze(structural_review=True)
        self.write_patch(self.patch(edges=False))
        ir = pipeline.review(self.out, log=lambda _: None)
        self.assertIn("f:ui.jsx#Wrapped", ir["nodes"])
        self.assertEqual(ir["stats"]["edges"], before["stats"]["edges"])

    def test_incoming_review_link_invalidates_callee_label_cache(self):
        self.source.unlink()
        text = ("class Service:\n    def go(self):\n        return 1\n"
                "def run():\n    try:\n        service = Service()\n        return service.go()\n"
                "    except RuntimeError:\n        return 0\n")
        (self.root / "app.py").write_text(text, encoding="utf-8")
        before = self.analyze(structural_review=True)
        target, caller = "f:app.py#Service.go", "f:app.py#run"
        self.assertFalse(any(c[0] == target for c in before["nodes"][caller]["calls"]))
        (self.out / "labels.json").write_text(json.dumps({"items": {target: ["Unused method", "Has no callers."]}}))
        pipeline.render(self.out, log=None)
        request = json.loads((self.out / "review-request.json").read_text(encoding="utf-8"))
        patch = {"version": 1, "analysis_id": request["analysis_id"], "reviewed_files": ["app.py"],
                 "symbols": [], "uncertain": [], "edges": [{"op": "add", "from": caller, "to": target,
                 "kind": "calls", "certainty": "supported", "reason": "The local constructor precedes the method call in this try block.",
                 "evidence": [{"file": "app.py", "line": 6, "end_line": 7,
                               "quote": "\n".join(text.splitlines()[5:7])}]}]}
        self.write_patch(patch)
        after = pipeline.review(self.out, log=lambda _: None)
        self.assertNotEqual(after["nodes"][target]["hash"], before["nodes"][target]["hash"])
        self.assertNotEqual(after["nodes"][target].get("title"), "Unused method")
        self.assertIn("run()", after["nodes"][target]["sum"])
        self.assertTrue(any(view.get("review_edges") for view in after["views"]))

    def test_stale_source_fails_without_overwriting_existing_map(self):
        self.analyze(structural_review=True)
        self.write_patch(self.patch())
        saved = (self.out / "ir.json").read_bytes()
        self.source.write_text(self.text + "// Source changed after the request.\n", encoding="utf-8")
        with self.assertRaisesRegex(pipeline.CodemapError, "stale|changed|another analysis"):
            pipeline.review(self.out, log=lambda _: None)
        self.assertEqual((self.out / "ir.json").read_bytes(), saved)

    def test_bad_quote_is_rejected_and_cli_signals_partial_failure(self):
        self.analyze(structural_review=True)
        patch = self.patch()
        patch["symbols"][0]["evidence"][0]["quote"] = "export function Imaginary() {}"
        self.write_patch(patch)
        with contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["review", "--out", str(self.out)])
        self.assertEqual(code, 2)
        ir = json.loads((self.out / "ir.json").read_text(encoding="utf-8"))
        self.assertNotIn("f:ui.jsx#Wrapped", ir["nodes"])
        self.assertEqual(len(ir["review"]["rejected"]), 2)

    def test_proposal_evidence_and_copied_credentials_are_redacted(self):
        marker = "CM_CREDENTIAL_17b5982e3bc04e79b21fabcd"
        self.text = self.text.replace("() => null", f"() => {{ const api_key = '{marker}'; return null; }}")
        self.source.write_text(self.text, encoding="utf-8")
        self.analyze(structural_review=True)
        patch = self.patch()
        patch["symbols"][0]["reason"] += f" Private marker {marker}."
        self.write_patch(patch)
        pipeline.review(self.out, log=lambda _: None)
        for name in ("review.json", "review.applied.json", "review-report.json", "review-request.json", "review.md",
                     "ir.json", "codemap.md", "codemap.html", "worksheet.md"):
            path = self.out / name
            if path.exists():
                self.assertNotIn(marker, path.read_text(encoding="utf-8"), name)
        self.assertIn(marker, self.source.read_text(encoding="utf-8"))

    def test_external_input_is_not_rewritten_and_review_does_not_survive_changed_source(self):
        self.analyze(structural_review=True)
        external = self.root / "proposal.json"
        external.write_text(json.dumps(self.patch()), encoding="utf-8")
        original = external.read_bytes()
        pipeline.review(self.out, patch_path=external, log=lambda _: None)
        self.assertEqual(external.read_bytes(), original)
        ir = self.analyze(structural_review=True)
        self.assertEqual(ir["review"]["status"], "pending")
        self.assertNotIn("f:ui.jsx#Wrapped", ir["nodes"])

    def test_rejected_protocol_fields_cannot_bypass_credential_redaction(self):
        marker = "CM_CREDENTIAL_17b5982e3bc04e79b21fabcd"
        self.text += f"const api_key = '{marker}';\n"
        self.source.write_text(self.text, encoding="utf-8")
        self.analyze(structural_review=True)
        patch = self.patch()
        bad_symbol = copy.deepcopy(patch["symbols"][0])
        bad_symbol.update(kind=marker, name="Other", qual="Other")
        bad_edge = copy.deepcopy(patch["edges"][0])
        bad_edge["from"] = marker
        patch["symbols"].append(bad_symbol)
        patch["edges"].append(bad_edge)
        self.write_patch(patch)
        result = pipeline.review(self.out, log=lambda _: None)
        self.assertEqual(len(result["review"]["rejected"]), 2)
        for path in self.out.glob("review*.json"):
            self.assertNotIn(marker, path.read_text(encoding="utf-8"), path.name)

    def test_custom_output_and_external_review_input_do_not_enter_audit(self):
        self.out = self.root / "reports"
        before = self.analyze(structural_review=True, audit=True)
        external = self.root / "proposal.json"
        external.write_text(json.dumps(self.empty_patch()), encoding="utf-8")
        result = pipeline.review(self.out, patch_path=external, log=lambda _: None)
        self.assertEqual(result["stats"]["files"], before["stats"]["files"])
        request = json.loads((self.out / "review-request.json").read_text(encoding="utf-8"))
        self.assertFalse(any(p.startswith("reports/") or p == "proposal.json" for p in request["files"]))

    def test_snapshot_baseline_is_pinned_and_changes_invalidate_review(self):
        pipeline.mark(self.root, out=self.out, log=lambda _: None)
        self.source.write_text(self.text + "const changed = 1;\n", encoding="utf-8")
        before = pipeline.analyze(self.root, out=self.out, structural_review=True, log=lambda _: None)
        self.write_patch(self.empty_patch())
        result = pipeline.review(self.out, log=lambda _: None)
        self.assertEqual(before["scope"], result["scope"])
        baseline = self.out / "baseline" / "ui.jsx"
        baseline.write_text(self.text + "// Baseline modified.\n", encoding="utf-8")
        with self.assertRaisesRegex(pipeline.CodemapError, "baseline changed"):
            pipeline.review(self.out, log=lambda _: None)

    @unittest.skipUnless(shutil.which("git"), "Git session-baseline regression needs Git")
    def test_git_dirty_mark_preserves_snapshot_and_untracked_semantics(self):
        def git(*args):
            subprocess.run(["git", *args], cwd=self.root, capture_output=True, check=True)
        git("init", "-q")
        git("config", "user.email", "codemap@example.invalid")
        git("config", "user.name", "Codemap test")
        git("add", "ui.jsx")
        git("commit", "-qm", "Initial fixture")
        self.source.write_text(self.text + "// Dirty before mark.\n", encoding="utf-8")
        pending = self.root / "pending.py"
        pending.write_text("def pending():\n    return 1\n", encoding="utf-8")
        pipeline.mark(self.root, out=self.out, log=lambda _: None)
        pending.write_text("def pending():\n    return 2\n", encoding="utf-8")
        before = pipeline.analyze(self.root, out=self.out, structural_review=True, log=lambda _: None)
        self.assertEqual(before["scope"]["reason"], "codemap mark")
        self.write_patch(self.empty_patch())
        result = pipeline.review(self.out, log=lambda _: None)
        self.assertEqual(result["scope"], before["scope"])
        self.assertEqual(result["nodes"]["f:pending.py#pending"]["status"], "modified")


if __name__ == "__main__":
    unittest.main()
