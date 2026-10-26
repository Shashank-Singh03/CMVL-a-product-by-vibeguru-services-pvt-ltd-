"""Map diff hunks onto symbols: which classes/functions were added, modified or deleted."""
from __future__ import annotations

from collections import defaultdict

from .model import sym_id
from .scan import language_of, parse_source
from .util import dedent_lines

MAX_DIFF_LINES = 30
CHANGED = ("added", "modified", "deleted")
SYMBOL_KINDS = ("class", "function", "method")


def own_status(node: dict) -> str:
    """A class's own change (not the roll-up of its methods), for honest counts."""
    return node.get("own_status", node["status"]) if node["kind"] == "class" else node["status"]


def changed_symbols(nodes: dict) -> list:
    return [n for n in nodes.values() if n["kind"] in SYMBOL_KINDS and own_status(n) in CHANGED]


def _cap(lines: list) -> list:
    lines = dedent_lines(lines)
    if len(lines) > MAX_DIFF_LINES:
        return lines[:MAX_DIFF_LINES] + [f" ... {len(lines) - MAX_DIFF_LINES} more lines"]
    return lines


def _whole(sym, lines: list, tag: str) -> list:
    return _cap([tag + ln for ln in lines[sym.line - 1: sym.end]])


def _module_lines(symbols: list, lines: list, tag: str) -> list:
    """Nonblank lines outside all symbols, including imports/constants in a new file."""
    out = []
    cursor = 1
    for start, end in sorted((max(1, s.line), s.end) for s in symbols):
        if end < cursor:
            continue
        out.extend(tag + ln for ln in lines[cursor - 1:start - 1] if ln.strip())
        cursor = end + 1
    out.extend(tag + ln for ln in lines[cursor - 1:] if ln.strip())
    return out


def _own_span(sym, members: list) -> int:
    """Lines of a class that are not inside one of its methods."""
    inner = sum(m.end - m.line + 1 for m in members)
    return max(0, (sym.end - sym.line + 1) - inner)


def _owner(sorted_syms: list, line: int):
    for s in sorted_syms:
        if s.line <= line <= s.end:
            return s
    return None


def _contextual(sym, lines: list, added: set, removed: dict) -> list:
    """The symbol's current code with changed lines marked, trimmed to the changed region."""
    out = []
    for ln in range(sym.line, sym.end + 1):
        out.extend("-" + t for t in removed.get(ln, ()))
        out.append(("+" if ln in added else " ") + (lines[ln - 1] if ln - 1 < len(lines) else ""))
    out.extend("-" + t for t in removed.get(sym.end + 1, ()))
    marks = [i for i, t in enumerate(out) if t[:1] in "+-"]
    if not marks:
        return []
    out = out[max(0, marks[0] - 2): marks[-1] + 3]
    return _cap(out)


