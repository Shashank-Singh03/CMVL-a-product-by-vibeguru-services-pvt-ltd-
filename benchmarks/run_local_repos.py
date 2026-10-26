"""Exercise codemap on existing checkouts without writing into those checkouts."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import run_accuracy as accuracy
from codemap_lib import VERSION, pipeline
from codemap_lib.graph import CodeGraph
from codemap_lib.util import read_text


def git(root, *args):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    return result.stdout.strip() if result.returncode == 0 else None


def file_sha256(path):
    """Hash large candidates without retaining their complete contents in memory."""
    digest, size = hashlib.sha256(), 0
    with Path(path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def inventory(root):
    located, is_git, sub = pipeline.locate(root)
    paths = pipeline.list_sources(located, is_git, sub, include_tests=True)
    hashes, sizes, errors = {}, {}, []
    for name in paths:
        path = located / name
        try:
            hashes[name], sizes[name] = file_sha256(path)
        except OSError as exc:
            errors.append({"file": name, "error_type": type(exc).__name__})
            continue
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode("utf-8")).hexdigest()
    return {"git": is_git, "revision": git(located, "rev-parse", "HEAD") if is_git else None,
            "status": git(located, "status", "--porcelain", "--untracked-files=all") if is_git else None,
            "source_sha256": hashes, "source_bytes": sizes, "digest": digest, "read_errors": errors}


def inventory_snapshot(root, phase):
    """An unavailable runtime inventory is a recorded gap, never unchanged input.

    Initial manifest validation remains strict. This handles a directory or its
    contents becoming unavailable after that validation has already succeeded.
    """
    try:
        if not Path(root).is_dir():
            raise FileNotFoundError
        snapshot = inventory(root)
        if not Path(root).is_dir():
            raise FileNotFoundError
    except (OSError, pipeline.CodemapError, pipeline.GitError, subprocess.SubprocessError) as exc:
        return None, {"phase": phase, "error_type": type(exc).__name__,
                      "error": "Input inventory is unavailable; source integrity could not be verified."}
    if snapshot.get("read_errors"):
        return snapshot, {"phase": phase, "error_type": "SourceReadError",
                          "read_error_count": len(snapshot["read_errors"]),
                          "error": "Some source candidates could not be fingerprinted; source integrity is unknown."}
    if snapshot["git"] and snapshot.get("status") is None:
        return snapshot, {"phase": phase, "error_type": "GitStatusUnavailable",
                          "error": "Working-tree status is unavailable; source integrity could not be verified."}
    return snapshot, None


def graph_invariants(ir):
    nodes, views = ir["nodes"], ir["views"]
    errors = []
    for view in views:
        if len(view["children"]) > 9:
            errors.append(view["id"] + ": more than 9 primary boxes")
        for nid in view["children"] + view.get("ghosts", []):
            if nid not in nodes:
                errors.append(view["id"] + ": missing node " + nid)
        for edge in view["edges"]:
            if edge[0] not in nodes or edge[1] not in nodes:
                errors.append(view["id"] + ": dangling edge")
    for nid, node in nodes.items():
        if node.get("parent") and node["parent"] not in nodes:
            errors.append(nid + ": missing parent")
    return errors


def _run_map_inprocess(root, out, **options):
    started = time.perf_counter()
    captured = {}

    class RecordingGraph(CodeGraph):
        def build(self):
            result = super().build()
            captured["graph"] = result
            return result

    try:
        with patch.object(pipeline, "CodeGraph", RecordingGraph):
            ir = pipeline.analyze(root, out=out, tier="free", log=lambda _: None, **options)
        graph = captured["graph"]
        snapshot = {"symbols": [{"id": s.id, "file": s.file, "line": s.line, "end": s.end,
                                  "kind": s.kind, "sub": s.sub, "status": s.status}
                                 for s in graph.syms.values()],
                    "edges": [{"from": a, "to": b, "kind": data[0], "weight": data[1], "confidence": data[2]}
                              for (a, b), data in sorted(graph.edges.items())]}
        (out / "graph.json").write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        expected, observed, reference_errors = [], [], []
        if options.get("repo_mode") and not options.get("audit"):
            for name, fi in graph.files.items():
                if fi.lang != "python":
                    continue
                try:
                    expected.extend(accuracy.python_reference_symbols(name, read_text(Path(ir["root"]) / name)))
                    observed.extend(s.id for s in fi.symbols if s.sub != "route handler")
                except (OSError, SyntaxError, ValueError) as exc:
                    reference_errors.append({"file": name, "error": type(exc).__name__})
        score = accuracy.score_items(expected, observed)
        return {"ok": True, "seconds": round(time.perf_counter() - started, 3), "scope": ir["scope"],
                "files": ir["stats"]["files"], "symbols": ir["stats"]["symbols"], "edges": ir["stats"]["edges"],
                "languages": ir["stats"]["languages"], "domains": ir["stats"]["domains"],
                "views": len(ir["views"]),
                "parse_errors": [name + ": parser rejected source" for name, fi in graph.files.items() if fi.error],
                "review_status": ir.get("review", {}).get("status", "not_requested"),
                "other_files": ir["other_files"], "risks": ir["stats"]["risks"],
                "risk_locations": [{k: f[k] for k in ("rule", "sev", "file", "line")} for f in ir["risks"]],
                "invariant_errors": graph_invariants(ir), "python_declarations": score if expected or observed else None,
                "reference_errors": reference_errors}
    except Exception as exc:
        return {"ok": False, "seconds": round(time.perf_counter() - started, 3),
                "error_type": type(exc).__name__, "error": "Analysis failed; inspect this checkout locally."}


def _worker_argv():
    return [sys.executable, str(Path(__file__).resolve()), "--worker"]


def _stop_worker(process):
    """Stop the owned analysis process and its children, never target programs."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if process.poll() is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    if process.stdin is not None:
        process.stdin.close()


