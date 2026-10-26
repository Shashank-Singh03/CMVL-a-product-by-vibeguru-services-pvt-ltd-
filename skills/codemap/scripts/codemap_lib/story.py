"""The walkthrough: a deterministic beat plan over the views, plus narration text.

A beat is one spoken sentence plus what the screen shows (a title card, a view with
some boxes highlighted, or a code diff). Layout and animation are templates, so the
only thing an LLM may add is better sentences (labels.json "beats").
"""
from __future__ import annotations

from .diffmap import CHANGED, changed_symbols, own_status
from .util import human_list, plural, speakable

SECONDS_PER_WORD = 0.4      # ~150 words per minute
BEAT_PAUSE = 0.6


def est_seconds(text: str) -> float:
    return round(len(text.split()) * SECONDS_PER_WORD + BEAT_PAUSE, 1)


def _changed_kids(nodes: dict, view: dict) -> list:
    return [c for c in view["order"] if nodes[c]["kind"] != "more"
            and (nodes[c]["status"] in CHANGED or nodes[c].get("changed", 0) > 0)]


def _size(n: dict) -> int:
    return n.get("add", 0) + n.get("del", 0)


def plan_beats(ir: dict, minutes: float = 2.0) -> list:
    """Diff mode: a short tour of every changed area, then deep dives into the biggest changes."""
    nodes, views = ir["nodes"], ir["views"]
    by_node = {v["node"]: v for v in views}
    budget = minutes * 60 - 7          # keep room for the outro
    beats: list = []
    used = [0.0]

    def add(kind: str, view=None, focus=None, node=None, cost: float = 7.0) -> bool:
        if used[0] + cost > budget and kind != "title":
            return False
        beats.append({"kind": kind, "view": view, "focus": focus or [], "node": node})
        used[0] += cost
        return True

    add("title", cost=8)
    if ir["scope"]["mode"] == "diff" or ir.get("why"):
        add("why", cost=7)
    if not views:
        beats.append({"kind": "outro", "view": None, "focus": [], "node": None})
        return beats
    root = views[0]

    if ir["scope"]["mode"] == "diff":
        add("overview", root["id"], _changed_kids(nodes, root), "root", cost=9)
        units: list = []          # changed classes/modules in reading order

        def tour(view: dict) -> None:
            for c in _changed_kids(nodes, view):
                kind = nodes[c]["kind"]
                if kind in ("class", "module"):
                    units.append(c)
                elif kind == "group" and c in by_node:
                    sub = by_node[c]
                    add("overview", sub["id"], _changed_kids(nodes, sub), c, cost=9)
                    tour(sub)
        tour(root)
        risky = [f for f in ir.get("risks", []) if f["sev"] in ("high", "medium")]
        if risky:                  # security heads-up before the deep dives
            flagged = [nid for nid, n in nodes.items() if n.get("flags")]
            add("risks", None, flagged[:6], None, cost=11)
        dives = sorted(units, key=lambda u: -_size(nodes[u]))
        chosen = []
        for u in dives:          # biggest changes first, as long as the budget allows
            v = by_node.get(u)
            kids = _changed_kids(nodes, v) if v else []
            top = sorted(kids, key=lambda c: -_size(nodes[c]))[:1]
            cost = 8 + 6 * len(top) + (9 if top and nodes[top[0]].get("diff") else 0)
            if used[0] + cost > budget:
                continue
            used[0] += cost
            chosen.append((u, v, kids, top))
        order = {u: i for i, u in enumerate(units)}
        for u, v, kids, top in sorted(chosen, key=lambda x: order[x[0]]):
            if v is None:
                beats.append({"kind": "focus", "view": by_node.get(nodes[u]["parent"], root)["id"], "focus": [u], "node": u})
                continue
            beats.append({"kind": "overview", "view": v["id"], "focus": kids, "node": u})
            for f in top:
                beats.append({"kind": "focus", "view": v["id"], "focus": [f], "node": f})
                if nodes[f].get("diff"):
                    beats.append({"kind": "code", "view": v["id"], "focus": [f], "node": f})
    else:
        add("overview", root["id"], [c for c in root["order"] if nodes[c]["kind"] != "more"], "root", cost=9)
        if any(f["sev"] in ("high", "medium") for f in ir.get("risks", [])):
            add("risks", None, [nid for nid, n in nodes.items() if n.get("flags")][:6], None, cost=11)
        for c in [c for c in root["order"] if nodes[c]["kind"] != "more"][:6]:
            if not add("focus", root["id"], [c], c, cost=6):
                break
        tops = sorted([c for c in root["children"] if c in by_node], key=lambda c: -nodes[c]["rank"])[:3]
        for c in tops:
            v = by_node[c]
            kids = [k for k in v["order"] if nodes[k]["kind"] != "more"]
            if not add("overview", v["id"], kids, c, cost=8):
                break
            for k in sorted(kids, key=lambda k: -nodes[k]["rank"])[:2]:
                if not add("focus", v["id"], [k], k, cost=6):
                    break
    beats.append({"kind": "outro", "view": None, "focus": [], "node": None})
    return beats


