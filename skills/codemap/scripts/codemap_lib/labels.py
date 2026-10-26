"""Labels: free deterministic summaries, the token-light worksheet, and merging the agent's answers."""
from __future__ import annotations

from .diffmap import CHANGED
from .util import est_tokens, human_list, sha1, words

CACHE_VERSION = "2"
DEFAULT_BUDGET = {"lite": 3000, "full": 8000, "video": 9000}


def short_name(qual: str) -> str:
    return qual.rsplit(".", 1)[-1] + "()"


def node_hash(n: dict) -> str:
    if n.get("hash"):
        return n["hash"]
    basis = f"{n['kind']}|{n['id']}|{','.join(sorted(n.get('children', [])))}"
    return sha1(basis)


def _sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?…" else text + "."


def auto_summary(n: dict, nodes: dict) -> tuple:
    """Free summary from docstrings and structure. Returns (summary, source)."""
    kind = n["kind"]
    code = n.get("domain", "code") == "code"
    if n.get("doc") and kind != "root":
        return _sentence(n["doc"]), "doc"
    if kind in ("function", "method"):
        parts = []
        if n.get("routes"):
            parts.append(f"Serves {human_list(n['routes'], 2)}.")
        elif n.get("entry", 0) >= 3:
            parts.append("Entry point.")
        name = short_name if code else (lambda q: q.rsplit(".", 1)[-1])
        calls = [name(q) for _, q in n.get("calls", [])[:3]]
        callers = [name(q) for _, q in n.get("callers", [])[:2]]
        if calls:
            parts.append(f"{'Calls' if code else 'Uses'} {human_list(calls)}.")
        if callers:
            parts.append(f"Used by {human_list(callers)}.")
        if not parts and not code:
            return (f"{(n.get('sub') or 'item').capitalize()} in {n.get('file', '').rsplit('/', 1)[-1]}."), "auto"
        return (" ".join(parts) or "Self-contained helper."), "auto"
    if kind == "class":
        names = [nodes[c]["name"] for c in n["children"]] + [h[1] for h in n.get("hidden", [])]
        label = n.get("sub") or "class"
        member = {"API endpoints": "operations", "API schemas": "schemas", "table": "columns", "message": "fields",
                  "service": "calls", "enum": "values"}.get(label, "methods" if code else "entries")
        text = f"{label.capitalize()} with {len(names) or n.get('methods', 0)} {member}"
        if n.get("bases"):
            text += f", extends {', '.join(n['bases'][:2])}"
        return text + (f": {human_list(names, 4)}." if names else "."), "auto"
    if kind == "module":
        names = [nodes[c]["name"] for c in n["children"]] + [h[1] for h in n.get("hidden", [])]
        if n.get("opaque"):
            return ("No declarations extracted; inspect the source" +
                    (" and change excerpt." if n.get("diff") else ".")), "auto"
        if names:
            return (f"Functions: {human_list(names, 4)}." if code else
                    f"{(n.get('sub') or 'file').capitalize()}: {human_list(names, 4)}."), "auto"
        return "Module-level code (imports, constants, setup).", "auto"
    if kind == "more":
        names = [nodes[m]["name"] for m in n.get("members", []) if m in nodes] + [h[1] for h in n.get("hidden", [])]
        return f"Folded to keep this view small: {human_list(names, 6)}.", "auto"
    if kind in ("group", "root"):
        kids = sorted(n["children"], key=lambda c: -nodes[c]["rank"])
        names = [nodes[c]["name"] for c in kids if nodes[c]["kind"] != "more"]
        files = n.get("files", 0)
        lead = f"{files} files" if files else "Parts"
        return f"{lead}: {human_list(names, 3)}.", "auto"
    return "", "auto"


def apply_auto(ir: dict) -> None:
    nodes = ir["nodes"]
    for n in nodes.values():
        if n.get("lsrc") in ("agent", "cache"):
            continue
        n["sum"], n["lsrc"] = auto_summary(n, nodes)
        n["title"] = None


def apply_cache(ir: dict, cache: dict) -> int:
    hits = 0
    for n in ir["nodes"].values():
        if n["kind"] in ("more", "root"):
            continue
        entry = cache.get(f"{CACHE_VERSION}:{node_hash(n)}")
        if entry:
            n["title"], n["sum"], n["lsrc"] = entry[0], entry[1], "cache"
            hits += 1
    return hits


def update_cache(ir: dict, cache: dict) -> None:
    for n in ir["nodes"].values():
        if n.get("lsrc") == "agent" and n["kind"] not in ("more", "root"):
            cache[f"{CACHE_VERSION}:{node_hash(n)}"] = [n.get("title"), n.get("sum")]


# ---------------------------------------------------------------- short ids