def run_map(root, out, timeout=0, **options):
    """Run one stage, optionally in a deadline-limited trusted Python worker."""
    root, out = Path(root).resolve(), Path(out).resolve()
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("timeout must be a finite nonnegative number")
    if not timeout:
        return _run_map_inprocess(root, out, **options)
    started = time.perf_counter()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".runner-", dir=out.parent) as temp:
        result_path = Path(temp) / "result.json"
        job = {"root": str(root), "out": str(out), "result": str(result_path), "options": options}
        popen_options = {"stdin": subprocess.PIPE, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                         "text": True, "encoding": "utf-8"}
        if os.name == "nt":
            popen_options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_options["start_new_session"] = True
        process = None
        try:
            process = subprocess.Popen(_worker_argv(), **popen_options)
            process.communicate(json.dumps(job), timeout=timeout)
            if process.returncode != 0 or not result_path.is_file() or result_path.stat().st_size > 50_000_000:
                return {"ok": False, "seconds": round(time.perf_counter() - started, 3),
                        "error_type": "WorkerError", "error": "Analysis worker did not return a valid result.",
                        "worker_exit_code": process.returncode}
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise ValueError("invalid worker response")
            result["seconds"] = round(time.perf_counter() - started, 3)
            result["timeout_seconds"] = timeout
            return result
        except subprocess.TimeoutExpired:
            if process is not None:
                _stop_worker(process)
            return {"ok": False, "seconds": round(time.perf_counter() - started, 3),
                    "error_type": "TimeoutExpired", "error": "Analysis exceeded the stage timeout.",
                    "timeout_seconds": timeout}
        except (OSError, ValueError) as exc:
            return {"ok": False, "seconds": round(time.perf_counter() - started, 3),
                    "error_type": "WorkerError", "error": "Analysis worker could not complete.",
                    "worker_error_type": type(exc).__name__}
        finally:
            if process is not None and process.poll() is None:
                _stop_worker(process)


def _outside_target(root, out):
    """Also guard a parent Git checkout when the selected target is a subfolder."""
    located, _, _ = pipeline.locate(root)
    try:
        out.relative_to(located.resolve())
    except ValueError:
        return
    raise ValueError("output must be outside every tested checkout")


def _worker_main():
    # This private entry point executes this runner, never repository code.
    try:
        job = json.loads(sys.stdin.read(2_000_001))
        root, out, result = (Path(job[k]).resolve() for k in ("root", "out", "result"))
        if not root.is_dir() or not isinstance(job.get("options"), dict):
            return 2
        _outside_target(root, out)
        if result.name != "result.json" or not result.parent.name.startswith(".runner-") or result.parent.parent != out.parent:
            return 2
        response = _run_map_inprocess(root, out, **job["options"])
        result.write_text(json.dumps(response, allow_nan=False) + "\n", encoding="utf-8")
        return 0
    except Exception:
        return 2


