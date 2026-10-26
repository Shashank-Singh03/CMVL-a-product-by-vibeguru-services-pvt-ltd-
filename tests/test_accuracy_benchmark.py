"""The benchmark must count wrong and missing results rather than hide them."""
from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "benchmarks" / "run_accuracy.py"
spec = importlib.util.spec_from_file_location("codemap_accuracy_benchmark", path)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class AccuracyBenchmarkTest(unittest.TestCase):
    def test_precision_recall_count_false_results_and_empty_sets(self):
        score = benchmark.score_items(["right", "missing"], ["right", "wrong"])
        self.assertEqual((score["tp"], score["fp"], score["fn"]), (1, 1, 1))
        self.assertEqual((score["precision"], score["recall"]), (0.5, 0.5))
        self.assertEqual(score["missing"], ["missing"])
        self.assertIsNone(benchmark.score_items([], [])["recall"])
        self.assertEqual(benchmark.score_items([], ["wrong"])["precision"], 0)

    def test_python_reference_includes_conditional_nested_and_duplicate_definitions(self):
        source = "if True:\n    def conditional():\n        def nested():\n            pass\n\nclass A:\n    def x(self):\n        pass\n    def x(self):\n        pass\n"
        self.assertEqual(benchmark.python_reference_symbols("app.py", source),
                         ["f:app.py#conditional", "f:app.py#conditional.nested", "c:app.py#A", "f:app.py#A.x", "f:app.py#A.x~2"])

    def test_reference_duplicate_class_names_qualify_their_own_methods(self):
        source = "class A:\n    def read(self):\n        pass\n\nclass A:\n    def read(self):\n        pass\n"
        self.assertEqual(benchmark.python_reference_symbols("app.py", source),
                         ["c:app.py#A", "f:app.py#A.read", "c:app.py#A~2", "f:app.py#A~2.read"])

    def test_real_pipeline_reports_disagreement_with_expectations(self):
        case = {"id": "instrumentation-check", "description": "Deliberately wrong annotation checks that the runner reports disagreements.",
                "languages": ["python"], "files": {"app.py": "def declared():\n    pass\n"},
                "expected": {"symbols": ["f:app.py#absent"], "edges": [], "risks": []}}
        result = benchmark.run_case(case)
        self.assertEqual(result["metrics"]["symbols"]["missing"], ["f:app.py#absent"])
        self.assertEqual(result["metrics"]["symbols"]["unexpected"], ["f:app.py#declared"])
        self.assertEqual(result["metrics"]["symbols"]["recall"], 0)

    def test_baseline_detects_new_misses_and_changed_dataset(self):
        perfect = benchmark.score_items(["a"], ["a"])
        report = {"dataset_sha256": "fixture-version", "cases": [{"id": "case", "parse_errors": [],
                  "metrics": {metric: copy.deepcopy(perfect) for metric in benchmark.METRICS}}]}
        baseline = benchmark.make_baseline(report)
        report["cases"][0]["metrics"]["symbols"] = benchmark.score_items(["a"], [])
        self.assertEqual(benchmark.baseline_regressions(report, baseline), ["case: 1 new symbols missing"])
        report["dataset_sha256"] = "changed"
        self.assertIn("Dataset changed", benchmark.baseline_regressions(report, baseline)[0])

    def test_report_accepts_fixture_notes_as_a_list(self):
        case = {"id": "reported-gap", "notes": ["A reviewed limitation."], "parse_errors": [],
                "metrics": {metric: benchmark.score_items(["missing"], []) for metric in benchmark.METRICS}}
        report = {"cases": [case], "summary": benchmark.aggregate([case])}
        self.assertIn("A reviewed limitation.", benchmark.markdown_report(report))

    def test_authored_corpus_has_no_new_errors_against_reviewed_baseline(self):
        root = path.parent
        cases = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((root / "cases").glob("*.json"))]
        report = {"dataset_sha256": benchmark.dataset_digest(cases),
                  "cases": [benchmark.run_case(case) for case in cases]}
        baseline = json.loads((root / "baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(benchmark.baseline_regressions(report, baseline), [])


if __name__ == "__main__":
    unittest.main()
