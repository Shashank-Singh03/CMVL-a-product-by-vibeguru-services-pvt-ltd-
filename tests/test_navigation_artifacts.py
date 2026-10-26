"""Return routes are bound to captured outputs and never arbitrary redirects."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/codemap/scripts"))
from codemap_lib import history, render


class NavigationArtifactsTest(unittest.TestCase):
    def test_only_canonical_return_routes_survive_public_artifacts(self):
        ident = "a" * 32
        self.assertEqual(render.portable_artifacts({"artifacts": {"dashboard": "../../../index.html", "task_id": ident}}),
                         {"dashboard": "../../../index.html", "task_id": ident})
        for path, task in (("https://example.invalid", ident), ("../../elsewhere.html", ident),
                           ("../../../index.html", "../private"), ("../../../index.html", None)):
            self.assertNotIn("dashboard", render.portable_artifacts({"artifacts": {"dashboard": path, "task_id": task}}))

    def test_task_navigation_requires_real_captured_output_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / ".codemap/tasks" / ("a" * 32) / "output"
            ir = {"root": str(out.parent / "work"), "analysis": {"isolated": True}}
            self.assertEqual(render.output_navigation(ir, out)["task_id"], "a" * 32)
            self.assertEqual(render.output_navigation(ir, root / "output"), {})
            ir["root"] = str(root / "live")
            self.assertEqual(render.output_navigation(ir, out), {})

    def test_standalone_map_only_links_existing_overview(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            self.assertEqual(render.output_navigation({}, out), {})
            (out / "index.html").write_text("<!doctype html><title>Overview</title>", encoding="utf-8")
            self.assertEqual(render.output_navigation({}, out), {"dashboard": "index.html"})

    def test_lesson_item_links_resolve_to_recorded_view_and_node(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ident, key = "a" * 32, "f:app.py#answer"
            out = root / ".codemap/tasks" / ident / "output"
            out.mkdir(parents=True)
            ir = {"nodes": {key: {"kind": "function", "file": "app.py", "name": "answer", "status": "modified",
                                  "lang": "python", "domain": "code", "diff": ["- return 1", "+ return 2"]}},
                  "views": [{"id": "v2", "children": [key], "edges": []}], "stats": {"files": 1}, "scope": {"files": 1}}
            (out / "ir.json").write_text(json.dumps(ir), encoding="utf-8")
            data = history._dashboard_data(root, root / ".codemap", [{"task_id": ident, "status": "awaiting_review"}])
            changes = data["tasks"][0]["learning"]["steps"][2]["changes"]
            url = urlsplit(changes[0]["mapUrl"])
            self.assertEqual(url.path, f"tasks/{ident}/output/codemap.html")
            self.assertEqual(parse_qs(url.fragment), {"v": ["v2"], "n": [key]})
            self.assertEqual(changes[0]["id"], "change-" + hashlib.sha256(key.encode()).hexdigest()[:12])

    def test_private_or_malformed_node_ids_do_not_gain_a_deep_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ident = "b" * 32
            out = root / ".codemap/tasks" / ident / "output"
            out.mkdir(parents=True)
            ir = {"nodes": {"f:app.py#C:/PRIVATE_OWNER/code": {"kind": "function", "file": "app.py", "name": "answer",
                  "status": "modified", "domain": "code", "lang": "python", "diff": ["+ return 2"]}}}
            (out / "ir.json").write_text(json.dumps(ir), encoding="utf-8")
            data = history._dashboard_data(root, root / ".codemap", [{"task_id": ident, "status": "awaiting_review"}])
            change = data["tasks"][0]["learning"]["steps"][2]["changes"][0]
            self.assertNotIn("mapUrl", change)
            self.assertNotIn("PRIVATE_OWNER", json.dumps(data))


if __name__ == "__main__":
    unittest.main()