def summarize(report):
    runs = [p[k] for p in report["projects"] for k in ("map", "audit", "diff") if p.get(k) is not None]
    failed = sum(not r["ok"] for r in runs)
    invariant_errors = sum(len(r.get("invariant_errors", [])) for r in runs)
    unavailable = sum(bool(p.get("inventory_errors")) for p in report["projects"])
    out = ["# Codemap: local repository evaluation", "",
           "Existing local checkouts were inspected statically. Their applications, build scripts and tests were not executed.",
           "Outputs are stored in this report folder, outside the tested checkouts. Counts describe what codemap mapped, not independently established completeness.", "",
           f"{len(runs)} analysis runs; {failed} failed; {invariant_errors} structural invariant errors; {unavailable} projects with unavailable input inventories. [Reviewed findings](findings.md) include source-first accuracy checks and known misses.", "",
           "| Project | Mapped files | Symbols | Links | Views | Map seconds | Parse errors | Audit H/M/L | Input unchanged |",
           "|---|---:|---:|---:|---:|---:|---:|---|---|"]
    for project in report["projects"]:
        data, audit = project.get("map"), project.get("audit") or {}
        risk = audit.get("risks", {})
        flags = "/".join(str(risk.get(s, "?")) for s in ("high", "medium", "low"))
        unchanged = project.get("input_unchanged", "pending")
        if unchanged is None:
            unchanged = "unknown"
        if data is None:
            state = "unavailable" if project.get("inventory_errors") else "pending"
            out.append(f"| {project['id']} | {state} | | | | | | {flags} | {unchanged} |")
        elif data["ok"]:
            out.append(f"| [{project['id']}](maps/{project['id']}/codemap.html) | {data['files']} | {data['symbols']} | {data['edges']} | {data['views']} | {data['seconds']:.2f} | {len(data['parse_errors'])} | {flags} | {unchanged} |")
        else:
            out.append(f"| {project['id']} | failed | | | | {data['seconds']:.2f} | {data['error_type']} | {flags} | {unchanged} |")
    out += ["", "## Python declaration reference", "",
            "Only named Python definitions have an independent AST reference here. Calls, security coverage and declarations in other languages are unscored unless separately annotated.", ""]
    for project in report["projects"]:
        score = (project.get("map") or {}).get("python_declarations")
        if score:
            out.append(f"- **{project['id']}**: {score['tp']} matched, {score['fn']} missing, {score['fp']} unexpected declarations; recall {accuracy._percent(score['recall'])}.")
    out += ["", "## Change-map runs", ""]
    for project in report["projects"]:
        diff = project.get("diff")
        if diff is None:
            if any(issue["phase"] == "before" for issue in project.get("inventory_errors", [])):
                out.append(f"- **{project['id']}**: not run because the input inventory was unavailable.")
            elif not project.get("before", {}).get("git", False):
                out.append(f"- **{project['id']}**: no Git checkout; no changes were manufactured in the original project.")
            else:
                out.append(f"- **{project['id']}**: pending.")
        elif diff["ok"]:
            scope = diff["scope"]
            if scope["mode"] == "diff":
                description = f"{scope['label']}; {scope['files']} changed files, +{scope['add']}/-{scope['del']}"
            else:
                description = f"whole-repository fallback ({scope['reason']}); no change comparison available"
            out.append(f"- **{project['id']}**: {description}, {diff['seconds']:.2f}s. [Map](diffs/{project['id']}/codemap.html)")
        else:
            out.append(f"- **{project['id']}**: {diff['error_type']}: {diff['error']}")
    if unavailable:
        out += ["", "## Unavailable input inventories", "",
                "A checkout or some of its files became unavailable after initial validation. Integrity is unknown for these projects; this does not establish that the analyzer changed their source. Completed stage results are retained, and these projects will be retried on resume when their inputs are available.", ""]
        for project in report["projects"]:
            for issue in project.get("inventory_errors", []):
                out.append(f"- **{project['id']}**, {issue['phase']} analysis: {issue['error_type']}. {issue['error']}")
    out += ["", "## Limits", "",
            "Selection is a convenience sample of the user's existing projects. Zero parse errors is not proof of correct symbols or links. Audit warnings need human review and are not confirmed vulnerabilities. Timings include output rendering on one Windows machine and vary with caching/load.",
            "Detailed hashes, revisions, working-tree states and results are in results.json. Source evidence and limited manual checks are stored separately under annotations/."]
    timeout = report.get("run_options", {}).get("timeout_seconds", 0)
    if timeout:
        out += ["", f"Each analysis stage runs in an isolated Python worker with a {timeout:g}-second deadline. Timed-out stages are recorded as failures and other projects continue."]
    if report.get("run_options", {}).get("review_requests"):
        out += ["", "Successful whole-project maps prepare structural review requests for the user's agent. A pending request is not a completed model review."]
    return "\n".join(out) + "\n"


