"""Local task history; public links never expose raw snapshots or state files."""
from __future__ import annotations

import html
import hashlib
import json
import math
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from . import VERSION
from .redact import SecretRedactor
from .formats import CODE_EXT, OTHER_EXT, CI_NAMES, DEPS_FILES, DOMAIN_NAMES

TASK_ID = re.compile(r"[0-9a-f]{32}\Z")
MAX_STATE_BYTES = 16_000_000
MAX_HISTORY_READ_BYTES = 64_000_000
MAX_TASKS = 1_000
MAX_IR_BYTES = 4_000_000
MAX_HISTORY_IR_BYTES = 16_000_000
MAX_HISTORY_LEARNING_BYTES = 1_000_000
DASHBOARD_ASSETS = Path(__file__).resolve().parent / "assets"
PUBLIC_FIELDS = ("task_id", "status", "created_at", "created", "started_at", "updated_at",
                 "map_ready", "video_status", "attribution", "reviewed_files", "capture_omitted_count")
STATUSES = {
    "capturing": "Capturing baseline", "active": "Task in progress", "freezing": "Freezing sources",
    "frozen": "Sources captured", "analysis_queued": "Map queued", "analyzing": "Building map",
    "awaiting_review": "Review pending", "awaiting_labels": "Labels pending",
    "review_queued": "Review queued", "reviewing": "Review in progress",
    "render_queued": "Map render queued", "finishing": "Finishing map", "map_reviewed": "Map reviewed",
    "video_queued": "Video queued", "rendering_video": "Rendering video", "complete": "Complete",
    "analysis_failed": "Map analysis failed", "video_failed": "Video rendering failed",
    "review_failed": "Graph review failed", "render_failed": "Map rendering failed",
    "capture_failed": "Source capture failed", "capture_unsafe": "Source capture unavailable",
    "unsafe": "Source capture unavailable", "cancelled": "Cancelled", "interrupted": "Interrupted",
    "skipped": "Skipped", "state_unavailable": "Task state unavailable",
}
ACTIVE = frozenset(("capturing", "active", "freezing", "analysis_queued", "analyzing",
                    "awaiting_review", "awaiting_labels", "review_queued", "reviewing",
                    "render_queued", "finishing", "video_queued", "rendering_video"))
FAILED = frozenset(("analysis_failed", "review_failed", "render_failed", "video_failed",
                    "capture_failed", "capture_unsafe", "unsafe"))
FORMAT_NAMES = {
    "python": "Python", "javascript": "JavaScript", "typescript": "TypeScript", "java": "Java",
    "cpp": "C++", "c": "C", "csharp": "C#", "go": "Go", "rust": "Rust", "php": "PHP",
    "kotlin": "Kotlin", "scala": "Scala", "groovy": "Groovy", "swift": "Swift", "dart": "Dart",
    "ruby": "Ruby", "lua": "Lua", "elixir": "Elixir", "r": "R", "solidity": "Solidity",
    "shell": "Shell", "powershell": "PowerShell", "vue": "Vue", "svelte": "Svelte", "astro": "Astro",
    "dockerfile": "Docker", "compose": "Docker Compose", "kubernetes": "Kubernetes", "terraform": "Terraform",
    "bicep": "Bicep", "cloudformation": "CloudFormation", "helm-values": "Helm",
    "github-actions": "GitHub Actions", "gitlab-ci": "GitLab CI", "azure-pipelines": "Azure Pipelines",
    "circleci": "CircleCI", "jenkins": "Jenkins", "bitbucket": "Bitbucket Pipelines", "make": "Make",
    "cloudbuild": "Cloud Build", "buildspec": "Buildspec", "openapi": "OpenAPI", "graphql": "GraphQL",
    "proto": "Protocol Buffers", "sql": "SQL", "prisma": "Prisma", "notebook": "Jupyter Notebook",
    "json": "JSON", "yaml": "YAML", "toml": "TOML", "ini": "INI", "env": "Environment settings",
    "markdown": "Markdown", "text": "Other text", "deps-npm": "npm dependencies",
    "deps-maven": "Maven dependencies", "deps-gradle": "Gradle dependencies", "deps-pip": "Python dependencies",
}
KNOWN_FORMATS = (set(CODE_EXT.values()) | {item[0] for item in OTHER_EXT.values()} | set(CI_NAMES.values())
                 | {"deps-" + value for value in DEPS_FILES.values()} | set(FORMAT_NAMES))