def _label(n: dict) -> str:
    return n["title"] if n.get("title") else speakable(n["name"])


VERB = {"added": "was added", "modified": "changed", "deleted": "was removed",
        "context": "is unchanged but connected to the change"}


def _change_phrase(nodes: dict, ids: list) -> str:
    """"A and B changed; C was added; D was removed" for the boxes in `ids`."""
    by: dict = {"modified": [], "added": [], "deleted": []}
    for c in ids:
        status = nodes[c]["status"]
        if status in by:
            by[status].append(_label(nodes[c]))
    parts = []
    if by["modified"]:
        parts.append(f"{human_list(by['modified'], 4)} changed")
    for status, word in (("added", "added"), ("deleted", "removed")):
        names = by[status]
        if names:
            parts.append(f"{human_list(names, 4)} {'was' if len(names) == 1 else 'were'} {word}")
    return "; ".join(parts)


def narrate(ir: dict) -> None:
    """Build ir["beats"] from the plan: text (agent-written `say` wins) and an estimated duration."""
    nodes, scope = ir["nodes"], ir["scope"]
    by_id = {v["id"]: v for v in ir["views"]}
    diff = scope["mode"] == "diff"
    counts = {s: 0 for s in CHANGED}
    for n in changed_symbols(nodes):
        counts[own_status(n)] += 1
    symbols = sum(counts.values()) if diff else sum(1 for n in nodes.values() if n["kind"] in ("class", "function", "method"))
    why = next((w["text"] for w in ir.get("why", []) if w["source"] == "agent"), None) \
        or next((w["text"] for w in ir.get("why", [])), None)

    keep = []
    for planned in ir.get("plan", []):
        b = dict(planned)
        kind = b["kind"]
        text = ""
        if kind == "title":
            if diff:
                mixed = len({n.get("domain") for n in nodes.values() if n.get("domain")}) > 1
                what = plural(symbols, "piece of code and configuration", "pieces of code and configuration") if mixed                     else plural(symbols, "class or function", "classes and functions")
                text = (f"Here is what changed in {ir['repo']}. {plural(scope['files'], 'file')} changed, "
                        f"{scope['add']} lines added and {scope['del']} removed, touching {what}.")
            else:
                text = (f"Here is a map of {ir['repo']}: {plural(ir['stats']['files'], 'source file')} "
                        f"with {symbols} classes and functions, grouped into layers.")
        elif kind == "why":
            if not why:
                continue
            text = f"The goal: {why}"
        elif kind == "overview":
            view = by_id[b["view"]]
            node = nodes[b["node"]]
            names = [_label(nodes[c]) for c in b["focus"]]
            phrase = _change_phrase(nodes, b["focus"]) if diff else ""
            if view.get("caption"):
                text = view["caption"]
            elif b["node"] == "root":
                text = (f"At the top level, the change touches {plural(len(names), 'part')}: {human_list(names)}."
                        if diff else f"At the top level there are {plural(len(names), 'part')}: {human_list(names)}.")
            elif node["kind"] in ("class", "module"):
                if diff and node["status"] == "added":
                    text = f"{_label(node)} is new. {node.get('sum', '')}".strip()
                else:
                    text = (f"Zooming into {_label(node)}: {phrase}." if diff and phrase
                            else f"Zooming into {_label(node)}: {human_list(names)}.")
            else:
                text = (f"In {_label(node)}: {phrase}." if diff and phrase
                        else f"Inside {_label(node)}: {human_list(names)}.")
        elif kind == "focus":
            n = nodes[b["node"]]
            summary = n.get("sum") or ""
            if diff:
                text = f"{_label(n)} {VERB.get(n['status'], 'changed')}. {summary}".strip()
            else:
                text = f"{_label(n)}. {summary}".strip()
        elif kind == "code":
            n = nodes[b["node"]]
            if n["status"] == "added":
                text = f"Here is the new code for {_label(n)}, {plural(n['add'], 'line')} in total."
            elif n["status"] == "deleted":
                text = f"And this is the code that was removed from {_label(n)}."
            else:
                text = (f"Here is the change inside {_label(n)}: green lines were added, red lines removed. "
                        f"{plural(n['add'], 'line')} in, {n['del']} out.")
        elif kind == "risks":
            risky = [f for f in ir.get("risks", []) if f["sev"] in ("high", "medium")]
            if not risky:
                continue
            top = risky[:2]
            text = (f"Heads-up: {plural(len(risky), 'possible risk')} to review. "
                    + " ".join(f["note"] if f.get("note") else f["msg"] for f in top))
        elif kind == "outro":
            if diff:
                text = (f"That's the whole change: {counts['added']} added, {counts['modified']} changed and "
                        f"{counts['deleted']} removed. Open the codemap page to explore every layer.")
            else:
                text = "That's the map. Open the codemap page to drill into any part."
        b["text"] = b.get("say") or text
        b["est"] = est_seconds(b["text"])
        keep.append(b)
    ir["beats"] = keep