def _fingerprints():
    app = {str(p.relative_to(accuracy.ROOT)): file_sha256(p)[0]
           for p in (accuracy.ROOT / "skills" / "codemap").rglob("*")
           if p.is_file() and p.suffix in (".py", ".html")}
    runner = {p.name: file_sha256(p)[0] for p in (Path(__file__), Path(accuracy.__file__))}
    return app, runner


def _atomic_text(path, text):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".runner-save-", delete=False) as stream:
        stream.write(text)
        temporary = Path(stream.name)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def checkpoint(report, out):
    _atomic_text(out / "results.json", json.dumps(report, indent=2, allow_nan=False) + "\n")
    _atomic_text(out / "report.md", summarize(report))


def _inventory_matches(before, after):
    return (not before.get("read_errors") and not after.get("read_errors")
            and all(before.get(k) == after.get(k) for k in ("digest", "revision", "status")))


def _can_resume(project, root, current, out=None, review_requests=False):
    if not project.get("completed") or not project.get("input_unchanged") or project.get("path") != str(root):
        return False
    stages = [project.get("map"), project.get("audit")]
    if current["git"]:
        stages.append(project.get("diff"))
    if out is not None:
        folders = ["maps", "audits"] + (["diffs"] if current["git"] else [])
        if any(not (out / folder / project["id"] / name).is_file()
               for folder in folders for name in ("codemap.html", "ir.json", "graph.json")):
            return False
        if review_requests and any(not (out / "maps" / project["id"] / name).is_file()
                                   for name in ("review.md", "review-request.json")):
            return False
    return (all(isinstance(stage, dict) and stage.get("ok") is True for stage in stages)
            and _inventory_matches(project.get("before", {}), current)
            and project.get("after_digest") == current["digest"]
            and project.get("after_status") == current["status"]
            and project.get("after_revision") == current["revision"])


def _validated_projects(items, out):
    if not isinstance(items, list):
        raise ValueError("manifest must be a JSON array")
    projects, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not re.fullmatch(r"[a-z0-9-]+", item["id"]) or item["id"] in seen:
            raise ValueError("project IDs must be unique lowercase slugs")
        if not isinstance(item.get("path"), str):
            raise ValueError("each project must have a local directory path")
        root = Path(item["path"]).resolve()
        if not root.is_dir():
            raise ValueError("every project root must be an existing directory")
        _outside_target(root, out)
        projects.append({"id": item["id"], "root": root})
        seen.add(item["id"])
    if out.exists() and not out.is_dir():
        raise ValueError("output must be a directory")
    return projects