def _is_reparse(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _safe(path: Path, base: Path, directory=False) -> bool:
    """Refuse symlinks/junctions at every managed path component."""
    try:
        relative = path.relative_to(base)
        current = base
        if _is_reparse(current):
            return False
        for part in relative.parts:
            current /= part
            if _is_reparse(current):
                return False
        path.resolve(strict=True).relative_to(base.resolve(strict=True))
        return path.is_dir() if directory else path.is_file()
    except (OSError, ValueError, RuntimeError):
        return False


def _read_states(base: Path) -> tuple[list, bool]:
    tasks = base / "tasks"
    if not tasks.exists():
        return [], False
    if not _safe(tasks, base, directory=True):
        return [], True
    try:
        def modified(path):
            try:
                return path.lstat().st_mtime
            except OSError:
                return 0
        entries = sorted(tasks.iterdir(), key=lambda p: (modified(p), p.name), reverse=True)
    except OSError:
        return [], True
    records, problem, remaining = [], False, MAX_HISTORY_READ_BYTES
    for task in entries:
        if not TASK_ID.fullmatch(task.name):
            continue
        if len(records) >= MAX_TASKS:
            problem = True
            break
        state = {"task_id": task.name, "status": "state_unavailable"}
        path = task / "state.json"
        try:
            if not _safe(task, base, directory=True) or not _safe(path, base):
                raise ValueError("unsafe state")
            if path.stat().st_size > min(MAX_STATE_BYTES, remaining):
                raise ValueError("oversized state")
            with path.open("rb") as source:
                data = source.read(min(MAX_STATE_BYTES, remaining) + 1)
            remaining -= len(data)
            if len(data) > MAX_STATE_BYTES or remaining < 0:
                raise ValueError("oversized state")
            value = json.loads(data.decode("utf-8-sig"))
            if not isinstance(value, dict) or value.get("task_id") != task.name:
                raise ValueError("invalid state identity")
            state = _public_state(value)
        except (OSError, ValueError, RecursionError):
            problem = True
        records.append(state)
    return records, problem


def _public_state(state):
    result = {field: state[field] for field in PUBLIC_FIELDS if field in state}
    if isinstance(result.get("reviewed_files"), list):
        result["reviewed_files"] = len(result["reviewed_files"])
    return result


def _records(states) -> tuple[list, bool]:
    if isinstance(states, dict):
        if "task_id" in states:
            states = [states]
        else:
            states = [dict(value, task_id=key) for key, value in states.items()
                      if isinstance(value, dict) and value.get("task_id", key) == key]
    if not isinstance(states, (list, tuple)):
        return [], True
    records, seen, problem = [], set(), len(states) > MAX_TASKS
    for state in states[:MAX_TASKS]:
        ident = state.get("task_id") if isinstance(state, dict) else None
        if not isinstance(ident, str) or not TASK_ID.fullmatch(ident) or ident in seen:
            problem = True
            continue
        seen.add(ident)
        records.append(_public_state(state))
    return records, problem


def _text(value, fallback="Not recorded") -> str:
    return value[:240] if isinstance(value, str) and value else fallback


def _timestamp(value, fallback="Not recorded") -> str:
    if type(value) in (int, float):
        try:
            if math.isfinite(value):
                return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        except (OverflowError, OSError, ValueError):
            pass
        return fallback
    return _text(value, fallback)


def _created(state):
    return state.get("created_at", state.get("created", state.get("started_at")))


def _artifact(base: Path, ident: str, filename: str) -> str | None:
    path = base / "tasks" / ident / "output" / filename
    try:
        if _safe(path, base) and path.stat().st_size > 0:
            return f"tasks/{ident}/output/{filename}"
    except OSError:
        pass
    return None


def _iso_timestamp(value):
    """The browser receives dates, never arbitrary private metadata strings."""
    try:
        if type(value) in (int, float) and math.isfinite(value):
            parsed = datetime.fromtimestamp(value, timezone.utc)
        elif isinstance(value, str) and len(value) <= 64:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None
        else:
            return None
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return None


def _count(value):
    return value if type(value) is int and 0 <= value <= 1_000_000_000 else None


def _description(value, root, redactor, limit=600, preserve_newlines=False):
    if not isinstance(value, str):
        return ""
    # Learn before truncation so an assignment cannot lose its secret boundary.
    value = value[:4000]
    redactor.add_text(value, config=True)
    value = redactor.text(value)
    value = value.replace(str(root), "[local project]").replace(root.as_posix(), "[local project]")
    value = re.sub(r"(?i)\b[a-z]:[\\/][^\s<>\"'`]+", "[local path]", value)
    value = re.sub(r"\\\\[^\s<>\"'`]+", "[local path]", value)
    value = re.sub(r"(?<![\w:/])/(?:Users|home|private|tmp|var|mnt|Volumes|workspace|workspaces|opt|srv|root|etc|usr|dev|run)(?:/[^\s<>\"'`]+)?",
                   "[local path]", value)
    value = "\n".join(line.rstrip() for line in value.splitlines()) if preserve_newlines else " ".join(value.split())
    return value[:limit].rstrip() + ("…" if len(value) > limit else "")


def _series(value, labels, allowed):
    if not isinstance(value, dict):
        return []
    result = [{"name": name, "label": labels.get(name, name.replace("-", " ").title()), "count": count}
              for name, value in value.items() if isinstance(name, str) and name in allowed
              and (count := _count(value)) is not None and count > 0]
    return sorted(result, key=lambda item: (-item["count"], item["label"]))


def _no_learning(reason="No captured analysis is available for this task yet."):
    return {"version": 1, "revision": "", "available": False, "reason": reason, "steps": [], "concepts": []}


def _clean_learning_text(value, redactor):
    # A credential can coincidentally be "changes" or "function". Redact
    # public prose and evidence without corrupting lesson IDs or enum values.
    prose = {"title", "body", "question", "answer", "term", "definition", "example", "summary",
             "text", "file", "from", "to", "reason"}
    if isinstance(value, dict):
        return {key: redactor.text(item) if key in prose and isinstance(item, str)
                else _clean_learning_text(item, redactor) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_learning_text(item, redactor) for item in value]
    return value


def _review_coverage(value, stats):
    review = value.get("review")
    listed = review.get("reviewed_files") if isinstance(review, dict) else None
    reviewed = None
    if isinstance(listed, list):
        reviewed = len({path for path in listed if isinstance(path, str) and path and len(path) <= 500
                        and not path.startswith(("/", "\\")) and ":" not in path and "\\" not in path
                        and not any(part in ("", ".", "..") for part in path.split("/"))
                        and not any(ord(char) < 32 for char in path)})
    total = _count(stats.get("files"))
    percent = round(reviewed / total * 100, 1) if total and reviewed is not None and reviewed <= total else None
    return {"reviewed": reviewed, "total": total, "percent": percent,
            "label": "Files listed in source review / parsed files"}


def _task_metadata(base, ident, budget, *, root=None, redactor=None, learning_budget=None):
    """Read only a bounded generated IR, never snapshots or arbitrary paths."""
    path = base / "tasks" / ident / "output" / "ir.json"
    try:
        if budget[0] <= 0 or not _safe(path, base):
            return {}
        size = path.stat().st_size
        if size > min(MAX_IR_BYTES, budget[0]):
            return {}
        with path.open("rb") as stream:
            raw = stream.read(min(MAX_IR_BYTES, budget[0]) + 1)
        budget[0] -= len(raw)
        if len(raw) > MAX_IR_BYTES or budget[0] < 0:
            return {}
        value = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(value, dict):
            return {}
        # Select immediately; the rest of the IR must never become page data.
        why = value.get("why")
        descriptions = [item.get("text", "")[:4000] for item in why[:3]
                        if isinstance(item, dict) and isinstance(item.get("text"), str)] if isinstance(why, list) else []
        stats = value.get("stats") if isinstance(value.get("stats"), dict) else {}
        scope = value.get("scope") if isinstance(value.get("scope"), dict) else {}
        result = {"description": " ".join(descriptions),
                "changedFiles": _count(scope.get("files")), "symbols": _count(stats.get("symbols")),
                "edges": _count(stats.get("edges")), "added": _count(scope.get("add")),
                "removed": _count(scope.get("del")),
                "languages": _series(stats.get("languages"), FORMAT_NAMES, KNOWN_FORMATS),
                "domains": _series(stats.get("domains"), DOMAIN_NAMES, set(DOMAIN_NAMES)),
                "reviewCoverage": _review_coverage(value, stats)}
        risks = stats.get("risks") if isinstance(stats.get("risks"), dict) else {}
        result["risks"] = {severity: _count(risks.get(severity)) for severity in ("high", "medium", "low")}
        if root is not None and redactor is not None and learning_budget is not None:
            if learning_budget[0] <= 0:
                result["learning"] = _no_learning("Learning details for older tasks are outside this page's size limit. Open their saved map for the full change.")
            else:
                from .learning import build_learning
                clean = lambda text, limit=600: _description(text, root, redactor, limit, preserve_newlines=True)
                lesson = build_learning(value, clean_text=clean)
                nodes = value.get("nodes") if isinstance(value.get("nodes"), dict) else {}
                views = value.get("views") if isinstance(value.get("views"), list) else []
                owner = {}
                for view in views[:1000]:
                    if not isinstance(view, dict) or not isinstance(view.get("id"), str) or not re.fullmatch(r"v\d+", view["id"]):
                        continue
                    for node_id in view.get("children", []) if isinstance(view.get("children"), list) else []:
                        if isinstance(node_id, str):
                            owner.setdefault(node_id, view["id"])
                links = {}
                for node_id, node in list(nodes.items())[:3000]:
                    if not isinstance(node_id, str) or not isinstance(node, dict):
                        continue
                    file = node.get("file")
                    if (not isinstance(file, str) or not file or len(node_id) > 1200
                            or file.startswith(("/", "\\")) or ":" in file or "\\" in node_id
                            or any(part in ("", ".", "..") for part in file.split("/"))
                            or any(ord(char) < 32 for char in node_id)
                            or not any(node_id == prefix + file or node_id.startswith(prefix + file + "#") for prefix in ("f:", "c:", "m:"))
                            or ":" in node_id[2:] or redactor.text(node_id) != node_id):
                        continue
                    cid = "change-" + hashlib.sha256(node_id.encode("utf-8", errors="replace")).hexdigest()[:12]
                    query = {"v": owner[node_id], "n": node_id} if node_id in owner else {"n": node_id}
                    links[cid] = f"tasks/{ident}/output/codemap.html#" + urlencode(query)
                for step in lesson.get("steps", []):
                    for change in step.get("changes", []):
                        if change.get("id") in links:
                            change["mapUrl"] = links[change["id"]]
                    for connection in step.get("connections", []):
                        for side in ("from", "to"):
                            if connection.get(side + "Id") in links:
                                connection[side + "MapUrl"] = links[connection[side + "Id"]]
                size = len(json.dumps(lesson, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
                if size <= min(40_000, learning_budget[0]):
                    result["learning"] = lesson
                    learning_budget[0] -= size
                else:
                    result["learning"] = _no_learning("Learning details exceed this page's size limit. Open the saved map for the full change.")
        return result
    except (OSError, ValueError, RecursionError):
        return {}


def _steps(status, video_disabled=False):
    stages = (("capture", "Capture"), ("analysis", "Map"), ("review", "Source review"),
              ("render", "Explanation"), ("video", "Video"))
    current = {
        "capturing": 0, "active": 0, "freezing": 0, "capture_failed": 0, "capture_unsafe": 0, "unsafe": 0,
        "frozen": 1, "analysis_queued": 1, "analyzing": 1, "analysis_failed": 1,
        "awaiting_review": 2, "review_queued": 2, "reviewing": 2, "review_failed": 2,
        "awaiting_labels": 3, "render_queued": 3, "finishing": 3, "render_failed": 3,
        "map_reviewed": 4, "video_queued": 4, "rendering_video": 4, "video_failed": 4, "complete": 5,
    }.get(status, -1)
    if video_disabled:
        stages = stages[:-1]
    return [{"key": key, "label": label,
             "state": "done" if index < current else (
                 "failed" if index == current and status in FAILED else (
                     "current" if index == current else "pending"))}
            for index, (key, label) in enumerate(stages)]


def _progress(steps, status):
    available = status in STATUSES and status not in ("skipped", "cancelled", "interrupted", "state_unavailable")
    total = len(steps) if available else 0
    done = sum(step["state"] == "done" for step in steps) if available else 0
    remaining = [step["label"] for step in steps if step["state"] != "done"] if available else []
    return {"done": done, "total": total, "percent": round(done / total * 100) if total else None,
            "current": next((step["label"] for step in steps if step["state"] in ("current", "failed")), None),
            "remaining": remaining, "label": "Generation stages", "available": available}


def _dashboard_data(root, base, states, problem=False):
    redactor = SecretRedactor()
    redactor.add_text(root.name, config=True)
    budget, learning_budget, entries = [MAX_HISTORY_IR_BYTES], [MAX_HISTORY_LEARNING_BYTES], []
    for state in sorted(states, key=lambda s: (_timestamp(_created(s), ""), s["task_id"]), reverse=True):
        ident = state["task_id"]
        status = state.get("status")
        status = status if isinstance(status, str) and status in STATUSES else "state_unavailable"
        map_url = _artifact(base, ident, "codemap.html") if state.get("map_ready") is True else None
        ready = status == "complete" and state.get("video_status") == "ready"
        mp4 = _artifact(base, ident, "codemap.mp4") if ready else None
        webm = _artifact(base, ident, "codemap.webm") if ready else None
        warnings = []
        missing_map = state.get("map_ready") is True and not map_url
        missing_video = ready and not mp4
        if missing_map:
            warnings.append("The saved map is marked ready, but its file is unavailable.")
        if missing_video:
            warnings.append("The finished MP4 file is unavailable.")
        if state.get("attribution") == "ambiguous":
            warnings.append("Concurrent edits may be included. This captured window does not establish authorship.")
        omitted = _count(state.get("capture_omitted_count")) or 0
        if omitted:
            warnings.append(f"{omitted} unchanged binary or oversized file(s) were not included in this map.")
        if status in FAILED:
            warnings.append("This stage did not finish. Captured sources and any completed map are retained; recover through your agent.")
        elif status == "state_unavailable":
            warnings.append("Saved task metadata could not be read safely. No completion is assumed.")
        no_outputs = status == "complete" and not map_url and not mp4 and not webm
        if no_outputs:
            warnings.append("The task reports completion, but no output files are available.")
        attention = status in FAILED or status in ("state_unavailable", "interrupted", "cancelled") or missing_map or missing_video or no_outputs
        label = STATUSES[status]
        if status in ("complete", "map_reviewed"):
            if missing_map and missing_video or no_outputs:
                label = "Output files unavailable"
            elif missing_map:
                label = "Map file unavailable"
            elif missing_video:
                label = "MP4 file unavailable"
        category = "attention" if attention else ("skipped" if status == "skipped" else (
            "complete" if status == "complete" else "active"))
        metadata = _task_metadata(base, ident, budget, root=root, redactor=redactor, learning_budget=learning_budget)
        summary = _description(metadata.pop("description", ""), root, redactor)
        title = summary.split(". ", 1)[0].rstrip(".") if summary else f"Task {ident[:8]}"
        if len(title) > 100:
            title = title[:97].rstrip() + "…"
        if not summary:
            summary = {
                "skipped": "No supported source changes were detected. This task does not need a map or video.",
                "cancelled": "Generation was cancelled. Any completed map remains available.",
                "interrupted": "Capture was interrupted. Start a new coding task to capture a complete change.",
                "state_unavailable": "Task details could not be read safely. Completion has not been assumed.",
                "capturing": "Recording the task's sources. A map appears after capture and analysis finish.",
                "active": "The coding task is in progress. Its walkthrough follows source capture and review.",
                "freezing": "Saving the end sources for this task before map generation.",
                "frozen": "Sources are captured. The initial map is next.",
                "analysis_queued": "Sources are captured. The initial map is queued.",
                "analyzing": "Sources are captured. The initial map is being prepared.",
            }.get(status, "Task details and available outputs are collected here.")
        reviewed = state.get("reviewed_files")
        reviewed = len(reviewed) if isinstance(reviewed, list) else _count(reviewed)
        steps = _steps(status, state.get("video_status") == "disabled")
        entries.append({"id": ident, "status": status, "statusLabel": label, "category": category,
                        "createdAt": _iso_timestamp(_created(state)), "updatedAt": _iso_timestamp(state.get("updated_at")),
                        "title": title, "summary": summary, "reviewedFiles": reviewed,
                        "changedFiles": None, "symbols": None, "edges": None, "added": None, "removed": None,
                        "languages": [], "domains": [], "risks": {"high": None, "medium": None, "low": None},
                        "reviewCoverage": {"reviewed": None, "total": None, "percent": None,
                                           "label": "Files listed in source review / parsed files"},
                        "learning": _no_learning(),
                        **metadata, "attribution": "ambiguous" if state.get("attribution") == "ambiguous" else "window",
                        "omittedCount": omitted, "mapUrl": map_url, "videoUrl": mp4 or webm,
                        "downloadUrl": mp4, "webmUrl": webm, "warnings": warnings,
                        "steps": steps, "progress": _progress(steps, status)})
    # Learn credentials across summaries before the final pass, since one entry
    # may contain a literal already discovered in another entry's assignment.
    for entry in entries:
        entry["title"] = redactor.text(entry["title"])
        entry["summary"] = redactor.text(entry["summary"])
        lesson = _clean_learning_text(entry["learning"], redactor)
        if lesson.get("available"):
            lesson.pop("revision", None)
            lesson["revision"] = hashlib.sha256(json.dumps(lesson, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()
        entry["learning"] = lesson
    known = [entry["progress"] for entry in entries if entry["progress"]["available"]]
    total, done = sum(item["total"] for item in known), sum(item["done"] for item in known)
    return {"version": 1, "appVersion": VERSION, "project": redactor.text(root.name),
            "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "problem": bool(problem), "tasks": entries,
            "projectProgress": {"done": done, "total": total, "percent": round(done / total * 100) if total else None,
                                "remainingTasks": sum(entry["category"] == "active" for entry in entries),
                                "label": "Generation stages across saved tasks", "available": bool(total)},
            "supportedLanguages": [{"name": name, "label": FORMAT_NAMES.get(name, name.title())} for name in sorted(set(CODE_EXT.values()))],
            "supportedDomains": [{"name": name, "label": label} for name, label in DOMAIN_NAMES.items()],
            "parserNotes": "Python uses AST parsing. JavaScript/TypeScript use syntax heuristics; other languages use pattern-based parsing. Dynamic behavior and some relationships can remain unresolved."}


def _dashboard_markup(root, base, states, problem):
    try:
        javascript = (DASHBOARD_ASSETS / "history.bundle.js").read_text(encoding="utf-8")
        stylesheet = (DASHBOARD_ASSETS / "history.css").read_text(encoding="utf-8")
    except OSError:
        javascript, stylesheet = "", ""
    payload = json.dumps(_dashboard_data(root, base, states, problem), ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    # JSON is inert script content. '<' must be escaped even in application/json
    # because the HTML parser would otherwise accept an injected closing tag.
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    head = "<style>" + stylesheet.replace("</style", "<\\/style") + "</style>"
    body = ('<script id="codemap-history-data" type="application/json">' + payload
            + '</script><script>window.__CODEMAP_HISTORY__=JSON.parse(document.getElementById("codemap-history-data").textContent);</script>'
            + "<script>" + javascript.replace("</script", "<\\/script") + "</script>")
    return head, body


def _render(root: Path, base: Path, states: list, problem: bool) -> str:
    dashboard_head, dashboard_body = _dashboard_markup(root, base, states, problem)
    # Learn only bounded public metadata. Prompts, source roots, raw errors,
    # worker arguments and arbitrary state values never enter the page.
    redactor = SecretRedactor()
    public = [root.name]
    for state in states:
        public.extend(_timestamp(state.get(field), "") for field in ("created_at", "created", "started_at", "updated_at"))
    for value in public:
        redactor.add_text(value, config=True)
    def clean(value):
        return html.escape(redactor.text(value), quote=True)

    cards, active = [], False
    states = sorted(states, key=lambda s: (_timestamp(_created(s), ""), s["task_id"]), reverse=True)
    for state in states:
        ident = state["task_id"]
        status = state.get("status") if isinstance(state.get("status"), str) else ""
        label = STATUSES.get(status, "Task status unavailable")
        active |= status in ACTIVE
        map_href = _artifact(base, ident, "codemap.html") if state.get("map_ready") is True else None
        ready = status == "complete" and state.get("video_status") == "ready"
        mp4 = _artifact(base, ident, "codemap.mp4") if ready else None
        webm = _artifact(base, ident, "codemap.webm") if ready else None
        missing_map = state.get("map_ready") is True and map_href is None
        missing_mp4 = ready and mp4 is None
        if status in ("complete", "map_reviewed"):
            if missing_map and missing_mp4:
                label = "Output files unavailable"
            elif missing_map:
                label = "Map file unavailable"
            elif missing_mp4:
                label = "MP4 file unavailable"
            elif status == "complete" and not map_href and not mp4 and not webm:
                label = "Output files unavailable"
        tone = "error" if status in FAILED or status == "state_unavailable" else "normal"
        created = clean(_timestamp(_created(state)))
        updated = clean(_timestamp(state.get("updated_at")))
        body = [f'<article class="card {tone}" aria-labelledby="task-{ident}">',
                f'<div class="card-head"><h2 id="task-{ident}">Task {ident[:8]}</h2><span class="status">{label}</span></div>',
                f'<dl><div><dt>Started</dt><dd><time>{created}</time></dd></div>',
                f'<div><dt>Updated</dt><dd><time>{updated}</time></dd></div></dl>']
        reviewed = state.get("reviewed_files")
        if isinstance(reviewed, list):
            reviewed = len(reviewed)
        if type(reviewed) is int and 0 <= reviewed <= 100_000:
            body.append(f'<p class="detail">Source review recorded for {reviewed} file(s).</p>')
        omitted = state.get("capture_omitted_count")
        if type(omitted) is int and 0 < omitted <= 1_000_000:
            body.append(f'<p class="warning" role="note">{omitted} unchanged binary or oversized file(s) were not included in this map.</p>')
        if state.get("attribution") == "ambiguous":
            body.append('<p class="warning" role="note">Concurrent edits may be included. This map describes a captured time window; authorship is not established.</p>')
        if status in FAILED:
            body.append('<p class="detail">This stage did not finish. A saved map may still be available; retry through your agent.</p>')
        elif status == "state_unavailable" or status not in STATUSES:
            body.append('<p class="detail">Saved task metadata could not be read safely. No completion is assumed.</p>')
        links = []
        if map_href:
            links.append(f'<a class="button primary" href="{map_href}">Open map</a>')
        if mp4 or webm:
            links.append(f'<a class="button" href="{mp4 or webm}">Play video</a>')
        if mp4:
            links.append(f'<a class="button" href="{mp4}" download="codemap.mp4">Download MP4</a>')
        if links:
            body.append('<nav class="actions" aria-label="Task outputs">' + "".join(links) + '</nav>')
        elif status == "complete":
            body.append('<p class="detail">The task reports completion, but no publishable output files are available.</p>')
        if status == "complete" and state.get("video_status") == "disabled":
            body.append('<p class="detail">Video was disabled for this task.</p>')
        if missing_map:
            body.append('<p class="warning" role="note">The saved map is marked ready, but its file is unavailable.</p>')
        if missing_mp4:
            body.append('<p class="warning" role="note">The finished MP4 file is unavailable.</p>')
        body.append('</article>')
        cards.append("\n".join(body))
    empty = '<p class="empty">No task history yet. Maps will appear here after your agent captures a task.</p>' if not cards else ""
    notice = '<p class="warning" role="note">Some task records are unavailable or the history limit was reached. No completion is assumed for those records.</p>' if problem else ""
    # Browsers can keep a meta-refresh timer even after React removes the tag.
    # Only the no-JavaScript fallback should ever schedule whole-page reloads.
    refresh = '<noscript><meta http-equiv="refresh" content="5"></noscript>' if active else ""
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">""" + refresh + """<title>Codemap task history</title>
<style id="codemap-fallback-styles">
:root{color-scheme:light dark;--bg:#f3f6fa;--card:#fff;--ink:#162238;--muted:#4d5c70;--line:#cad4e0;--accent:#174fa3;--note:#fff2cc;--note-ink:#604200;--error:#a0202b}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#111823;--card:#192333;--ink:#edf2fa;--muted:#b3bfd0;--line:#41516a;--accent:#a8caff;--note:#3b3017;--note-ink:#ffe09a;--error:#ffb2b8}}
:root[data-theme=dark]{color-scheme:dark;--bg:#111823;--card:#192333;--ink:#edf2fa;--muted:#b3bfd0;--line:#41516a;--accent:#a8caff;--note:#3b3017;--note-ink:#ffe09a;--error:#ffb2b8}
:root[data-theme=light]{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,sans-serif}main{max-width:1000px;margin:auto;padding:32px 20px}header{margin-bottom:28px}.top,.card-head,.actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.top{justify-content:space-between}h1{font-size:28px;margin:0}h2{font-size:19px;margin:0}.intro,.detail,dt{color:var(--muted)}.intro{max-width:75ch}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px;margin:16px 0}.card-head{justify-content:space-between}.status{font-size:14px;font-weight:650;border:1px solid var(--line);padding:3px 10px;border-radius:999px}.error .status{color:var(--error)}dl{display:flex;gap:28px;flex-wrap:wrap;margin:16px 0}dt{font-size:13px}dd{margin:0;overflow-wrap:anywhere}.button,button{display:inline-block;background:var(--card);color:var(--accent);border:1px solid var(--line);border-radius:8px;padding:9px 13px;font:inherit;font-weight:600;text-decoration:none;cursor:pointer}.primary{border-color:var(--accent)}a:hover,button:hover{text-decoration:underline}:focus-visible{outline:3px solid var(--accent);outline-offset:3px}.warning{background:var(--note);color:var(--note-ink);padding:12px;border-radius:8px}.empty{padding:28px;border:1px dashed var(--line);border-radius:12px}.detail{font-size:14px}footer{font-size:13px;color:var(--muted);margin-top:28px}@media(max-width:480px){main{padding:20px 14px}.card{padding:17px}h1{font-size:24px}}
</style>""" + dashboard_head + """</head><body><div id="codemap-dashboard"></div><div class="history-fallback"><main><header><div class="top"><h1>Codemap task history</h1><div class="actions"><button id="refresh" type="button">Refresh</button><button id="theme" type="button" aria-label="Toggle light and dark theme">Change theme</button></div></div>
<p class="intro">Maps and videos use sources captured at task time. Changes describe that time window; they do not prove who authored each edit.</p>
<p class="detail">Project: """ + clean(root.name) + "</p></header>" + notice + empty + "".join(cards) + """<footer>Local files only. Raw source snapshots and private task metadata are not linked here.</footer></main></div>""" + dashboard_body + """
<script>document.getElementById('refresh').addEventListener('click',function(){location.reload()});document.getElementById('theme').addEventListener('click',function(){var d=document.documentElement;var dark=d.dataset.theme?d.dataset.theme==='dark':matchMedia('(prefers-color-scheme: dark)').matches;d.dataset.theme=dark?'light':'dark'})</script></body></html>
"""


def write_history(root, states=None) -> Path:
    """Atomically publish .codemap/index.html from bounded, public task metadata."""
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("history root must be a directory")
    base = root / ".codemap"
    if base.exists() or base.is_symlink():
        if not _safe(base, root, directory=True):
            raise ValueError("unsafe codemap output directory")
    else:
        base.mkdir()
    records, problem = _read_states(base) if states is None else _records(states)
    # Disk records undergo the same identity validation as caller-provided data.
    records, invalid = _records(records)
    content = _render(root, base, records, problem or invalid)
    output, temporary = base / "index.html", None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=base,
                                         prefix=".history-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output
