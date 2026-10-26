"""Outputs: Markdown with Mermaid diagrams, and the single-file HTML explorer/player."""
from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path

from .diffmap import CHANGED, changed_symbols, own_status
from .util import read_text, write_text

ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"
KIND = {"root": "system", "group": "group", "class": "class", "module": "module",
        "function": "function", "method": "method", "more": "folded"}
WORD = {"added": "added", "modified": "changed", "deleted": "removed", "context": "unchanged", "same": ""}
STYLE = {
    "added": "fill:#dcfce7,stroke:#16a34a,color:#14532d",
    "modified": "fill:#fef3c7,stroke:#d97706,color:#78350f",
    "deleted": "fill:#fee2e2,stroke:#dc2626,color:#7f1d1d,stroke-dasharray:5 4",
    "context": "fill:#f8fafc,stroke:#94a3b8,color:#475569,stroke-dasharray:4 4",
    "same": "fill:#eef2ff,stroke:#6366f1,color:#1e1b4b",
    "ghost": "fill:#ffffff,stroke:#cbd5e1,color:#94a3b8,stroke-dasharray:2 4",
    "more": "fill:#ffffff,stroke:#cbd5e1,color:#64748b,stroke-dasharray:2 2",
}
MAX_MD_VIEWS = 40


def output_navigation(ir, out_dir):
    """Bind return links to the actual output layout, never an arbitrary URL."""
    out = Path(out_dir).resolve()
    try:
        ident = out.parent.name
        if (out.name == "output" and out.parent.parent.name == "tasks"
                and out.parent.parent.parent.name == ".codemap" and re.fullmatch(r"[0-9a-f]{32}", ident)
                and ir.get("analysis", {}).get("isolated") is True
                and Path(ir.get("root", "")).resolve() == out.parent / "work"):
            return {"dashboard": "../../../index.html", "task_id": ident}
        if (out / "index.html").is_file() and not (out / "index.html").is_symlink():
            return {"dashboard": "index.html"}
    except (OSError, ValueError, TypeError):
        pass
    return {}


def portable_artifacts(ir: dict) -> dict:
    """Keep output handoffs relative; never publish an absolute checkout path."""
    artifacts = ir.get("artifacts") or {}
    out = {}
    if artifacts.get("diff") == "codemap.diff":
        out["diff"] = "codemap.diff"
    if artifacts.get("source_kind") == "snapshot":
        out["source_kind"] = "snapshot"
    if artifacts.get("dashboard") == "index.html":
        out["dashboard"] = "index.html"
    elif (artifacts.get("dashboard") == "../../../index.html"
          and isinstance(artifacts.get("task_id"), str) and re.fullmatch(r"[0-9a-f]{32}", artifacts["task_id"])):
        out.update(dashboard="../../../index.html", task_id=artifacts["task_id"])
    for key, name in (("review_request", "review.md"), ("review_report", "review-report.json")):
        if artifacts.get(key) == name:
            out[key] = name
    source_base = artifacts.get("source_base")
    if (isinstance(source_base, str) and source_base
            and not source_base.startswith(("/", "\\"))
            and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", source_base)
            and "\\" not in source_base and not any(ord(c) < 32 for c in source_base)):
        out["source_base"] = source_base
    return out


def _mm(text: str) -> str:
    return (text.replace("&", "&amp;").replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")
            .replace("[", "#91;").replace("]", "#93;"))


def _title(n: dict) -> str:
    return n.get("title") or n["name"]


def _counts(n: dict) -> str:
    parts = []
    if n.get("add"):
        parts.append(f"+{n['add']}")
    if n.get("del"):
        parts.append(f"-{n['del']}")
    return " ".join(parts)


def mermaid(ir: dict, view: dict) -> str:
    nodes = ir["nodes"]
    ids: dict = {}
    lines = ["flowchart LR"]
    for i, nid in enumerate(view["order"] + view.get("ghosts", [])):
        n = nodes[nid]
        ids[nid] = f"n{i}"
        ghost = nid in view.get("ghosts", [])
        sub = n["name"] if n.get("title") else (n.get("sub") or KIND.get(n["kind"], n["kind"]))
        extra = " · ".join(x for x in (WORD.get(n["status"], "") if not ghost else "outside", _counts(n),
                                       f"⚠ {n['risk']}" if n.get("risk") and not ghost else "") if x)
        label = _mm(_title(n)) + "<br/>" + _mm(sub) + (f"<br/>{_mm(extra)}" if extra else "")
        cls = "ghost" if ghost else ("more" if n["kind"] == "more" else n["status"])
        shape = ('(["', '"])') if n["kind"] == "more" else ('["', '"]')
        lines.append(f"  n{i}{shape[0]}{label}{shape[1]}:::{cls}")
    for a, b, _w, conf, kind in view["edges"]:
        if a in ids and b in ids:
            reviewed = any(e[:2] == [a, b] for e in view.get("review_edges", []))
            arrow = "-.->" if (conf < 0.6 or kind == "inherits" or reviewed) else "-->"
            label = {"inherits": "|extends|", "refs": "|uses|"}.get(kind, "")
            if reviewed:
                label = "|includes model inference|"
            lines.append(f"  {ids[a]} {arrow}{label} {ids[b]}")
    used = {("ghost" if nid in view.get("ghosts", []) else ("more" if nodes[nid]["kind"] == "more" else nodes[nid]["status"]))
            for nid in ids}
    for cls in sorted(used):
        lines.append(f"  classDef {cls} {STYLE.get(cls, STYLE['same'])}")
    return "\n".join(lines)