def assign_short_ids(ir: dict) -> dict:
    nodes = ir["nodes"]
    short: dict = {}
    seen: set = set()
    counters = {"g": 0, "u": 0, "f": 0}
    for v in ir["views"]:
        for nid in [v["node"]] + v["children"]:
            if nid in seen or nid not in nodes:
                continue
            seen.add(nid)
            kind = nodes[nid]["kind"]
            if kind in ("more", "root"):
                continue
            prefix = "g" if kind == "group" else ("u" if kind in ("class", "module") else "f")
            counters[prefix] += 1
            short[f"{prefix}{counters[prefix]}"] = nid
    for i, b in enumerate(ir.get("plan", []), 1):
        b["sid"] = f"b{i}"
    return short


# ---------------------------------------------------------------- worksheet

def _weak(n: dict) -> bool:
    """Would a newcomer struggle with the free label?"""
    if n.get("opaque") or n.get("flags"):
        return True
    if n["kind"] in ("group", "class", "module"):
        return n.get("lsrc") != "doc" or n["kind"] == "group"
    return not n.get("doc") and len(words(n["name"].rstrip("()"))) < 3


def _status(n: dict) -> str:
    if n["status"] not in CHANGED:
        return ""
    counts = f" +{n['add']}" if n["add"] else ""
    counts += f" -{n['del']}" if n["del"] else ""
    return f" | {n['status'].upper()}{counts}"


def _line(sid: str, n: dict, nodes: dict, rev: dict, with_diff: bool, flags: dict) -> str:
    kind = n["kind"]
    code = n.get("domain", "code") == "code"
    if kind == "group":
        kids = sorted(n["children"], key=lambda c: -nodes[c]["rank"])
        has = ", ".join(nodes[c]["name"] for c in kids[:5] if nodes[c]["kind"] != "more")
        return f"{sid} group {n.get('path') or n['name']} | {n.get('files', '?')} files{_status(n)} | has: {has}"
    where = f"{n['file']}:{n['line']}" if n.get("line") else n["file"]
    if kind == "class":
        names = [nodes[c]["name"] for c in n["children"]] + [h[1] for h in n.get("hidden", [])]
        label = "members" if not code else "methods"
        text = f"{sid} {n.get('sub') or 'class'} {where} | {n['sig']}{_status(n)} | {label}: {', '.join(names[:8]) or '-'}"
    elif kind == "module" and n.get("opaque"):
        text = (f"{sid} file {n['file']} ({n.get('sub') or 'unparsed'}; codemap has no parser for it: "
                f"describe it from these lines){_status(n)}")
        with_diff = True
    elif kind == "module":
        names = [nodes[c]["name"] for c in n["children"]] + [h[1] for h in n.get("hidden", [])]
        label = "functions" if code else "items"
        text = f"{sid} {n.get('sub') or 'module'} {n['file']}{_status(n)} | {label}: {', '.join(names[:8]) or '-'}"
    else:
        what = n.get("sub") or kind
        text = f"{sid} {what} {n['qual']} {where} | {n['sig']}{_status(n)}"
        calls = ", ".join(rev.get(i, short_name(q) if code else q) for i, q in n.get("calls", [])[:4])
        users = ", ".join(rev.get(i, short_name(q) if code else q) for i, q in n.get("callers", [])[:3])
        if n.get("routes"):
            text += f" | serves: {', '.join(n['routes'][:2])}"
        if calls:
            text += f" | {'calls' if code else 'uses'}: {calls}"
        if users:
            text += f" | used by: {users}"
    if n.get("doc"):
        text += f" | doc: {n['doc']}"
    if with_diff and n["status"] in CHANGED and n.get("diff"):
        changed = [ln for ln in n["diff"] if ln[:1] in "+-"][:12 if n.get("opaque") else 8]
        text += "".join(f"\n    {ln[:110]}" for ln in changed)
    for fid in n.get("flags", [])[:3]:
        f = flags.get(fid)
        if f:
            text += f"\n    ! {f['id']} {f['sev'].upper()} {f['rule']}: {f['msg']} (line {f['line']})"
    return text