def apply_changes(files: dict, texts: dict, changes, graph) -> tuple:
    """Set statuses on symbols (mutates files/graph).

    Returns (changed files that could not be mapped, {path: previous FileInfo} for modified files).
    """
    other, base_infos = [], {}
    for path, fd in sorted(changes.files.items()):
        if not language_of(path) or (fd.status != "deleted" and path not in files):
            note = "lockfile" if language_of(path) is None and not fd.binary else ""
            other.append({"path": path, "status": fd.status, "add": fd.add, "del": fd.dele, "note": note})
            continue
        if fd.status == "deleted":
            old = changes.old_text(path)
            if old is None:
                other.append({"path": path, "status": fd.status, "add": fd.add, "del": fd.dele, "note": ""})
                continue
            fi = parse_source(path, old)
            fi.status, fi.dele = "deleted", fd.dele
            old_lines = old.splitlines()
            module_lines = _module_lines(fi.symbols, old_lines, "-")
            fi.module_del = len(module_lines)
            fi.module_diff = _cap(module_lines)
            members = defaultdict(list)
            for s in fi.symbols:
                if s.parent:
                    members[s.parent].append(s)
            for s in fi.symbols:
                s.status, s.calls = "deleted", []
                s.dele = _own_span(s, members[s.id]) if s.kind == "class" else s.end - s.line + 1
                if s.kind != "class":
                    s.diff = _whole(s, old_lines, "-")
                graph.syms[s.id] = s
            files[path] = fi
            graph.files[path] = fi
            continue
        fi = files.get(path)
        if fi is None:
            continue
        lines = texts.get(path, "").splitlines()
        fi.add, fi.dele = fd.add, fd.dele
        members = defaultdict(list)
        for s in fi.symbols:
            if s.parent:
                members[s.parent].append(s)
        if fd.status == "added":
            fi.status = "added"
            module_lines = _module_lines(fi.symbols, lines, "+")
            fi.module_add = len(module_lines)
            fi.module_diff = _cap(module_lines)
            for s in fi.symbols:
                s.status = "added"
                s.add = _own_span(s, members[s.id]) if s.kind == "class" else s.end - s.line + 1
                if s.kind != "class":
                    s.diff = _whole(s, lines, "+")
            continue

        fi.status = "modified"
        old = changes.old_text(path) or ""
        base = parse_source(fd.old_path, old) if old else None
        if base is not None:
            base_infos[path] = base
        base_syms = {s.qual: s for s in base.symbols} if base else {}
        cur_syms = {s.qual: s for s in fi.symbols}
        cur_sorted = sorted(fi.symbols, key=lambda s: (s.end - s.line, s.line))
        base_sorted = sorted(base.symbols, key=lambda s: (s.end - s.line, s.line)) if base else []
        added_lines: dict = defaultdict(set)
        removed: dict = defaultdict(lambda: defaultdict(list))
        raw: dict = defaultdict(list)
        for h in fd.hunks:
            anchor = h.new_start if h.new_len else h.new_start + 1
            for ln, txt in h.minus:
                if not txt.strip():          # blank lines never make a symbol "changed"
                    continue
                bs = _owner(base_sorted, ln)
                cur = cur_syms.get(bs.qual) if bs else None
                if bs is None:
                    fi.module_del += 1
                    fi.module_diff.append("-" + txt)
                elif cur is not None:
                    cur.dele += 1
                    removed[cur.id][anchor].append(txt)
                    raw[cur.id].append("-" + txt)
            for ln, txt in h.plus:
                if not txt.strip():
                    continue
                s = _owner(cur_sorted, ln)
                if s is None:
                    fi.module_add += 1
                    fi.module_diff.append("+" + txt)
                else:
                    s.add += 1
                    added_lines[s.id].add(ln)
                    raw[s.id].append("+" + txt)
        fi.module_diff = _cap(fi.module_diff)
        for s in fi.symbols:
            if base and s.qual not in base_syms:
                s.status = "added"
                s.add = _own_span(s, members[s.id]) if s.kind == "class" else s.end - s.line + 1
                s.dele = 0
                if s.kind != "class":
                    s.diff = _whole(s, lines, "+")
            elif s.add or s.dele:
                s.status = "modified"
                s.diff = _cap(raw[s.id]) if s.kind == "class" else _contextual(s, lines, added_lines[s.id], removed[s.id])
        # symbols that existed before but are gone now
        old_lines = old.splitlines()
        base_members = defaultdict(list)
        for s in base_syms.values():
            if s.parent:
                base_members[s.parent].append(s)
        for qual, bs in base_syms.items():
            if qual in cur_syms:
                continue
            bs.status, bs.calls, bs.file = "deleted", [], path
            bs.dele = _own_span(bs, base_members[bs.id]) if bs.kind == "class" else bs.end - bs.line + 1
            if bs.kind != "class":
                bs.diff = _whole(bs, old_lines, "-")
            bs.id = sym_id(bs.kind, path, bs.qual)
            if bs.lexical_parent:
                old_prefix, enclosing_qual = bs.lexical_parent.split("#", 1)
                enclosing_kind = "class" if old_prefix.startswith("c:") else "function"
                bs.lexical_parent = sym_id(enclosing_kind, path, enclosing_qual)
            if bs.parent:
                bs.parent = sym_id("class", path, bs.qual.rsplit(".", 1)[0])
            fi.symbols.append(bs)
            graph.syms[bs.id] = bs
    return other, base_infos
