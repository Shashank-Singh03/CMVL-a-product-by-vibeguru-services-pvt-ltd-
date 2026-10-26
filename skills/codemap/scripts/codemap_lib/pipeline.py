"""The three pipelines: analyze (zero tokens), render (merge labels), video (MP4)."""
from __future__ import annotations

import os
import time
from collections import Counter
from pathlib import Path

from . import MAX_VIEW, VERSION
from .changes import GitError, Scope, collect_changes, git, mark as record_baseline, repo_root, resolve_scope
from .diffmap import CHANGED, apply_changes, changed_symbols, own_status
from .graph import CodeGraph
from .formats import classify
from .hierarchy import MapBuilder
from .labels import apply_auto, apply_cache, assign_short_ids, build_worksheet, merge_labels, update_cache
from .render import ASSETS, write_outputs
from .redact import SecretRedactor
from .risks import SEVERITY_ORDER, RiskScanner, summarize
from .routes import apply_routes
from .scan import keep, parse_files, walk_files
from .story import narrate, plan_beats
from .util import read_json, read_text, sha1, write_json, write_text


class CodemapError(RuntimeError):
    pass


SKIP_NOTES = {"too_large": "File exceeds the snapshot text size limit; inspect separately.",
              "unreadable": "File could not be read; inspect separately.",
              "binary": "File contains binary data; inspect separately.",
              "not_file": "Path is no longer a regular file; inspect separately.",
              "not_scanned": "Existing path was excluded from snapshot discovery; inspect separately.",
              "baseline_unavailable": "Baseline text is unavailable; inspect separately."}


def locate(target: Path) -> tuple:
    """(root, is_git, sub-path) for a target folder."""
    target = Path(target).resolve()
    if not target.exists():
        raise CodemapError(f"no such folder: {target}")
    base = target if target.is_dir() else target.parent
    root = repo_root(base)
    if root:
        root = root.resolve()
        sub = base.relative_to(root).as_posix() if base != root else ""
        return root, True, "" if sub == "." else sub
    return base, False, ""


def out_dir_for(target, out=None) -> Path:
    if out:
        return Path(out).resolve()
    root, _, _ = locate(Path(target))
    return root / ".codemap"


