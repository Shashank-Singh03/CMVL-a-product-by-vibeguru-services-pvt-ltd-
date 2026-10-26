"""Authored Python scope and conservative instance-flow regressions."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "codemap" / "scripts"))

from codemap_lib.graph import CodeGraph
from codemap_lib.parse_python import parse_python


def graph(sources):
    return CodeGraph({path: parse_python(path, text) for path, text in sources.items()}).build()


class PythonStructureTest(unittest.TestCase):
    def test_conditional_and_nested_declarations_have_lexical_ids(self):
        fi = parse_python("app.py", '''if enabled:
    def guarded(): pass
try:
    class Selected: pass
except ImportError:
    class Fallback: pass
def outer():
    if enabled:
        def inner(): pass
    class Worker:
        def run(self):
            def callback(): pass
            return callback()
    return inner()
class Container:
    class Child:
        def run(self): pass
''')
        by_name = {s.qual: s for s in fi.symbols}
        self.assertEqual(set(by_name), {"guarded", "Selected", "Fallback", "outer", "outer.inner", "outer.Worker",
                                        "outer.Worker.run", "outer.Worker.run.callback", "Container", "Container.Child", "Container.Child.run"})
        self.assertEqual(by_name["outer.inner"].lexical_parent, "f:app.py#outer")
        self.assertIsNone(by_name["outer.inner"].parent)
        self.assertEqual(by_name["outer.Worker.run"].parent, "c:app.py#outer.Worker")
        self.assertEqual(by_name["Container.Child"].lexical_parent, "c:app.py#Container")
        self.assertTrue(all(not s.provenance for s in fi.symbols))

    def test_nested_body_calls_belong_to_nested_function(self):
        g = graph({"app.py": "def helper(): pass\ndef outer():\n    def inner():\n        return helper()\n    return inner()\n"})
        self.assertIn(("f:app.py#outer", "f:app.py#outer.inner"), g.edges)
        self.assertIn(("f:app.py#outer.inner", "f:app.py#helper"), g.edges)
        self.assertNotIn(("f:app.py#outer", "f:app.py#helper"), g.edges)
        self.assertNotIn("inner", g.top["app.py"])
        self.assertNotIn("inner", g.global_names)

    def test_lexical_shadowing_and_global_names_stay_separate(self):
        g = graph({"app.py": '''def helper(): pass
def outer():
    def helper(): pass
    def caller(): return helper()
    return caller()
def other(): return helper()
'''})
        self.assertIn(("f:app.py#outer.caller", "f:app.py#outer.helper"), g.edges)
        self.assertIn(("f:app.py#other", "f:app.py#helper"), g.edges)
        self.assertNotIn(("f:app.py#outer.caller", "f:app.py#helper"), g.edges)

    def test_nested_class_and_qualified_class_access(self):
        g = graph({"app.py": '''def outer():
    class Worker:
        def run(self): pass
    instance = Worker()
    return instance.run()
class Box:
    class Worker:
        @staticmethod
        def run(): pass
def use(): return Box.Worker.run()
'''})
        self.assertIn(("f:app.py#outer", "f:app.py#outer.Worker.run"), g.edges)
        self.assertIn(("f:app.py#use", "f:app.py#Box.Worker.run"), g.edges)

    def test_conditional_duplicate_names_remain_ambiguous(self):
        g = graph({"app.py": "if enabled:\n    def chosen(): pass\nelse:\n    def chosen(): pass\ndef use(): return chosen()\n"})
        self.assertIn("f:app.py#chosen", g.syms)
        self.assertIn("f:app.py#chosen~2", g.syms)
        self.assertNotIn(("f:app.py#use", "f:app.py#chosen"), g.edges)
        self.assertNotIn(("f:app.py#use", "f:app.py#chosen~2"), g.edges)

    def test_duplicate_outer_declarations_keep_distinct_child_ids(self):
        fi = parse_python("app.py", "def outer():\n    def inner(): pass\ndef outer():\n    def inner(): pass\n")
        self.assertEqual([s.qual for s in fi.symbols], ["outer", "outer.inner", "outer~2", "outer~2.inner"])

    def test_defaults_execute_in_outer_scope_and_lambda_body_is_not_flattened(self):
        g = graph({"app.py": '''def prepare(): pass
def work(): pass
def outer():
    def inner(value=prepare()): return work()
    deferred = lambda: work()
    return inner()
'''})
        self.assertIn(("f:app.py#outer", "f:app.py#prepare"), g.edges)
        self.assertIn(("f:app.py#outer.inner", "f:app.py#work"), g.edges)
        self.assertNotIn(("f:app.py#outer", "f:app.py#work"), g.edges)


class PythonConstructedReceiverTest(unittest.TestCase):
    WORKER = "class Worker:\n    def run(self): pass\n"

    def test_imported_constructed_local_receiver_resolves(self):
        g = graph({"worker.py": self.WORKER,
                   "app.py": "from worker import Worker\ndef handle():\n    worker = Worker()\n    return worker.run()\n"})
        edge = g.edges[("f:app.py#handle", "f:worker.py#Worker.run")]
        self.assertGreaterEqual(edge[2], 0.8)

    def test_local_imports_do_not_leak_to_other_functions(self):
        g = graph({"first.py": self.WORKER, "second.py": self.WORKER,
                   "app.py": '''from first import Worker
def one():
    from second import Worker
    instance = Worker()
    return instance.run()
def two():
    instance = Worker()
    return instance.run()
'''})
        self.assertIn(("f:app.py#one", "f:second.py#Worker.run"), g.edges)
        self.assertIn(("f:app.py#two", "f:first.py#Worker.run"), g.edges)
        self.assertNotIn(("f:app.py#two", "f:second.py#Worker.run"), g.edges)

    def test_reassignments_parameters_and_factories_remain_unknown(self):
        bodies = (
            "def use(other):\n    instance = Worker()\n    instance = other\n    return instance.run()\n",
            "def use(instance: Worker):\n    return instance.run()\n",
            "def use(Worker):\n    instance = Worker()\n    return instance.run()\n",
            "def factory(): return Worker()\ndef use():\n    instance = factory()\n    return instance.run()\n",
            "def use(enabled):\n    if enabled:\n        instance = Worker()\n    return instance.run()\n",
            "def use(other):\n    instance = Worker()\n    instance.run = other\n    return instance.run()\n",
        )
        for body in bodies:
            with self.subTest(body=body):
                g = graph({"app.py": self.WORKER + body})
                self.assertNotIn(("f:app.py#use", "f:app.py#Worker.run"), g.edges)

    def test_constructor_assignment_does_not_resolve_an_earlier_call(self):
        g = graph({"app.py": self.WORKER + "def use():\n    instance.run()\n    instance = Worker()\n"})
        self.assertNotIn(("f:app.py#use", "f:app.py#Worker.run"), g.edges)

    def test_rebound_constructor_names_are_not_treated_as_classes(self):
        bodies = (
            "Worker = unknown\ndef use():\n    instance = Worker()\n    return instance.run()\n",
            "def use(other):\n    class Local:\n        def run(self): pass\n    Local = other\n    instance = Local()\n    return instance.run()\n",
            "def use(other):\n    from worker import Worker\n    Worker = other\n    instance = Worker()\n    return instance.run()\n",
            "def use(other):\n    class Local:\n        def run(self): pass\n    Local = other\n    def inner():\n        instance = Local()\n        return instance.run()\n    return inner()\n",
        )
        for body in bodies:
            with self.subTest(body=body):
                g = graph({"app.py": self.WORKER + body, "worker.py": self.WORKER})
                self.assertFalse(any(dst.endswith(".run") for (src, dst) in g.edges
                                     if src in ("f:app.py#use", "f:app.py#use.inner")))

    def test_nonlocal_mutation_disables_outer_constructed_type(self):
        g = graph({"app.py": self.WORKER + '''def use(other):
    instance = Worker()
    def replace():
        nonlocal instance
        instance = other
    replace()
    return instance.run()
'''})
        self.assertNotIn(("f:app.py#use", "f:app.py#Worker.run"), g.edges)

    def test_same_named_python_classes_are_not_partial_classes(self):
        g = graph({"first.py": "class Worker: pass\n", "second.py": self.WORKER,
                   "app.py": "from first import Worker\ndef use():\n    instance = Worker()\n    return instance.run()\n"})
        self.assertNotIn(("f:app.py#use", "f:second.py#Worker.run"), g.edges)

    def test_imported_rebound_constructor_is_not_exported_as_original_class(self):
        g = graph({"worker.py": self.WORKER + "Worker = unknown\n",
                   "app.py": "from worker import Worker\ndef use():\n    instance = Worker()\n    return instance.run()\n"})
        self.assertNotIn(("f:app.py#use", "f:worker.py#Worker.run"), g.edges)


class PythonContainerReceiverTest(unittest.TestCase):
    PREFIX = "class Part:\n    def update(self): pass\nclass Other:\n    def update(self): pass\n"

    def test_explicit_homogeneous_list_resolves_same_named_method(self):
        source = self.PREFIX + '''class Device:
    def setup(self):
        self.parts = []
        for i in range(2):
            self.parts.append(Part())
    def refresh(self, index):
        return self.parts[index].update()
'''
        g = graph({"app.py": source})
        self.assertIn(("f:app.py#Device.refresh", "f:app.py#Part.update"), g.edges)
        self.assertNotIn(("f:app.py#Device.refresh", "f:app.py#Other.update"), g.edges)

    def test_mixed_unknown_or_escaped_lists_remain_unresolved(self):
        for mutation in ("self.parts.append(Other())", "self.parts.append(unknown)", "send(self.parts)", "self.parts = unknown", "alias = self.parts"):
            with self.subTest(mutation=mutation):
                source = self.PREFIX + "class Device:\n    def setup(self, unknown):\n        self.parts = [Part()]\n        " + mutation + "\n    def refresh(self, index):\n        return self.parts[index].update()\n"
                g = graph({"app.py": source})
                self.assertNotIn(("f:app.py#Device.refresh", "f:app.py#Part.update"), g.edges)
                self.assertNotIn(("f:app.py#Device.refresh", "f:app.py#Other.update"), g.edges)

    def test_annotation_alone_does_not_invent_container_type(self):
        source = self.PREFIX + "class Device:\n    def setup(self, unknown):\n        self.parts: list[Part] = unknown\n    def refresh(self, index):\n        return self.parts[index].update()\n"
        g = graph({"app.py": source})
        self.assertNotIn(("f:app.py#Device.refresh", "f:app.py#Part.update"), g.edges)


if __name__ == "__main__":
    unittest.main()