def main(argv=None):
    if (sys.argv[1:] if argv is None else argv) == ["--worker"]:
        return _worker_main()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="JSON array of {id, path} local checkouts")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=0, help="seconds per isolated analysis stage; 0 keeps in-process behavior")
    parser.add_argument("--resume", action="store_true", help="reuse completed projects only when code, inputs and review mode still match")
    parser.add_argument("--review-requests", action="store_true", help="prepare pending structural model review requests for whole-project maps")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout < 0:
        parser.error("timeout must be a finite nonnegative number")
    args.out = args.out.resolve()
    try:
        projects = _validated_projects(json.loads(args.manifest.read_text(encoding="utf-8")), args.out)
    except (OSError, ValueError, pipeline.CodemapError):
        parser.error("invalid manifest, project root, or output location; output must be outside every tested checkout")
    app, runner = _fingerprints()
    run_options = {"timeout_seconds": args.timeout, "review_requests": args.review_requests}
    previous = {}
    if args.resume and (args.out / "results.json").is_file():
        try:
            old = json.loads((args.out / "results.json").read_text(encoding="utf-8"))
            if (old.get("app_sha256") == app and old.get("runner_sha256") == runner
                    and old.get("run_options", {}).get("review_requests", False) == args.review_requests):
                previous = {p["id"]: p for p in old.get("projects", []) if isinstance(p, dict) and isinstance(p.get("id"), str)}
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    args.out.mkdir(parents=True, exist_ok=True)
    report = {"environment": {"python": platform.python_version(), "platform": platform.platform(), "codemap": VERSION},
              "app_sha256": app, "runner_sha256": runner, "run_options": run_options, "projects": [], "status": "running"}
    checkpoint(report, args.out)
    try:
        for item in projects:
            root = item["root"]
            before, before_issue = inventory_snapshot(root, "before")
            if before_issue is not None:
                result = {"id": item["id"], "path": str(root), "before": before or {}, "map": None, "audit": None,
                          "diff": None, "completed": False, "input_unchanged": None, "resumed": False,
                          "inventory_errors": [before_issue], "status": "unavailable", "integrity": "unknown"}
                report["projects"].append(result)
                checkpoint(report, args.out)
                print(f"{item['id']}: input inventory unavailable before analysis; integrity unknown", flush=True)
                continue
            prior = previous.get(item["id"], {})
            if _can_resume(prior, root, before, args.out, args.review_requests):
                result = dict(prior, resumed=True)
                report["projects"].append(result)
                checkpoint(report, args.out)
                print(f"{item['id']}: reused completed result; inputs unchanged", flush=True)
                continue
            result = {"id": item["id"], "path": str(root), "before": before, "map": None, "audit": None,
                      "diff": None, "completed": False, "input_unchanged": None, "resumed": False,
                      "inventory_errors": [], "status": "running", "integrity": "unknown"}
            report["projects"].append(result)
            checkpoint(report, args.out)
            stages = [("map", "maps", {"repo_mode": True, "structural_review": args.review_requests}),
                      ("audit", "audits", {"repo_mode": True, "audit": True})]
            if before["git"]:
                stages.append(("diff", "diffs", {}))
            for key, folder, options in stages:
                result["active_stage"] = key
                checkpoint(report, args.out)
                print(f"{item['id']}: {key} started", flush=True)
                result[key] = run_map(root, args.out / folder / item["id"], timeout=args.timeout, **options)
                result["active_stage"] = None
                checkpoint(report, args.out)
            after, after_issue = inventory_snapshot(root, "after")
            if after_issue is not None:
                result.update(status="unverified", integrity="unknown", inventory_errors=[after_issue],
                              after_digest=after["digest"] if after else None,
                              after_status=after["status"] if after else None,
                              after_revision=after["revision"] if after else None,
                              after_read_errors=after.get("read_errors", []) if after else [])
                checkpoint(report, args.out)
                print(f"{item['id']}: input inventory unavailable after analysis; stage results retained, integrity unknown", flush=True)
                continue
            result.update(input_unchanged=_inventory_matches(before, after), completed=True,
                          status="complete", integrity="unchanged" if _inventory_matches(before, after) else "changed",
                          after_digest=after["digest"], after_status=after["status"], after_revision=after["revision"],
                          after_read_errors=after.get("read_errors", []))
            checkpoint(report, args.out)
            data = result["map"]
            print(f"{item['id']}: " + (f"{data['files']} files, {data['symbols']} symbols, {data['edges']} links, {data['seconds']:.2f}s" if data['ok'] else data['error_type']) + f"; unchanged={result['input_unchanged']}", flush=True)
    except KeyboardInterrupt:
        report["status"] = "interrupted"
        checkpoint(report, args.out)
        return 130
    report["status"] = "complete"
    checkpoint(report, args.out)
    print("Report: " + str(args.out / "report.md"))
    runs = [p[k] for p in report["projects"] for k in ("map", "audit", "diff") if p[k] is not None]
    passed = all(r["ok"] and not r.get("invariant_errors") and not r.get("parse_errors") for r in runs)
    return 0 if passed and all(p["input_unchanged"] for p in report["projects"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
