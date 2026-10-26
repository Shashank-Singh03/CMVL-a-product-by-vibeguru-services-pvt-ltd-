"""Measure codemap against authored expectations and an independent Python AST reference.

Only static analysis runs. No target project is imported or executed, and the
default run needs neither a network connection nor extra packages.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
import sys
import sysconfig
import tempfile
import time
from collections import Counter
from pathlib import Path, PurePosixPath
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "codemap" / "scripts"))

from codemap_lib import VERSION, pipeline
from codemap_lib.graph import CodeGraph
from codemap_lib.util import read_text

METRICS = ("symbols", "edges", "risks")


def _item(value):
    if isinstance(value, dict):
        return (value["rule"], value["file"], value["line"])
    return tuple(value) if isinstance(value, list) else value


def score_items(expected, observed):
    """Undefined ratios are null, not a misleading 100% for empty observations."""
    expected, observed = {_item(x) for x in expected}, {_item(x) for x in observed}
    correct = expected & observed
    return {"tp": len(correct), "fp": len(observed - expected), "fn": len(expected - observed),
            "precision": len(correct) / len(observed) if observed else None,
            "recall": len(correct) / len(expected) if expected else None,
            "unexpected": sorted(observed - expected), "missing": sorted(expected - observed)}


def aggregate(results):
    combined = {}
    for metric in METRICS:
        counts = {k: sum(result["metrics"][metric][k] for result in results) for k in ("tp", "fp", "fn")}
        tp, fp, fn = (counts[k] for k in ("tp", "fp", "fn"))
        combined[metric] = dict(counts, precision=tp / (tp + fp) if tp + fp else None,
                                recall=tp / (tp + fn) if tp + fn else None)
    return combined


def _write_sources(root, sources):
    for path, text in sources.items():
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts or "\\" in path or ":" in path:
            raise ValueError("Fixture paths must stay inside their temporary project: " + path)
        target = root.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _analyze(root, **options):
    observed = {}

    class RecordingGraph(CodeGraph):
        def build(self):
            result = super().build()
            observed["graph"] = result
            return result

    # Observe the real pipeline's final graph instead of maintaining a second
    # parser/linker implementation. The independent truth comes from annotations.
    with patch.object(pipeline, "CodeGraph", RecordingGraph):
        ir = pipeline.analyze(root, tier="free", log=lambda _: None, **options)
    return ir, observed["graph"]


def run_case(case):
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="codemap-accuracy-") as folder:
        root = Path(folder)
        before = case.get("before")
        if case.get("mode", "repo") == "diff":
            if before is None:
                raise ValueError("A diff fixture must provide before sources: " + case["id"])
            _write_sources(root, before)
            pipeline.mark(root, log=lambda _: None)
            for deleted in before.keys() - case["files"].keys():
                (root / deleted).unlink()
        _write_sources(root, case["files"])
        ir, graph = _analyze(root, repo_mode=case.get("mode", "repo") == "repo",
                             include_tests=True, audit=case.get("audit", False))
        actual = {"symbols": list(graph.syms), "edges": list(graph.edges), "risks": ir["risks"]}
        result = {"id": case["id"], "description": case["description"],
                  "provenance": case.get("provenance", "authored"), "languages": case["languages"],
                  "known_gap": case.get("known_gap", False), "notes": case.get("notes", ""),
                  "metrics": {m: score_items(case["expected"][m], actual[m]) for m in METRICS},
                  "parse_errors": ir["stats"]["parse_errors"], "files": ir["stats"]["files"],
                  "seconds": round(time.perf_counter() - started, 4)}
        if "mapped_symbols" in case["expected"]:
            result["missing_from_map"] = sorted(set(case["expected"]["mapped_symbols"]) - ir["nodes"].keys())
        return result


def python_reference_symbols(path, text):
    """Reference every named definition, including conditional and nested ones.

    The reference uses Python's complete AST walk, without codemap's extraction
    helpers. Lambda expressions and implicit/computed runtime declarations are
    outside this metric. This measures declaration extraction, not call accuracy.
    """
    tree = ast.parse(text)
    ids, counts = [], Counter()

    class Definitions(ast.NodeVisitor):
        def __init__(self):
            self.scope = []

        def definition(self, node, prefix):
            qual = ".".join(self.scope + [node.name])
            key = f"{prefix}:{path}#{qual}"
            counts[key] += 1
            ids.append(key + (f"~{counts[key]}" if counts[key] > 1 else ""))
            self.scope.append(node.name + (f"~{counts[key]}" if counts[key] > 1 else ""))
            self.generic_visit(node)
            self.scope.pop()

        def visit_ClassDef(self, node):
            self.definition(node, "c")

        def visit_FunctionDef(self, node):
            self.definition(node, "f")

        visit_AsyncFunctionDef = visit_FunctionDef

    Definitions().visit(tree)
    return ids


def measure_project(root, name=None):
    started = time.perf_counter()
    root = Path(root).resolve()
    with tempfile.TemporaryDirectory(prefix="codemap-project-measurement-") as output:
        ir, graph = _analyze(root, out=output, repo_mode=True, include_tests=False)
        expected, observed, by_file, reference_errors, generated, hashes = [], [], [], [], [], {}
        for path, fi in sorted(graph.files.items()):
            if fi.lang != "python":
                continue
            try:
                source = read_text(root / path)
                hashes[path] = hashlib.sha256(source.encode("utf-8")).hexdigest()
                reference = python_reference_symbols(path, source)
            except (OSError, SyntaxError, ValueError) as exc:
                reference_errors.append({"file": path, "error": type(exc).__name__})
                continue
            # Route endpoints synthesized for inline registrations are useful
            # graph entities, but they are not named Python declarations.
            generated.extend(s.id for s in fi.symbols if s.sub == "route handler")
            actual = [s.id for s in fi.symbols if s.sub != "route handler"]
            expected.extend(reference)
            observed.extend(actual)
            by_file.append({"file": path, "symbols": score_items(reference, actual)})
        return {"id": name or root.name, "provenance": "local real source; Python AST reference",
                "files": ir["stats"]["files"], "languages": ir["stats"]["languages"],
                "symbols": ir["stats"]["symbols"], "edges_unscored": ir["stats"]["edges"],
                "parse_errors": ir["stats"]["parse_errors"], "reference_errors": reference_errors,
                "generated_entities_unscored": generated,
                "source_sha256": hashes,
                "python_symbols": score_items(expected, observed), "python_files": by_file,
                "seconds": round(time.perf_counter() - started, 4),
                "limits": "Only eligible mapped Python files have declaration reference truth. Generated route entities, calls, risks, other languages and skipped files are unscored."}


def measure_stdlib():
    """A small real CPython sample, pinned by installed version and content hashes."""
    base = Path(sysconfig.get_path("stdlib"))
    candidates = ("json/encoder.py", "http/server.py", "asyncio/queues.py", "logging/handlers.py",
                  "concurrent/futures/_base.py", "tomllib/_parser.py")
    selected = {p: read_text(base / p) for p in candidates if (base / p).is_file()}
    if not selected:
        return {"id": "cpython-stdlib-sample", "error": "Installed Python has no available sample source files."}
    with tempfile.TemporaryDirectory(prefix="codemap-stdlib-reference-") as folder:
        root = Path(folder)
        _write_sources(root, selected)
        result = measure_project(root, "cpython-stdlib-sample")
    result["python_version"] = platform.python_version()
    result["source_sha256"] = {p: hashlib.sha256(text.encode("utf-8")).hexdigest() for p, text in selected.items()}
    result["limits"] += " The six-file subset does not reproduce full-project import resolution."
    return result


def dataset_digest(cases):
    payload = json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def make_baseline(report):
    """Store individual misses, not rounded scores that could hide regressions."""
    return {"schema_version": 1, "dataset_sha256": report["dataset_sha256"],
            "cases": [{"id": case["id"], "metrics": {metric: {
                "missing": case["metrics"][metric]["missing"],
                "unexpected": case["metrics"][metric]["unexpected"]} for metric in METRICS},
                "parse_errors": case["parse_errors"], "missing_from_map": case.get("missing_from_map", [])}
                for case in report["cases"]]}


def baseline_regressions(report, baseline):
    if baseline.get("schema_version") != 1:
        return ["Unsupported baseline schema."]
    if baseline.get("dataset_sha256") != report["dataset_sha256"]:
        return ["Dataset changed; review expectations and explicitly refresh the baseline."]
    before = {case["id"]: case for case in baseline["cases"]}
    regressions = []
    for case in report["cases"]:
        old = before.get(case["id"])
        if old is None:
            regressions.append(case["id"] + ": missing baseline case")
            continue
        for metric in METRICS:
            for kind in ("missing", "unexpected"):
                added = {_item(x) for x in case["metrics"][metric][kind]} - {_item(x) for x in old["metrics"][metric][kind]}
                if added:
                    regressions.append(f"{case['id']}: {len(added)} new {metric} {kind}")
        if set(case["parse_errors"]) - set(old["parse_errors"]):
            regressions.append(case["id"] + ": new parse errors")
        if set(case.get("missing_from_map", [])) - set(old.get("missing_from_map", [])):
            regressions.append(case["id"] + ": newly omitted mapped symbols")
    return regressions


def _percent(value):
    return "unscored" if value is None else f"{100 * value:.1f}%"


def markdown_report(report):
    out = ["# Codemap accuracy measurements", "",
           "Authored fixtures have manually specified expected symbols, links and risks. Real-source Python checks use an independent AST declaration reference.",
           "These samples measure limited capabilities; they do not establish general code understanding or security coverage.", "",
           "| Authored fixture metric | Correct | Unexpected | Missing | Precision | Recall |",
           "|---|---:|---:|---:|---:|---:|"]
    for metric, score in report["summary"].items():
        out.append(f"| {metric} | {score['tp']} | {score['fp']} | {score['fn']} | {_percent(score['precision'])} | {_percent(score['recall'])} |")
    out += ["", "## Misses and unexpected findings", ""]
    for case in report["cases"]:
        failures = [f"{metric}: {score['fn']} missing, {score['fp']} unexpected" for metric, score in case["metrics"].items() if score["fn"] or score["fp"]]
        if failures or case["parse_errors"] or case.get("missing_from_map"):
            notes = case.get("notes", "")
            notes = " ".join(notes) if isinstance(notes, list) else notes
            out.append(f"- **{case['id']}**: " + "; ".join(failures or ["parse/map coverage problem"]) + ". " + notes)
    if not any(s["fn"] or s["fp"] for case in report["cases"] for s in case["metrics"].values()):
        out.append("No symbol/link/risk mismatches in this authored fixture set.")
    for project in report.get("projects", []):
        out += ["", "## " + project["id"], ""]
        if "error" in project:
            out.append(project["error"])
            continue
        score = project["python_symbols"]
        out.append(f"{project['files']} mapped files; Python declarations: {score['tp']} matched, {score['fn']} missing, {score['fp']} unexpected. Precision {_percent(score['precision'])}, recall {_percent(score['recall'])}.")
        out.append(project["limits"])
    out += ["", "Detailed identities, source hashes, environment and per-case results are in the adjacent JSON report."]
    return "\n".join(out) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("cases"))
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("results") / "latest.json")
    parser.add_argument("--stdlib", action="store_true", help="measure six installed CPython source files against AST declarations")
    parser.add_argument("--project", action="append", type=Path, default=[], help="measure a local project; repeatable, static inspection only")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-baseline", type=Path, help="explicitly save current fixture misses as a reviewed starting point")
    parser.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args(argv)
    if args.fail_on_regression and not args.baseline:
        parser.error("--fail-on-regression requires --baseline")
    cases = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(args.cases.glob("*.json"))]
    if not cases or len({case["id"] for case in cases}) != len(cases):
        parser.error("the case folder must contain fixtures with unique ids")
    results = [run_case(case) for case in cases]
    report = {"schema_version": 1, "dataset_sha256": dataset_digest(cases),
              "environment": {"python": platform.python_version(), "platform": platform.system(), "codemap": VERSION},
              "cases": results, "summary": aggregate(results), "projects": []}
    if args.stdlib:
        report["projects"].append(measure_stdlib())
    report["projects"].extend(measure_project(p) for p in args.project)
    regressions = baseline_regressions(report, json.loads(args.baseline.read_text(encoding="utf-8"))) if args.baseline else []
    report["regressions"] = regressions
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.out.with_suffix(".md").write_text(markdown_report(report), encoding="utf-8")
    if args.write_baseline:
        args.write_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.write_baseline.write_text(json.dumps(make_baseline(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Measured {len(results)} authored fixtures and {len(report['projects'])} real-source samples.")
    for metric, score in report["summary"].items():
        print(f"  {metric}: precision {_percent(score['precision'])}, recall {_percent(score['recall'])}; {score['fp']} unexpected, {score['fn']} missing")
    for regression in regressions:
        print("  regression: " + regression)
    print("Report: " + str(args.out))
    return 1 if args.fail_on_regression and regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