def _dfs_views(ir: dict) -> list:
    by_node = {v["node"]: v for v in ir["views"]}
    out: list = []

    def walk(v):
        out.append(v)
        for c in v["order"]:
            if c in by_node and len(out) < MAX_MD_VIEWS * 4:
                walk(by_node[c])
    if ir["views"]:
        walk(ir["views"][0])
    if ir["scope"]["mode"] != "diff" and len(out) > MAX_MD_VIEWS:
        out = out[:MAX_MD_VIEWS]
    return out


def scope_line(ir: dict) -> str:
    sc = ir["scope"]
    if sc["mode"] == "diff":
        return f"Changes: {sc['label']} · {sc['files']} files · +{sc['add']} −{sc['del']}"
    st = ir["stats"]
    return f"Whole repository · {st['files']} files · {st['symbols']} classes & functions"


def render_markdown(ir: dict) -> str:
    nodes = ir["nodes"]
    out = [f"# codemap: {ir['repo']}", "", f"> {scope_line(ir)}  ",
           f"> Generated {ir['generated']} by codemap {ir['version']} · tier `{ir['tier']}`. "
           "Each diagram shows at most 9 boxes; arrows mean *calls / uses*.", ""]
    if portable_artifacts(ir).get("diff"):
        out += ["[Open the full unified diff](codemap.diff) (recognized credentials redacted). "
                "For review only; not an apply-ready patch. "
                "Keep `codemap.diff` beside this map when sharing or moving it.", ""]
    coverage, review = ir.get("stats", {}).get("coverage", {}), ir.get("review", {})
    out += ["## Analysis coverage", "",
            f"{coverage.get('without_symbols_count', 0)} mapped files without extracted declarations; "
            f"{coverage.get('omitted_count', 0)} candidate files omitted. "
            "A file without declarations can be ordinary setup/configuration or an extraction gap.", ""]
    if review.get("status") == "pending":
        out += ["Structural model review is **pending**. Read [review.md](review.md), inspect source, "
                "write review.json and run `codemap review` before labeling.", ""]
    elif review.get("status") == "reviewed":
        out += [f"Model reports reviewing {len(review.get('reviewed_files', []))} files. "
                f"Applied {len(review.get('accepted_symbols', []))} symbol additions and "
                f"{len(review.get('accepted_edges', []))} edge operations; "
                f"{len(review.get('uncertain', []))} uncertain and {len(review.get('rejected', []))} rejected proposals. "
                "Evidence positions and text were checked; model inferences are not semantic proofs. "
                "New model links are dashed. [Review report](review-report.json).", ""]
    else:
        out += ["Parser results only; no structural model review was requested.", ""]
    if ir.get("why"):
        out += ["## Why", "", ir["why"][0]["text"], ""]
    if ir["scope"]["mode"] == "diff":
        groups = {s: [] for s in CHANGED}
        for n in changed_symbols(nodes):
            groups[own_status(n)].append(n)
        out += ["## What changed", "",
                "| | count | where |", "|---|---:|---|"]
        for status in CHANGED:
            items = sorted(groups[status], key=lambda n: (n["file"], n["line"]))
            names = ", ".join(f"`{n['qual']}`" for n in items[:12]) + (f" +{len(items) - 12} more" if len(items) > 12 else "")
            out.append(f"| **{WORD[status]}** | {len(items)} | {names or '-'} |")
        if ir.get("other_files"):
            others = ", ".join(f"`{o['path']}` ({o.get('note') or o['status']})" for o in ir["other_files"][:10])
            out += ["", f"Also changed (not mapped): {others}"]
        out.append("")
    risks = ir.get("risks", [])
    if risks:
        sev_icon = {"high": "🔴 high", "medium": "🟠 medium", "low": "🟡 low"}
        out += ["## Risk flags", "",
                "Deterministic checks on the changed lines. Hints for a reviewer, not verdicts.", "",
                "| severity | where | what | note |", "|---|---|---|---|"]
        for f in risks[:25]:
            note = (f.get("note") or "").replace("|", "\\|")
            out.append(f"| {sev_icon[f['sev']]} | `{f['file']}:{f['line']}` | {f['msg']} | {note} |")
        if len(risks) > 25:
            out.append(f"| | | +{len(risks) - 25} more in codemap.html | |")
        out.append("")
    out += ["## Map (read top-down)", ""]
    for v in _dfs_views(ir):
        node = nodes[v["node"]]
        name = ir["repo"] if node["kind"] == "root" else _title(node)
        heading = f"### Level {v['level']} · {name}" + (f" <sub>{node['name']}</sub>" if node.get("title") else "")
        body = [heading, ""]
        caption = v.get("caption") or node.get("sum")
        if caption:
            body += [f"*{caption}*", ""]
        body += ["```mermaid", mermaid(ir, v), "```", ""]
        for i, cid in enumerate(v["order"], 1):
            c = nodes[cid]
            tag = f" — *{WORD[c['status']]}" + (f", {_counts(c)}" if _counts(c) else "") + "*" if WORD.get(c["status"]) else ""
            ident = f" (`{c['name']}`)" if c.get("title") else ""
            kind = f" <sub>{c['sub']}</sub>" if c.get("sub") and c.get("domain", "code") != "code" else ""
            risk = f" ⚠ {c['risk']}" if c.get("risk") else ""
            body.append(f"{i}. **{_title(c)}**{ident}{kind}{tag}{risk} — {c.get('sum', '')}")
        if v.get("ghosts"):
            body.append("")
            body.append("Connected outside this view: " + ", ".join(_title(nodes[g]) for g in v["ghosts"]))
        body.append("")
        if v["level"] >= 2:
            out += [f"<details><summary>Level {v['level']} · {name}</summary>", ""] + body[1:] + ["</details>", ""]
        else:
            out += body
    out += ["---", "Open `codemap.html` in a browser for the clickable map and the narrated walkthrough."]
    return "\n".join(out) + "\n"