def build_worksheet(ir: dict, tier: str, budget: int | None, output_hint=None) -> tuple:
    """Returns (worksheet text, item count, token estimate)."""
    nodes, short = ir["nodes"], ir["short"]
    flags = {f["id"]: f for f in ir.get("risks", [])}
    rev = {nid: sid for sid, nid in short.items()}
    budget = budget or DEFAULT_BUDGET.get(tier, 8000)
    candidates = []
    for sid, nid in short.items():
        n = nodes[nid]
        if n.get("lsrc") in ("agent", "cache"):
            continue
        if tier == "lite" and not _weak(n):
            continue
        changed = n["status"] in CHANGED or n.get("changed", 0) > 0
        bucket = 0 if n["kind"] == "group" else (1 if n.get("flags") or n.get("opaque") else
                                                  (2 if changed and n["kind"] in ("class", "module") else
                                                   (3 if changed else (4 if n["kind"] in ("class", "module") else 5))))
        candidates.append((bucket, -n.get("rank", 0), sid))
    candidates.sort()
    with_diff = tier in ("full", "video")
    lines, used = [], 0
    for _b, _r, sid in candidates:
        line = _line(sid, nodes[short[sid]], nodes, rev, with_diff, flags)
        cost = est_tokens(line)
        if used + cost > budget and lines:
            break
        lines.append(line)
        used += cost
    if not lines and tier != "video":
        return "", 0, 0

    scope = ir["scope"]
    head = [
        f"# codemap worksheet | tier: {tier} | {len(lines)} items",
        "Task: give each item a plain-English title and a one-line summary so a newcomer understands this code.",
        f"Scope: {scope['label']}" + (f" | {scope['files']} files +{scope['add']} -{scope['del']}" if scope["mode"] == "diff" else ""),
    ]
    for c in ir.get("commits", [])[:5]:
        head.append(f"Commit: {c.splitlines()[0][:120]}")
    out = head + ["", "## Views (top-down; each line is one diagram)"]
    for v in ir["views"][:40]:
        node = nodes[v["node"]]
        kids = " ".join(rev.get(c, "+more") for c in v["children"])
        out.append(f"{v['id']} L{v['level']} {rev.get(v['node'], node['name'])}: {kids}")
    out += ["", "## Items (id kind where | signature | status | facts; diff lines indented)"] + lines
    if tier == "video":
        out += ["", "## Narration beats (one spoken sentence each, <= 25 words; describe, never read code aloud)"]
        for b in ir.get("plan", []):
            target = rev.get(b.get("node") or "", b.get("view") or "")
            out.append(f"{b['sid']} {b['kind']} {target}".rstrip())
    risky = [f for f in ir.get("risks", []) if f["sev"] in ("high", "medium")][:12]
    if risky:
        out += ["", "## Risk flags (deterministic rules; confirm each, or say why it is a false positive)"]
        out += [f"{f['id']} {f['sev'].upper()} {f['file']}:{f['line']} {f['rule']}: {f['msg']}" for f in risky]
    out += [
        "",
        (f"## Reply: write {str(output_hint).rstrip('/')}/labels.json, then render using this output directory"
         if output_hint else "## Reply: write labels.json beside this worksheet, then render using this output directory"),
        '{"why": "1-2 sentences: the goal of this change (what problem it solves)",',
        ' "items": {"g1": ["Title of 2-5 words", "What it does / what changed, max 20 words"]},',
        ' "views": {"v0": "One-line caption for the diagram"}'
        + (',\n "risks": {"r1": "Real issue: ... / False positive: ..."}' if risky else "")
        + (',\n "beats": {"b1": "Narration sentence"}}' if tier == "video" else "}"),
        "Rules: plain words, no identifiers in titles; for changed items say what changed and why; skip items you cannot improve.",
    ]
    text = "\n".join(out) + "\n"
    return text, len(lines), est_tokens(text)


# ---------------------------------------------------------------- merge

def merge_labels(ir: dict, labels: dict) -> dict:
    """Apply the agent's labels.json. Returns counts for reporting."""
    nodes, short = ir["nodes"], ir.get("short", {})
    applied, unknown = 0, []
    for key, value in (labels.get("items") or {}).items():
        nid = short.get(key, key)
        if nid not in nodes:
            unknown.append(key)
            continue
        if isinstance(value, str):
            title, summary = None, value
        elif isinstance(value, dict):
            title, summary = value.get("title"), value.get("sum") or value.get("summary")
        elif isinstance(value, (list, tuple)) and value:
            title, summary = value[0], (value[1] if len(value) > 1 else None)
        else:
            unknown.append(key)
            continue
        n = nodes[nid]
        if title:
            n["title"] = str(title).strip()[:60]
        if summary:
            n["sum"] = _sentence(str(summary))[:240]
        n["lsrc"] = "agent"
        applied += 1
    if labels.get("why"):
        ir["why"] = [{"source": "agent", "text": _sentence(str(labels["why"]))}] + \
            [w for w in ir.get("why", []) if w["source"] != "agent"]
    captions = labels.get("views") or {}
    for v in ir["views"]:
        caption = captions.get(v["id"]) or captions.get(v["node"])    # worksheet id, or the stable node id
        if caption:
            v["caption"] = _sentence(str(caption))
    says = labels.get("beats") or {}
    for b in ir.get("plan", []):
        if b.get("sid") in says:
            b["say"] = _sentence(str(says[b["sid"]]))
    notes = labels.get("risks") or {}
    for f in ir.get("risks", []):
        if f["id"] in notes:
            f["note"] = _sentence(str(notes[f["id"]]))[:240]
    return {"applied": applied, "unknown": unknown}
