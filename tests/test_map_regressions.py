"""Regression coverage for complete change maps and content-based label reuse."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "codemap" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from codemap_lib import pipeline  # noqa: E402
from codemap_lib.graph import CodeGraph  # noqa: E402
from codemap_lib.hierarchy import MapBuilder  # noqa: E402
from codemap_lib.labels import apply_cache, node_hash, update_cache  # noqa: E402
from codemap_lib.scan import parse_source  # noqa: E402


def silent(*_args, **_kwargs):
    pass


class MapRegressionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="codemap-map-regression-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def analyze(self, **kwargs):
        return pipeline.analyze(self.root, tier="full", log=silent, **kwargs)

    def cache_labels(self, labels):
        out = self.root / ".codemap"
        (out / "labels.json").write_text(json.dumps({"items": labels}), encoding="utf-8")
        pipeline.render(out, log=None)

    def test_module_changes_survive_alongside_class_changes(self):
        self.write("app.py", "import json\nLIMIT = 10\n\nclass Reader:\n"
                   "    def read(self):\n        return LIMIT\n")
        pipeline.mark(self.root, log=silent)
        self.write("app.py", "import os\nLIMIT = 20\n\nclass Reader:\n"
                   "    def read(self):\n        return LIMIT * 2\n")

        ir = self.analyze()
        nodes = ir["nodes"]
        module = nodes["m:app.py"]
        self.assertEqual(module["own_status"], "modified")
        self.assertEqual((module["add"], module["del"]), (2, 2))
        self.assertIn("+import os", module["diff"])
        self.assertIn("+LIMIT = 20", module["diff"])
        self.assertIn("-LIMIT = 10", module["diff"])
        self.assertEqual(nodes["f:app.py#Reader.read"]["status"], "modified")
        self.assertEqual((nodes["root"]["add"], nodes["root"]["del"]), (3, 3),
                         "module and method lines must each contribute once")
        worksheet = (self.root / ".codemap" / "worksheet.md").read_text(encoding="utf-8")
        self.assertIn("+LIMIT = 20", worksheet)

    def test_added_file_keeps_imports_and_constants_outside_symbols(self):
        self.write("existing.py", "def existing():\n    return 1\n")
        pipeline.mark(self.root, log=silent)
        self.write("reader.py", "import json\nLIMIT = 10\n\nclass Reader:\n"
                   "    def read(self):\n        return LIMIT\n\n"
                   "def boot():\n    return Reader()\n\n")

        ir = self.analyze()
        nodes = ir["nodes"]
        module = nodes["m:reader.py"]
        self.assertEqual(module["own_status"], "added")
        self.assertEqual(module["diff"], ["+import json", "+LIMIT = 10"])
        self.assertEqual(module["add"], 4, "two module lines plus the two-line boot function")
        self.assertEqual(nodes["c:reader.py#Reader"]["add"], 3)
        self.assertEqual((nodes["root"]["add"], nodes["root"]["del"]), (7, 0))
        self.assertGreater(ir["scope"]["add"], nodes["root"]["add"],
                           "blank lines between symbols stay outside mapped line counts")
        worksheet = (self.root / ".codemap" / "worksheet.md").read_text(encoding="utf-8")
        self.assertIn("+import json", worksheet)
        self.assertIn("+LIMIT = 10", worksheet)

    def test_deleted_file_keeps_imports_and_constants_outside_symbols(self):
        self.write("reader.py", "import json\nLIMIT = 10\n\nclass Reader:\n"
                   "    def read(self):\n        return LIMIT\n\n")
        self.write("existing.py", "def existing():\n    return 1\n")
        pipeline.mark(self.root, log=silent)
        (self.root / "reader.py").unlink()

        ir = self.analyze()
        nodes = ir["nodes"]
        module = nodes["m:reader.py"]
        self.assertEqual(module["own_status"], "deleted")
        self.assertEqual(module["diff"], ["-import json", "-LIMIT = 10"])
        self.assertEqual(module["del"], 2)
        self.assertEqual(nodes["c:reader.py#Reader"]["del"], 3)
        self.assertEqual((nodes["root"]["add"], nodes["root"]["del"]), (0, 5))
        self.assertGreater(ir["scope"]["del"], nodes["root"]["del"])
        worksheet = (self.root / ".codemap" / "worksheet.md").read_text(encoding="utf-8")
        self.assertIn("-import json", worksheet)
        self.assertIn("-LIMIT = 10", worksheet)

    def make_grouped_repo(self):
        self.write("left/worker.py", "LIMIT = 10\n\nclass Worker:\n"
                   "    def run(self):\n        return 1\n\n"
                   "def start():\n    return Worker().run()\n")
        for name in ("a", "b", "c"):
            self.write(f"right/{name}.py", f"def {name}():\n    return 7\n")
        return self.analyze(repo_mode=True)

    def test_changed_body_invalidates_containers_but_reuses_unchanged_labels(self):
        ir = self.make_grouped_repo()
        class_id = "c:left/worker.py#Worker"
        module_id = "m:left/worker.py"
        method_id = "f:left/worker.py#Worker.run"
        group_id = ir["nodes"][class_id]["parent"]
        self.assertEqual(ir["nodes"][group_id]["kind"], "group")
        labels = {
            class_id: ["Data reader", "Reads existing data."],
            module_id: ["Read workflow", "Starts the data reader."],
            method_id: ["Read data", "Returns the existing data."],
            group_id: ["Readers", "Contains the data readers."],
            "m:right/a.py": ["Stable helper", "Provides an independent helper."],
        }
        self.cache_labels(labels)

        unchanged = self.analyze(repo_mode=True)
        for nid, value in labels.items():
            self.assertEqual(unchanged["nodes"][nid]["lsrc"], "cache")
            self.assertEqual(unchanged["nodes"][nid]["title"], value[0])

        worker = self.root / "left" / "worker.py"
        worker.write_text(worker.read_text(encoding="utf-8").replace("return 1", "return 2"), encoding="utf-8")
        changed = self.analyze(repo_mode=True)
        for nid in (class_id, module_id, method_id, group_id):
            self.assertNotEqual(changed["nodes"][nid]["lsrc"], "cache", nid)
            self.assertNotEqual(node_hash(ir["nodes"][nid]), node_hash(changed["nodes"][nid]), nid)
        self.assertEqual(changed["nodes"]["m:right/a.py"]["title"], "Stable helper")
        self.assertEqual(changed["nodes"]["m:right/a.py"]["lsrc"], "cache")

    def test_module_constant_edit_invalidates_module_and_group_labels(self):
        ir = self.make_grouped_repo()
        module_id = "m:left/worker.py"
        class_id = "c:left/worker.py#Worker"
        group_id = ir["nodes"][module_id]["parent"]
        self.cache_labels({module_id: ["Small batch", "Processes up to ten items."],
                           group_id: ["Small batches", "Contains small batch workflows."],
                           class_id: ["Stable worker", "Runs the worker."]})
        worker = self.root / "left" / "worker.py"
        worker.write_text(worker.read_text(encoding="utf-8").replace("LIMIT = 10", "LIMIT = 20"), encoding="utf-8")
        changed = self.analyze(repo_mode=True)
        for nid in (module_id, group_id):
            self.assertNotEqual(changed["nodes"][nid]["lsrc"], "cache", nid)
        self.assertEqual(changed["nodes"][class_id]["lsrc"], "cache",
                         "an unrelated module constant must not invalidate an unchanged class")

    def test_hidden_method_content_invalidates_class_label(self):
        def build(hidden_return):
            fi = parse_source("worker.py", "class Worker:\n"
                              "    def run(self):\n        return 1\n"
                              f"    def helper(self):\n        return {hidden_return}\n")
            next(s for s in fi.symbols if s.qual == "Worker.run").status = "modified"
            nodes, _views = MapBuilder(CodeGraph({fi.path: fi}).build(), {fi.path: fi}, "diff", "fixture").build()
            return {"nodes": nodes}

        before = build(1)
        class_id = "c:worker.py#Worker"
        self.assertIn(["f:worker.py#Worker.helper", "helper()"], before["nodes"][class_id]["hidden"])
        before["nodes"][class_id].update(title="One result", sum="Always returns one.", lsrc="agent")
        cache = {}
        update_cache(before, cache)
        after = build(2)
        self.assertEqual(apply_cache(after, cache), 0)
        self.assertNotEqual(node_hash(before["nodes"][class_id]), node_hash(after["nodes"][class_id]))

    def test_previously_incomplete_container_cache_is_not_reused(self):
        ir = self.make_grouped_repo()
        module = ir["nodes"]["m:left/worker.py"]
        old_cache = {f"1:{node_hash(module)}": ["Old label", "Uses the old cache format."]}
        self.assertEqual(apply_cache(ir, old_cache), 0)
        self.assertNotEqual(module["lsrc"], "cache")


if __name__ == "__main__":
    unittest.main()