def _ensure_out(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    ignore = out_dir / ".gitignore"
    if not ignore.exists():
        write_text(ignore, "# codemap output - safe to delete\n*\n")


def _rel(path: Path) -> str:
    try:
        rel = os.path.relpath(path)
    except ValueError:
        return str(path)
    return str(path) if rel.startswith("..") else rel


def list_sources(root: Path, is_git: bool, sub: str, include_tests: bool) -> list:
    if is_git:
        out = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", sub or ".")
        paths = [p for p in out.split("\0") if p]
    else:
        paths = walk_files(root, sub)
    return sorted(p for p in paths if keep(p, include_tests) and (root / p).is_file())


def _why_from_commits(commits: list) -> list:
    if not commits:
        return []
    subjects = [c.splitlines()[0].strip() for c in commits if c.strip()]
    if len(subjects) == 1:
        text = commits[0].strip().replace("\n", " ")[:300]
    else:
        text = f"{len(subjects)} commits: " + "; ".join(subjects[:5])[:300]
    return [{"source": "commits", "text": text}]


def _add_source(redactor: SecretRedactor, path: str, text: str) -> None:
    name = Path(path).name.lower()
    _fmt, domain = classify(path, text[:4000])
    redactor.add_text(text, env=name == ".env" or name.startswith(".env.") or name.endswith(".env"),
                      config=domain in ("config", "infra", "ci"))


def _learn_changed_sources(root: Path, changes, redactor: SecretRedactor) -> None:
    # Full-diff export includes collected edits beyond the map's 600 KB parsing
    # limit. Stream their source too, so an isolated changed key-body line has
    # the same redaction context as a small mapped file.
    for path, fd in changes.files.items():
        source = root / path
        if fd.status == "deleted" or source.is_symlink():
            continue
        try:
            with source.open(encoding="utf-8-sig", errors="replace") as stream:
                head = stream.read(4000)
                stream.seek(0)
                fmt, domain = classify(path, head)
                redactor.add_lines(stream, env=fmt == "env", config=domain in ("config", "infra", "ci"))
        except OSError as exc:
            raise CodemapError(f"cannot safely prepare the review diff; source is unreadable: {path}") from exc


def _render_redactor(ir: dict) -> SecretRedactor:
    redactor = SecretRedactor()
    redactor.add_object(ir)
    root = Path(ir.get("root") or ".").resolve()
    paths = {n["file"] for n in ir.get("nodes", {}).values() if n.get("file")}
    # Display paths can themselves be redacted; stable IDs retain the original
    # source identity for subsequent renders without changing graph references.
    paths.update(nid.split(":", 1)[1].split("#", 1)[0]
                 for nid in ir.get("nodes", {}) if nid.startswith(("f:", "c:", "m:")))
    for path in sorted(paths):
        source = (root / path).resolve()
        try:
            source.relative_to(root)
            if source.is_file() and source.stat().st_size <= 600_000:
                _add_source(redactor, path, read_text(source))
        except (OSError, ValueError):
            continue
    return redactor


def _redact_saved_inputs(out_dir: Path, redactor: SecretRedactor) -> None:
    saved = {}
    for name in ("cache.json", "labels.json", "labels.previous.json", "review.json", "review.applied.json",
                 "review-report.json", "review-request.json"):
        path = out_dir / name
        if path.exists():
            saved[path] = read_json(path, {})
            redactor.add_object(saved[path])
    worksheets = {p: read_text(p) for p in (out_dir / "worksheet.md", out_dir / "review.md") if p.exists()}
    for text in worksheets.values():
        redactor.add_text(text)
    for path, value in saved.items():
        trusted_review = path.name in ("review-request.json", "review-report.json")
        write_json(path, redactor.clean_review(value) if trusted_review else redactor.clean(value))
    for path, text in worksheets.items():
        write_text(path, redactor.text(text))
    diff = out_dir / "codemap.diff"
    if diff.exists():
        # Older artifacts can be re-rendered after labels introduce another
        # recognized credential. Re-sanitize the review diff as well.
        write_text(diff, redactor.text(read_text(diff)))


def _review_diff(changes) -> str:
    """All collected changed lines, without the per-symbol display cap.

    Redaction can alter values and multiline keys, so this artifact is intended
    for review rather than applying as a patch. Scope/file exclusions still apply.
    """
    lines = ["# codemap: complete collected text changes (recognized credentials redacted).",
             "# Review only: this redacted diff is not an apply-ready patch.", ""]
    for skipped in getattr(changes, "skipped", []):
        lines.append(f"# Omitted {skipped['path']}: " + SKIP_NOTES.get(skipped["reason"], "Text unavailable."))
    for path, fd in sorted(changes.files.items()):
        lines.append(f"diff --git a/{fd.old_path} b/{path}")
        if fd.status == "renamed":
            lines.extend([f"rename from {fd.old_path}", f"rename to {path}"])
        lines.extend(["--- " + ("/dev/null" if fd.status == "added" else "a/" + fd.old_path),
                      "+++ " + ("/dev/null" if fd.status == "deleted" else "b/" + path)])
        for hunk in fd.hunks:
            lines.append(f"@@ -{hunk.old_start},{hunk.old_len} +{hunk.new_start},{hunk.new_len} @@")
            lines.extend("-" + text for _line, text in hunk.minus)
            lines.extend("+" + text for _line, text in hunk.plus)
        lines.append("")
    return "\n".join(lines) + "\n"


def _source_base(root: Path, out_dir: Path) -> str | None:
    try:
        relative = os.path.relpath(root, out_dir).replace(os.sep, "/")
    except ValueError:  # different Windows volumes
        return None
    return relative.rstrip("/") + "/"


def _scope_state(scope, out_dir):
    """Pin exact scope semantics, including marked untracked-file snapshots."""
    import hashlib
    import json
    from dataclasses import asdict
    state = {"scope": asdict(scope)}
    if scope.reason == "codemap mark":
        baseline = read_json(out_dir / "baseline.json") or {}
        snapshots = []
        snapshot_root = (out_dir / "baseline").resolve()
        for name in baseline.get("files", []):
            source = (snapshot_root / name).resolve()
            try:
                source.relative_to(snapshot_root)
            except ValueError as exc:
                raise CodemapError("unsafe baseline path; regenerate the baseline") from exc
            snapshots.append((name, hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None))
        state["baseline_digest"] = hashlib.sha256(json.dumps([baseline, snapshots], sort_keys=True).encode()).hexdigest()
    return state


def analyze(target=".", out=None, base=None, repo_mode=False, tier=None, budget=None, minutes=2.0,
            include_tests=None, audit=False, log=print, structural_review=None, review_patch=None, review_scope=None,
            review_excluded_paths=(), isolated=False, project_name=None, isolated_files=None) -> dict:
    t0 = time.time()
    if isolated:
        # A task snapshot can be nested inside an ignored directory of a Git
        # checkout. Never climb back into that live checkout while explaining
        # a frozen task, including when replaying its structural review.
        root, is_git, sub = Path(target).resolve(), False, ""
        if not root.is_dir():
            raise CodemapError("the captured task source is unavailable")
    else:
        root, is_git, sub = locate(Path(target))
    out_dir = Path(out).resolve() if out else root / ".codemap"
    if out_dir == root:
        raise CodemapError("use a dedicated output folder rather than the project root")
    _ensure_out(out_dir)
    try:
        output_prefix = out_dir.relative_to(root).as_posix() + "/"
    except ValueError:
        output_prefix = None
    def excluded(path):
        return ((output_prefix is not None and path.startswith(output_prefix)) or path in review_excluded_paths)
    try:
        if review_scope is not None:
            import re
            scope = Scope(**review_scope["scope"])
            if (scope.mode not in ("repo", "diff") or scope.kind not in ("git", "files", "")
                    or (scope.mode == "diff" and scope.kind == "git" and not re.fullmatch(r"[0-9a-f]{40,64}", scope.base))):
                raise CodemapError("invalid saved review scope; regenerate the analysis")
            if _scope_state(scope, out_dir) != review_scope:
                raise CodemapError("session baseline changed since the review request; regenerate the analysis")
        else:
            scope = resolve_scope(root, is_git, base, repo_mode, out_dir)
    except GitError as exc:
        raise CodemapError(str(exc)) from exc
    diff = scope.mode == "diff"
    tier = tier or ("lite" if diff else "free")
    if include_tests is None:
        include_tests = diff
    if isolated_files is not None:
        if (not isolated or not isinstance(isolated_files, list)
                or any(not isinstance(p, str) or not p or "\\" in p or ":" in p
                       or any(part in ("", ".", "..") for part in p.split("/")) for p in isolated_files)):
            raise CodemapError("invalid captured source inventory")
        candidates = sorted(set(isolated_files))
    else:
        candidates = list_sources(root, is_git, sub, include_tests)
    paths = [path for path in candidates if not excluded(path)]
    changes = collect_changes(root, scope, paths, out_dir, sub) if diff else None
    if changes is not None:
        changes.files = {p: fd for p, fd in changes.files.items() if not excluded(p)}
        changes.skipped = [item for item in changes.skipped if not excluded(item["path"])]
    files, texts = parse_files(root, paths, set(changes.files) if changes else set(), diff_mode=diff, audit=audit)
    if not files and not diff:
        raise CodemapError(f"no files to map under {(root / sub) if sub else root} (source, API, infra, CI or dependency files)")
    route_count = apply_routes(files, texts)
    graph = CodeGraph(files, root=root).build()
    t_parse = time.time() - t0

    scope_info = {"mode": scope.mode, "label": scope.label or "whole repository", "reason": scope.reason,
                  "base": scope.base, "files": 0, "add": 0, "del": 0}
    other, commits = [], []
    scanner = RiskScanner()
    if diff:
        other, base_infos = apply_changes(files, texts, changes, graph)
        graph = CodeGraph(files, root=root).build()
        skipped = getattr(changes, "skipped", [])
        other.extend({"path": item["path"], "status": "skipped", "add": 0, "del": 0,
                      "note": SKIP_NOTES.get(item["reason"], "Text unavailable; inspect separately.")} for item in skipped)
        commits = changes.commits
        scope_info["files"] = len(set(changes.files) | {item["path"] for item in skipped})
        scope_info["add"] = sum(fd.add for fd in changes.files.values())
        scope_info["del"] = sum(fd.dele for fd in changes.files.values())
        if not any(s.status in CHANGED for s in graph.syms.values()) and not any(
                fi.module_add or fi.module_del or fi.status in ("added", "deleted") for fi in files.values()):
            if skipped:
                raise CodemapError(f"no readable changes to map; {len(skipped)} file(s) were skipped "
                                   "because their text is unavailable or exceeds the snapshot size limit")
            hint = "nothing to explain" if not changes.files else "only files codemap cannot read changed"
            raise CodemapError(f"{hint} ({scope.label}). Try --base <ref>, or --repo for a whole-repo map.")
        scanner.scan_changes(files, changes, base_infos, texts)
    elif audit:
        scanner.scan_files(files, texts)

    review_enabled = tier != "free" if structural_review is None else structural_review
    request, review_info = None, {"status": "not_requested"}
    if review_enabled or review_patch is not None:
        from .review import ReviewError, apply_edge_ops, build_request, review_worksheet, validate_review
        request = build_request(files, texts, graph, scope_info)
        review_info = {"status": "pending", "analysis_id": request["analysis_id"], "reviewed_files": [],
                       "accepted_symbols": [], "accepted_edges": [], "rejected": [], "uncertain": []}
        if review_patch is not None:
            try:
                new_symbols, edge_ops, review_info = validate_review(request, files, texts, graph, review_patch)
            except ReviewError as exc:
                raise CodemapError(str(exc)) from exc
            for symbol in new_symbols:
                files[symbol.file].symbols.append(symbol)
                graph.syms[symbol.id] = symbol
            # Only validated edge proposals alter relationships. Adding a model
            # symbol must not silently trigger new global-name guesses.
            apply_edge_ops(graph, edge_ops)

    name = project_name or (sub.rsplit("/", 1)[-1] if sub else root.name) or "repo"
    nodes, views = MapBuilder(graph, files, scope.mode, name).build()
    langs = Counter(fi.lang for fi in files.values())
    domains = Counter(fi.domain for fi in files.values())
    symbols = sum(len(fi.symbols) for fi in files.values())
    flags = sorted(scanner.flags, key=lambda f: (SEVERITY_ORDER[f["sev"]], f["file"], f["line"]))
    ir = {
        "version": VERSION, "generated": time.strftime("%Y-%m-%d %H:%M"), "repo": name, "root": str(root),
        "sub": sub, "scope": scope_info, "tier": tier,
        "stats": {"files": len(files), "symbols": symbols, "edges": len(graph.edges), "languages": dict(langs),
                  "domains": dict(domains), "routes": route_count,
                  "parse_errors": [f"{p}: {fi.error}" for p, fi in files.items() if fi.error][:20],
                  "parse_seconds": round(t_parse, 2), "external": dict(graph.ext_usage.most_common(12)),
                  "risks": summarize(flags)},
        "nodes": nodes, "views": views, "why": _why_from_commits(commits), "commits": commits, "other_files": other,
        "review": review_info,
        "analysis": {"target": str(root / sub), "base": base, "scope": _scope_state(scope, out_dir),
                     "repo_mode": repo_mode, "tier": tier, "budget": budget, "minutes": minutes,
                     "include_tests": include_tests, "audit": audit,
                     "isolated": isolated, "project_name": project_name, "isolated_files": isolated_files},
        "risks": flags,
        "artifacts": {"source_base": _source_base(root, out_dir)},
    }
    no_symbols = sorted(p for p, fi in files.items() if not fi.symbols and fi.status != "deleted")
    omitted = sorted(set(paths) - set(files))
    ir["stats"]["coverage"] = {"candidate_files": len(paths), "without_symbols_count": len(no_symbols),
                                  "without_symbols": no_symbols[:40], "omitted_count": len(omitted),
                                  "omitted_files": omitted[:40]}
    if request is not None:
        ir["artifacts"]["review_request"] = "review.md"
    if isolated:
        ir["artifacts"]["source_kind"] = "snapshot"
    if review_patch is not None:
        ir["artifacts"]["review_report"] = "review-report.json"
    ir["plan"] = plan_beats(ir, minutes)
    ir["short"] = assign_short_ids(ir)
    apply_auto(ir)
    redactor = SecretRedactor()
    for path, text in texts.items():
        _add_source(redactor, path, text)
    if changes:
        _learn_changed_sources(root, changes, redactor)
        for path, fd in changes.files.items():
            _add_source(redactor, fd.old_path, changes.old_text(fd.old_path) or "")
        # The diff includes collected lines that could not be mapped, too.
        for fd in changes.files.values():
            for hunk in fd.hunks:
                for _line, text in hunk.plus + hunk.minus:
                    _add_source(redactor, fd.path, text)
        write_text(out_dir / "codemap.diff", redactor.text(_review_diff(changes)))
        ir["artifacts"]["diff"] = "codemap.diff"
    redactor.add_object(ir)
    _redact_saved_inputs(out_dir, redactor)
    cache = read_json(out_dir / "cache.json", {}) or {}
    redactor.add_object(cache)
    cache = redactor.clean(cache)
    if (out_dir / "cache.json").exists():
        write_json(out_dir / "cache.json", cache)
    hits = apply_cache(ir, cache)
    ir = redactor.clean_ir(ir)
    nodes = ir["nodes"]

    if request is not None:
        write_json(out_dir / "review-request.json", redactor.clean_review(request))
        write_text(out_dir / "review.md", redactor.text(review_worksheet(request)))
    if review_patch is not None:
        write_json(out_dir / "review-report.json", redactor.clean_review(review_info))
        # This includes rejected input; only engine-produced metadata is trusted
        # to contain protocol IDs/enums worth preserving during redaction.
        write_json(out_dir / "review.applied.json", redactor.clean(review_patch))

    stale = out_dir / "labels.json"
    if stale.exists():
        labels = read_json(stale, {})
        redactor.add_object(labels)
        write_json(stale, redactor.clean(labels))
        stale.replace(out_dir / "labels.previous.json")
    ws_path = out_dir / "worksheet.md"
    # Ordinary worksheets are portable across output directories. Automatic
    # handoffs name their isolated task directory explicitly to avoid a write
    # into the live checkout's generic .codemap folder.
    output_hint = _rel(out_dir).replace(os.sep, "/") if isolated else None
    ws_text, ws_items, ws_tokens = ("", 0, 0) if tier == "free" else build_worksheet(ir, tier, budget, output_hint)
    if ws_text:
        write_text(ws_path, redactor.text(ws_text))
    elif ws_path.exists():
        ws_path.unlink()
    write_json(out_dir / "ir.json", ir)
    md, html, _ = render(out_dir, log=None)

    # ---- report (kept short: this text lands in the agent's context) ----
    changed = Counter(own_status(n) for n in changed_symbols(nodes))
    lang_txt = ", ".join(f"{k} {v}" for k, v in langs.most_common(6)) + (" ..." if len(langs) > 6 else "")
    log(f"codemap {VERSION} | {scope_info['label']} ({scope.reason})")
    log(f"parsed {len(files)} files ({lang_txt or 'none'}) in {t_parse:.1f}s | {symbols} symbols, {len(graph.edges)} edges")
    if diff:
        log(f"changes: {scope_info['files']} files +{scope_info['add']} -{scope_info['del']} | symbols: "
            f"{changed['added']} added, {changed['modified']} changed, {changed['deleted']} removed"
            + (f" | {len(other)} unreadable files" if other else ""))
    if flags:
        risk = summarize(flags)
        log(f"risk flags: {risk['high']} high, {risk['medium']} medium, {risk['low']} low"
            f" (top: {flags[0]['msg'][:70]} {flags[0]['file']}:{flags[0]['line']})")
    beats_sec = sum(b.get("est", 0) for b in read_json(out_dir / "ir.json", {}).get("beats", []))
    log(f"map: {len(views)} views (max {MAX_VIEW} boxes each) | walkthrough: {len(ir['plan'])} beats (~{beats_sec / 60:.1f} min)")
    log(f"outputs: {_rel(md)}  {_rel(html)}")
    if ws_text:
        log(f"worksheet: {_rel(ws_path)} ({ws_items} items, ~{ws_tokens / 1000:.1f}k tokens)"
            f"{f', {hits} reused from cache' if hits else ''} -> write {_rel(out_dir / 'labels.json')}, then run `render`")
    elif tier != "free":
        log(f"worksheet: nothing to label ({hits} labels reused from cache)")
    else:
        log("tier free: no LLM used. For AI-written names: --tier lite|full|video")
    if request is not None:
        if review_patch is None:
            log(f"structural review pending: read {_rel(out_dir / 'review.md')} and source, write review.json, then run `review`")
        else:
            log(f"structural review: {len(review_info['accepted_symbols'])} symbols, {len(review_info['accepted_edges'])} edge operations; "
                f"{len(review_info['rejected'])} rejected, {len(review_info['uncertain'])} uncertain. See {_rel(out_dir / 'review-report.json')}")
    return ir


def review(out_dir: Path, patch_path=None, log=print) -> dict:
    """Reparse the same scope, validate the agent's corrections, then rebuild outputs."""
    import json
    out_dir = Path(out_dir).resolve()
    ir = read_json(out_dir / "ir.json")
    if not ir or not ir.get("analysis") or not (out_dir / "review-request.json").exists():
        raise CodemapError("no structural review request found; run `analyze --review` first")
    path = Path(patch_path).resolve() if patch_path else out_dir / "review.json"
    try:
        if path.stat().st_size > 2_000_000:
            raise CodemapError("review JSON exceeds the 2 MB limit; review a smaller scope")
        patch = json.loads(read_text(path))
    except (OSError, ValueError) as exc:
        raise CodemapError(f"could not read review JSON: {exc}") from exc
    expected = read_json(out_dir / "review-request.json") or {}
    if not isinstance(patch, dict) or patch.get("analysis_id") != expected.get("analysis_id"):
        raise CodemapError("review belongs to a different analysis; read the current review.md and source")
    options = dict(ir["analysis"])
    target = options.pop("target")
    pinned_scope = options.pop("scope", None)
    if pinned_scope is None:
        raise CodemapError("analysis lacks a pinned review scope; regenerate with `analyze --review`")
    source_root = Path(ir["root"]).resolve()
    try:
        input_rel = path.relative_to(source_root).as_posix()
    except ValueError:
        input_rel = None
    excluded_inputs = (input_rel,) if input_rel and input_rel not in expected.get("files", {}) else ()
    result = analyze(target, out=out_dir, log=log, structural_review=True, review_patch=patch,
                     review_scope=pinned_scope, review_excluded_paths=excluded_inputs, **options)
    if path != out_dir / "review.json":
        # Keep a redacted local copy; never rewrite a caller's external proposal file.
        write_json(out_dir / "review.json", read_json(out_dir / "review.applied.json"))
    return result


def render(out_dir: Path, log=print) -> tuple:
    out_dir = Path(out_dir)
    ir = read_json(out_dir / "ir.json")
    if not ir:
        raise CodemapError(f"no analysis found in {out_dir}; run `codemap analyze` first")
    info = {"applied": 0, "unknown": []}
    labels = read_json(out_dir / "labels.json")
    redactor = _render_redactor(ir)
    redactor.add_object(labels)
    _redact_saved_inputs(out_dir, redactor)
    ir = redactor.clean_ir(ir)
    if labels:
        labels = redactor.clean(labels)
        write_json(out_dir / "labels.json", labels)
        info = merge_labels(ir, labels)
        cache_path = out_dir / "cache.json"
        cache = read_json(cache_path, {}) or {}
        redactor.add_object(cache)
        cache = redactor.clean(cache)
        update_cache(ir, cache)
        write_json(cache_path, redactor.clean(cache))
    narrate(ir)
    ir = redactor.clean_ir(ir)
    write_json(out_dir / "ir.json", ir)
    from .video import embedded_audio
    md, html = write_outputs(ir, out_dir, audio=embedded_audio(out_dir, ir["beats"]))
    if log:
        if labels:
            extra = f" ({len(info['unknown'])} unknown ids ignored: {', '.join(info['unknown'][:5])})" if info["unknown"] else ""
            log(f"labels: applied {info['applied']} items{extra}")
        log(f"outputs: {_rel(md)}  {_rel(html)}")
    return md, html, info


def video(out_dir: Path, tts="auto", voice=None, rate=0, res=1080, jobs=4, fps=15, webm=False, log=print) -> Path:
    from .video import VideoError, make_video
    out_dir = Path(out_dir)
    if not (out_dir / "ir.json").exists():
        raise CodemapError(f"no analysis found in {out_dir}; run `codemap analyze` first")
    _, html, _ = render(out_dir, log=None)
    ir = read_json(out_dir / "ir.json")
    t0 = time.time()
    log(f"rendering {len(ir['beats'])} beats to MP4 ...")
    template_hash = sha1(read_text(ASSETS / "viewer.html"))[:12]
    try:
        mp4 = make_video(ir, html, out_dir, tts=tts, voice=voice, rate=rate, res=res, jobs=jobs, log=log,
                         template_hash=template_hash, fps=fps, webm=webm)
    except VideoError as exc:
        raise CodemapError(str(exc)) from exc
    render(out_dir, log=None)            # embed the recorded narration into codemap.html
    log(f"video: {_rel(mp4)}" + (f" + {_rel(mp4.with_suffix('.webm'))}" if webm else "") + f" ({time.time() - t0:.1f}s total)")
    if not webm and os.environ.get("TERM_PROGRAM") == "vscode":
        log("tip: VS Code's built-in player mutes AAC audio; open the MP4 in a browser or media player, or add --webm")
    return mp4


def mark(target=".", out=None, clear=False, log=print) -> None:
    root, is_git, _ = locate(Path(target))
    out_dir = Path(out).resolve() if out else root / ".codemap"
    _ensure_out(out_dir)
    if clear:
        for p in (out_dir / "baseline.json",):
            if p.exists():
                p.unlink()
        import shutil
        shutil.rmtree(out_dir / "baseline", ignore_errors=True)
        log("baseline cleared: analyze is back to automatic scope (uncommitted changes / last commit)")
        return
    data = record_baseline(root, out_dir, is_git)
    how = f"git snapshot {data.get('base', '')[:7]}" if data["kind"] == "git" else "file snapshot"
    log(f"baseline recorded ({how}, {len(data['files'])} files copied) at {data['created']}")
    log("later, `codemap analyze` explains exactly what changed since now (`codemap mark --clear` to undo)")