def html_payload(ir: dict) -> dict:
    keep = ("repo", "scope", "stats", "tier", "generated", "version", "views", "beats", "why", "other_files", "risks")
    data = {k: ir.get(k) for k in keep}
    data["artifacts"] = portable_artifacts(ir)
    from .learning import build_learning
    lesson = build_learning(ir, clean_text=lambda value, limit=600: value[:limit])
    data["learning_items"] = [item["id"] for step in lesson.get("steps", []) for item in step.get("changes", [])]
    review = ir.get("review") or {}
    data["review"] = {"status": review.get("status", "not_requested"),
                      "reviewed_file_count": len(review.get("reviewed_files", [])),
                      "accepted_symbol_count": len(review.get("accepted_symbols", [])),
                      "accepted_edge_count": len(review.get("accepted_edges", [])),
                      "uncertain_count": len(review.get("uncertain", [])),
                      "rejected_count": len(review.get("rejected", [])),
                      "uncertain": review.get("uncertain", [])[:40], "rejected": review.get("rejected", [])[:20]}
    nodes = {}
    for nid, n in ir["nodes"].items():
        m = {k: v for k, v in n.items() if (k != "hash" and v not in (None, "", [], 0)) or k in ("name", "kind", "status")}
        m["rank"] = round(n.get("rank", 0.0), 5)
        m["lsrc"] = n.get("lsrc")
        m["learning_id"] = "change-" + hashlib.sha256(nid.encode("utf-8", errors="replace")).hexdigest()[:12]
        nodes[nid] = m
    data["nodes"] = nodes
    return data


def render_html(ir: dict, audio: dict | None = None) -> str:
    template = read_text(ASSETS / "viewer.html").replace("/*__CODEMAP_STUDIO__*/", read_text(ASSETS / "studio.css"))
    from . import VERSION
    template = template.replace("__CODEMAP_APP_VERSION__", VERSION)
    data = html_payload(ir)
    if audio:
        data["audio"] = audio
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/").replace("<!--", "<\\!--")
    title = f"codemap · {ir['repo']}"
    return template.replace("__CODEMAP_TITLE__", title.replace("<", "&lt;")).replace('"__CODEMAP_DATA__"', payload)


def write_outputs(ir: dict, out_dir: Path, audio: dict | None = None) -> tuple:
    ir = dict(ir, artifacts=dict(ir.get("artifacts") or {}))
    ir["artifacts"].pop("dashboard", None)
    ir["artifacts"].pop("task_id", None)
    ir["artifacts"].update(output_navigation(ir, out_dir))
    md, html = out_dir / "codemap.md", out_dir / "codemap.html"
    write_text(md, render_markdown(ir))
    write_text(html, render_html(ir, audio))
    return md, html
