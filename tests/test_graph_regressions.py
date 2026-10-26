"""Regression coverage for lexical bindings, removed targets and snapshot scope."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "codemap" / "scripts"))

from codemap_lib import pipeline
from codemap_lib.graph import CodeGraph
from codemap_lib.scan import parse_source


def graph_of(sources):
    return CodeGraph({p: parse_source(p, text) for p, text in sources.items()}).build()


class PythonBindingRegressionTest(unittest.TestCase):
    def test_parameters_and_assigned_locals_do_not_resolve_to_global_functions(self):
        for body in (
            "def caller(store):\n    return store()\n",
            "def caller(*store):\n    return store()\n",
            "def caller(**store):\n    return store()\n",
            "def caller():\n    store = lambda: 2\n    return store()\n",
        ):
            with self.subTest(body=body):
                g = graph_of({"app.py": "def store():\n    return 1\n\n" + body})
                self.assertNotIn(("f:app.py#caller", "f:app.py#store"), g.edges)

    def test_parameter_receivers_and_callbacks_are_not_guessed(self):
        g = graph_of({"app.py": "class Store:\n    def deliver(self):\n        pass\n\n"
                      "def caller(store, deliver):\n    store.deliver()\n    map(deliver, [])\n"})
        self.assertEqual(g.edges, {})

    def test_global_declarations_and_self_calls_still_resolve(self):
        g = graph_of({"app.py": "def store():\n    return 1\n\n"
                      "def caller():\n    global store\n    return store()\n\n"
                      "class Service:\n    def run(self):\n        return self.deliver()\n"
                      "    def deliver(self):\n        return 1\n"})
        self.assertIn(("f:app.py#caller", "f:app.py#store"), g.edges)
        self.assertIn(("f:app.py#Service.run", "f:app.py#Service.deliver"), g.edges)

    def test_reassigned_and_static_receivers_do_not_resolve_as_own_methods(self):
        for method in (
            "    def run(self):\n        self = object()\n        return self.deliver()\n",
            "    @staticmethod\n    def run(self):\n        return self.deliver()\n",
            "    @classmethod\n    def run(cls):\n        cls = object()\n        return cls.deliver()\n",
        ):
            with self.subTest(method=method):
                g = graph_of({"app.py": "class Service:\n    def deliver(self):\n        return 1\n" + method})
                self.assertNotIn(("f:app.py#Service.run", "f:app.py#Service.deliver"), g.edges)

    def test_nested_parameter_and_lambda_bindings_do_not_resolve_as_globals(self):
        for body in (
            "    def nested(store):\n        return store()\n    return nested(lambda: 2)\n",
            "    nested = lambda store: store()\n    return nested(lambda: 2)\n",
            "    return [store() for store in items]\n",
        ):
            with self.subTest(body=body):
                g = graph_of({"app.py": "def store():\n    return 1\n\ndef caller(items):\n" + body})
                self.assertNotIn(("f:app.py#caller", "f:app.py#store"), g.edges)

    def test_nested_global_calls_and_captured_receivers_remain_visible(self):
        g = graph_of({"helpers.py": "def deliver():\n    return 1\n",
                      "app.py": "from helpers import deliver\n"
                      "def caller(deliver):\n    def nested():\n        global deliver\n        return deliver()\n"
                      "    return nested()\n\n"
                      "class Service:\n    def run(self):\n        def nested():\n            return self.deliver()\n"
                      "        return nested()\n    def deliver(self):\n        return 1\n"})
        self.assertIn(("f:app.py#caller", "f:app.py#caller.nested"), g.edges)
        self.assertIn(("f:app.py#caller.nested", "f:helpers.py#deliver"), g.edges)
        self.assertNotIn(("f:app.py#caller", "f:helpers.py#deliver"), g.edges)
        self.assertIn(("f:app.py#Service.run", "f:app.py#Service.run.nested"), g.edges)
        self.assertIn(("f:app.py#Service.run.nested", "f:app.py#Service.deliver"), g.edges)
        self.assertNotIn(("f:app.py#Service.run", "f:app.py#Service.deliver"), g.edges)

    def test_comprehension_binding_does_not_hide_later_global_call(self):
        g = graph_of({"app.py": "def store():\n    return 1\n\ndef caller(items):\n"
                      "    [store() for store in items]\n    return store()\n"})
        self.assertIn(("f:app.py#caller", "f:app.py#store"), g.edges)

    @unittest.skipUnless(sys.version_info >= (3, 12), "generic declarations need Python 3.12+")
    def test_generic_declaration_scopes_still_respect_parameter_bindings(self):
        g = graph_of({"app.py": "def store():\n    return 1\n\ndef caller[T](store):\n    return store()\n"
                      "class Service[T]:\n    def run(self, store):\n        self.deliver()\n        return store()\n"
                      "    def deliver(self):\n        return 1\n"})
        self.assertNotIn(("f:app.py#caller", "f:app.py#store"), g.edges)
        self.assertNotIn(("f:app.py#Service.run", "f:app.py#store"), g.edges)
        self.assertIn(("f:app.py#Service.run", "f:app.py#Service.deliver"), g.edges)


class IncludeResolutionRegressionTest(unittest.TestCase):
    def test_ambiguous_suffix_is_unresolved_for_every_input_order(self):
        sources = {"src/main.c": '#include "common.h"\nint main() { return perform(); }\n',
                   "a/common.h": "int perform() { return 1; }\n",
                   "b/common.h": "int perform() { return 2; }\n"}
        for items in (list(sources.items()), list(reversed(list(sources.items())))):
            g = graph_of(dict(items))
            self.assertIsNone(g._file_module("src/main.c", "common.h", "c"))
            self.assertEqual(g.edges, {})

    def test_unique_suffix_still_resolves(self):
        g = graph_of({"src/main.c": '#include "common.h"\nint main() { return perform(); }\n',
                      "include/common.h": "int perform() { return 1; }\n"})
        self.assertIn(("f:src/main.c#main", "f:include/common.h#perform"), g.edges)


class DeletedTargetRegressionTest(unittest.TestCase):
    def analyze_change(self, old_sources, new_sources):
        with tempfile.TemporaryDirectory(prefix="codemap-graph-regression-") as directory:
            root = Path(directory)
            for path, text in old_sources.items():
                full = root / path
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(text, encoding="utf-8")
            pipeline.mark(root, log=lambda *_: None)
            for path in old_sources.keys() - new_sources.keys():
                (root / path).unlink()
            for path, text in new_sources.items():
                full = root / path
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(text, encoding="utf-8")
            return pipeline.analyze(root, tier="free", log=lambda *_: None)

    def test_retained_broken_caller_is_visible_after_function_deletion(self):
        ir = self.analyze_change(
            {"app.py": "def helper():\n    return 1\n\ndef caller():\n    return helper()\n"},
            {"app.py": "def caller():\n    return helper()\n"})
        deleted = ir["nodes"]["f:app.py#helper"]
        caller = ir["nodes"]["f:app.py#caller"]
        self.assertEqual(deleted["status"], "deleted")
        self.assertEqual(caller["status"], "context")
        self.assertIn(["f:app.py#caller", "caller"], deleted["callers"])
        self.assertIn(["f:app.py#helper", "helper"], caller["calls"])

    def test_deleted_file_retains_current_importing_caller(self):
        ir = self.analyze_change(
            {"helper.py": "def helper():\n    return 1\n",
             "app.py": "from helper import helper\n\ndef caller():\n    return helper()\n"},
            {"app.py": "from helper import helper\n\ndef caller():\n    return helper()\n"})
        self.assertIn(["f:app.py#caller", "caller"], ir["nodes"]["f:helper.py#helper"]["callers"])

    def test_replaced_import_and_assignment_do_not_target_deleted_definition(self):
        old = {"app.py": "def helper():\n    return 1\n\ndef caller():\n    return helper()\n",
               "other.py": "def helper():\n    return 2\n"}
        for replacement in ("from other import helper", "helper = lambda: 2"):
            with self.subTest(replacement=replacement):
                new = dict(old, **{"app.py": replacement + "\n\ndef caller():\n    return helper()\n"})
                ir = self.analyze_change(old, new)
                self.assertEqual(ir["nodes"]["f:app.py#helper"]["callers"], [])

    def test_removed_call_is_not_restored_from_historical_source(self):
        ir = self.analyze_change(
            {"app.py": "def helper():\n    return 1\n\ndef caller():\n    return helper()\n"},
            {"app.py": "def caller():\n    return 2\n"})
        self.assertEqual(ir["nodes"]["f:app.py#helper"]["callers"], [])
        self.assertEqual(ir["nodes"]["f:app.py#caller"]["calls"], [])


@unittest.skipUnless(shutil.which("git"), "git is unavailable")
class GitBaselineRegressionTest(unittest.TestCase):
    def test_deletion_of_snapshotted_untracked_file_is_mapped(self):
        with tempfile.TemporaryDirectory(prefix="codemap-baseline-regression-") as directory:
            root = Path(directory)
            def git(*args):
                subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
            git("init", "-q")
            git("config", "user.email", "codemap-test@example.invalid")
            git("config", "user.name", "Codemap Test")
            (root / "tracked.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            git("add", "tracked.py")
            git("commit", "-qm", "Initial")
            (root / "draft.py").write_text("def draft():\n    return 2\n", encoding="utf-8")
            pipeline.mark(root, log=lambda *_: None)
            (root / "draft.py").unlink()
            ir = pipeline.analyze(root, tier="free", log=lambda *_: None)
            self.assertEqual(ir["scope"]["files"], 1)
            self.assertEqual(ir["nodes"]["f:draft.py#draft"]["status"], "deleted")


if __name__ == "__main__":
    unittest.main()
