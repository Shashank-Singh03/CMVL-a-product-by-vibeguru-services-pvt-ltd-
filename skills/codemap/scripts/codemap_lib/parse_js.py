"""JavaScript / TypeScript scanner (zero dependencies).

This is not a full parser. It blanks out comments, strings and regex literals,
then finds classes, functions, methods, imports and call sites with regular
expressions plus brace matching. That is enough to map structure. A tree-sitter
backend can replace it later behind the same FileInfo interface.
"""
from __future__ import annotations

import bisect
import re

from .model import FileInfo, Imp, Sym, dedupe_ids, sym_id
from .util import first_sentence, sha1

_ID = r"[A-Za-z_$][\w$]*"
TS_EXTS = (".ts", ".tsx", ".mts", ".cts")

_REGEX_AFTER_WORDS = frozenset("return typeof case do else in of new delete void throw yield await".split())
_REGEX_AFTER_CHARS = frozenset("(,=:[!&|?{};+-*%<>~^")

NOT_METHOD = frozenset(
    "if for while switch catch return function typeof new delete void do else try throw case "
    "await yield super import export".split())
NOT_CALL = NOT_METHOD | frozenset("async of in instanceof require constructor".split())

RE_CLASS = re.compile(
    r"\b(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class(?:\s+(?!extends\b)(" + _ID + r"))?\s*"
    r"(?:<[^{]*?>)?\s*(?:extends\s+([\w$.]+)(?:\s*<[^{]*?>)?(?:\([^{]*?\))?\s*)?(?:implements\s+[^{]+?)?\{")
RE_FUNC = re.compile(
    r"\b(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(" + _ID + r")?\s*(?:<[^(]*?>)?\s*\(")
RE_BINDING = re.compile(
    r"(?:\b(?:export\s+)?(?:const|let|var)\s+(" + _ID + r")|\b(?:module\.)?exports\.(" + _ID + r"))"
    r"\s*(?::[^=;\n]+?)?=\s*(?:async\s+)?(?=function\b|\(|" + _ID + r"\s*=>|<)")
RE_WRAPPED_BINDING = re.compile(
    r"\b(?:export\s+)?(?:const|let|var)\s+(" + _ID + r")\s*(?::[^=;\n]+?)?=\s*("
    + _ID + r"(?:\." + _ID + r")?)\s*")
RE_METHOD = re.compile(
    r"(?:^|(?<=[;{}\s]))((?:(?:public|private|protected|static|async|readonly|override|abstract|declare|get|set)\s+)*)"
    r"\*?\s*(#?" + _ID + r")\s*\??\s*(?:<[^(]*?>)?\s*\(", re.M)
RE_FIELD = re.compile(
    r"(?:^|(?<=[;{}\s]))(?:(?:public|private|protected|static|readonly|override)\s+)*(#?" + _ID + r")"
    r"\s*(?::[^=;]+?)?=\s*(?:async\s+)?(?=\(|" + _ID + r"\s*=>)", re.M)
RE_ARROW_ID = re.compile(_ID + r"\s*=>")
RE_CALL = re.compile(
    r"(?<![\w$.#])(" + _ID + r"(?:\s*\??\.\s*#?" + _ID + r")*)\s*(?:<[\w$.,\s\[\]|]*>)?\s*\(")
RE_JSX = re.compile(r"<([A-Z][\w$]*(?:\.[A-Z][\w$]*)*)[\s/>]")
RE_CALLBACK = re.compile(r"[(,]\s*((?:this\.)?#?" + _ID + r")\s*(?=[,)])")   # map(renderNote), on("x", this.handle)
RE_DOT = re.compile(r"\s*\??\.\s*")

RE_IMPORT = re.compile(r"\bimport\s+(?:type\s+)?([\w$*{}\s,]+?)\s*from\s*['\"]([^'\"\n]+)['\"]")
RE_IMPORT_BARE = re.compile(r"\bimport\s*['\"]([^'\"\n]+)['\"]")
RE_EXPORT_FROM = re.compile(
    r"\bexport\s+(?:type\s+)?(\*(?:\s+as\s+" + _ID + r")?|\{[^}]*\})\s*from\s*['\"]([^'\"\n]+)['\"]")
RE_REQUIRE = re.compile(
    r"(?:\b(?:const|let|var)\s+(" + _ID + r"|\{[^}]*\})\s*=\s*)?\brequire\s*\(\s*['\"]([^'\"\n]+)['\"]\s*\)")
RE_DYNAMIC = re.compile(r"\bimport\s*\(\s*['\"]([^'\"\n]+)['\"]\s*\)")
_TYPE = _ID + r"(?:\." + _ID + r")*"
RE_THIS_NEW = re.compile(r"\bthis\.#?(" + _ID + r")\s*=\s*new\s+(" + _TYPE + r")")
RE_THIS_ASSIGN = re.compile(r"\bthis\.#?(" + _ID + r")\s*=\s*(" + _ID + r")\s*[;\n]")
RE_PARAM_PROP = re.compile(r"\b(?:private|public|protected|readonly)\s+(?:readonly\s+)?(" + _ID + r")\s*\??\s*:\s*(" + _TYPE + r")")
RE_PARAM_TYPE = re.compile(r"(" + _ID + r")\s*\??\s*:\s*(" + _TYPE + r")")
RE_EXPORT_LIST = re.compile(r"\bexport\s*\{([^}]*)\}(?!\s*from)")
RE_EXPORT_DEFAULT = re.compile(r"\bexport\s+default\s+(" + _ID + r")\s*;?\s*$", re.M)
RE_MODULE_EXPORTS = re.compile(r"\bmodule\.exports\s*=\s*(" + _ID + r"|\{[^}]*\})")


_INTERESTING = re.compile(r"[/'\"`]")
_STRING_END = {"'": re.compile(r"\\.|'|\n", re.S), '"': re.compile(r'\\.|"|\n', re.S)}
_TEMPLATE_TOKEN = re.compile(r"\\.|`|\$\{|[{}]", re.S)
_NOT_NEWLINE = re.compile(r"[^\n]")


def _previous(text: str, pos: int) -> tuple:
    """(last significant character, last word) before `pos`, for regex-vs-division decisions."""
    k = pos - 1
    while k >= 0 and text[k] in " \t\r\n":
        k -= 1
    if k < 0:
        return "", ""
    ch = text[k]
    if ch.isalnum() or ch in "_$":
        s = k
        while s >= 0 and (text[s].isalnum() or text[s] in "_$"):
            s -= 1
        return ch, text[s + 1:k + 1]
    return ch, ""


def blank(text: str, keep_strings: bool) -> str:
    """Replace comments (and optionally string/regex contents) with spaces; offsets and newlines survive.

    Jumps between the only characters that can open a comment, string, template or
    regex literal, so ordinary code is skipped at C speed.
    """
    n = len(text)
    wipes = []        # (start, end) ranges to blank
    i = 0
    while True:
        m = _INTERESTING.search(text, i)
        if not m:
            break
        j = m.start()
        c = text[j]
        if c == "/":
            nxt = text[j + 1:j + 2]
            if nxt == "/":
                end = text.find("\n", j)
                end = n if end < 0 else end
                wipes.append((j, end))
                i = end
                continue
            if nxt == "*":
                end = text.find("*/", j + 2)
                end = n if end < 0 else end + 2
                wipes.append((j, end))
                i = end
                continue
            last, word = _previous(text, j)
            if last == "" or last in _REGEX_AFTER_CHARS or word in _REGEX_AFTER_WORDS:
                k, in_class = j + 1, False
                while k < n and text[k] != "\n":
                    ch = text[k]
                    if ch == "\\":
                        k += 2
                        continue
                    if ch == "[":
                        in_class = True
                    elif ch == "]":
                        in_class = False
                    elif ch == "/" and not in_class:
                        break
                    k += 1
                if k < n and text[k] == "/":
                    wipes.append((j + 1, k))
                    i = k + 1
                    continue
            i = j + 1
            continue
        if c in "'\"":
            pattern, k = _STRING_END[c], j + 1
            while True:
                t = pattern.search(text, k)
                if not t:
                    k = n
                    break
                if t.group() == c or t.group() == "\n":
                    k = t.start()
                    break
                k = t.end()          # escaped character
            if not keep_strings:
                wipes.append((j + 1, k))
            i = k + 1
            continue
        # template literal: blank the literal text but keep ${ ... } expressions (they contain calls)
        k, depth, seg = j + 1, 0, j + 1
        while True:
            t = _TEMPLATE_TOKEN.search(text, k)
            if not t:
                k = n
                break
            tok = t.group()
            k = t.end()
            if tok == "`" and depth == 0:
                k = t.start()
                break
            if tok == "${":
                if depth == 0 and not keep_strings:
                    wipes.append((seg, t.start()))
                depth += 1
            elif tok == "{" and depth:
                depth += 1
            elif tok == "}" and depth:
                depth -= 1
                if depth == 0:
                    seg = t.end()
        if not keep_strings:
            wipes.append((seg, k))
        i = k + 1
    if not wipes:
        return text
    pieces, prev = [], 0
    for a, b in wipes:
        if b <= prev:
            continue
        a = max(a, prev)
        pieces.append(text[prev:a])
        pieces.append(_NOT_NEWLINE.sub(" ", text[a:b]))
        prev = b
    pieces.append(text[prev:])
    return "".join(pieces)


def _match(code: str, pos: int, open_ch: str, close_ch: str, limit: int = 20000) -> int:
    depth = 0
    end = min(len(code), pos + limit)
    for i in range(pos, end):
        ch = code[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
    return -1


def _after_params(code: str, pos: int) -> int:
    """Skip whitespace and an optional `: ReturnType` after a parameter list."""
    n = len(code)
    i = pos
    while i < n and code[i].isspace():
        i += 1
    if i < n and code[i] == ":":
        depth = 0
        i += 1
        while i < n:
            if code.startswith("=>", i) and depth <= 0:
                break
            ch = code[i]
            if ch in "(<[":
                depth += 1
            elif ch in ")>]":
                depth -= 1
            elif ch in "{;" and depth <= 0:
                break
            i += 1
    while i < n and code[i].isspace():
        i += 1
    return i


def _type_arguments_end(code: str, pos: int) -> int:
    """Balance nested TypeScript type arguments, excluding the `>` in `=>`."""
    depth = 0
    for i in range(pos, min(len(code), pos + 20000)):
        if code[i] == "<":
            depth += 1
        elif code[i] == ">" and code[i - 1:i] != "=":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _fn_body(code: str, pos: int, bmatch: dict):
    """Given the position right after `name =`, return (body_start, body_end) of a function value."""
    n = len(code)
    if code.startswith("function", pos):
        p = code.find("(", pos)
        q = _match(code, p, "(", ")") if p >= 0 else -1
        if q < 0:
            return None
        a = _after_params(code, q + 1)
        return (a, bmatch[a]) if a < n and code[a] == "{" and a in bmatch else None
    if pos < n and code[pos] == "<":
        pos = code.find("(", pos)
        if pos < 0:
            return None
    if pos < n and code[pos] == "(":
        q = _match(code, pos, "(", ")")
        if q < 0:
            return None
        a = _after_params(code, q + 1)
        if not code.startswith("=>", a):
            return None
        b = a + 2
    else:
        m = RE_ARROW_ID.match(code, pos)
        if not m:
            return None
        b = m.end()
    while b < n and code[b].isspace():
        b += 1
    if b < n and code[b] == "{":
        e = bmatch.get(b)
        return (b, e) if e is not None else None
    if b < n and code[b] == "(":
        e = _match(code, b, "(", ")")
        return (b, e) if e >= 0 else None
    ends = [x for x in (code.find("\n", b), code.find(";", b)) if x >= 0]
    return b, (min(ends) if ends else n - 1)


def _jsdoc(text: str, start: int) -> tuple:
    """(first sentence of the /** doc */ right before `start`, offset where the comment begins)."""
    before = text[:start].rstrip()
    if not before.endswith("*/"):
        return "", None
    open_at = before.rfind("/**")
    if open_at < 0:
        return "", None
    body = []
    for ln in before[open_at + 3:-2].splitlines():
        ln = ln.strip().lstrip("*").strip()
        if ln.startswith("@"):
            break
        body.append(ln)
    return first_sentence(" ".join(body)), open_at


def _clause(clause: str, spec: str, fi: FileInfo) -> None:
    """Parse `X, { a as b, type C }` / `* as ns` import clauses."""
    clause = clause.strip()
    rest = clause
    if not clause.startswith(("{", "*")):
        default, _, rest = clause.partition(",")
        if default.strip():
            fi.imports.append(Imp(default.strip(), spec, "default"))
        rest = rest.strip()
    if rest.startswith("*"):
        m = re.match(r"\*\s*as\s+(" + _ID + ")", rest)
        if m:
            fi.imports.append(Imp(m.group(1), spec, None))
    elif rest.startswith("{"):
        for part in rest.strip("{} \n\t").split(","):
            part = re.sub(r"^type\s+", "", part.strip())
            if not part:
                continue
            name, _, alias = part.partition(" as ")
            fi.imports.append(Imp((alias or name).strip(), spec, name.strip()))


def _calls_in(code: str, a: int, b: int, jsx: bool) -> dict:
    seen: dict = {}
    for m in RE_CALL.finditer(code, a, b):
        parts = tuple(RE_DOT.split(m.group(1)))
        if parts[0] in NOT_CALL:
            continue
        seen.setdefault(parts, m.start())
    if jsx:
        for m in RE_JSX.finditer(code, a, b):
            seen.setdefault(tuple(m.group(1).split(".")), m.start())
    for m in RE_CALLBACK.finditer(code, a, b):
        parts = tuple(m.group(1).split("."))
        if parts[-1] not in NOT_CALL and parts[0] not in ("true", "false", "null", "undefined"):
            seen.setdefault(("&",) + parts, m.start())
    return seen


def parse_js(path: str, text: str) -> FileInfo:
    fi = FileInfo(path=path, lang="typescript" if path.endswith(TS_EXTS) else "javascript")
    fi.loc = sum(1 for ln in text.splitlines() if ln.strip())
    code_nc = blank(text, keep_strings=True)
    code = blank(text, keep_strings=False)
    jsx = not path.endswith((".ts", ".mts", ".cts"))
    newlines = [m.start() for m in re.finditer("\n", text)]

    def line_of(pos: int) -> int:
        return bisect.bisect_left(newlines, pos) + 1

    # brace depth + matching, computed once on the blanked code
    ev_pos: list = []
    ev_depth: list = []
    bmatch: dict = {}
    stack: list = []
    depth = 0
    for m in re.finditer(r"[{}]", code):
        if m.group() == "{":
            depth += 1
            stack.append(m.start())
        else:
            depth = max(0, depth - 1)
            if stack:
                bmatch[stack.pop()] = m.start()
        ev_pos.append(m.start())
        ev_depth.append(depth)

    def depth_at(pos: int) -> int:
        k = bisect.bisect_left(ev_pos, pos)
        return ev_depth[k - 1] if k else 0

    def source_matches(pattern):
        # Literal module specifiers are needed, but an import example inside a
        # quoted string must not establish a real binding or wrapper identity.
        return (m for m in pattern.finditer(code_nc) if not code[m.start()].isspace())

    # ---- imports / exports (strings kept, comments removed) ----
    for m in source_matches(RE_IMPORT):
        _clause(m.group(1), m.group(2), fi)
    for m in source_matches(RE_IMPORT_BARE):
        fi.imports.append(Imp("", m.group(1), None))
    for m in source_matches(RE_DYNAMIC):
        fi.imports.append(Imp("", m.group(1), None))
    for m in source_matches(RE_REQUIRE):
        target, spec = m.group(1), m.group(2)
        if target and target.startswith("{"):
            for part in target.strip("{} ").split(","):
                name, _, alias = part.strip().partition(":")
                if name.strip():
                    fi.imports.append(Imp((alias or name).strip(), spec, name.strip()))
        else:
            fi.imports.append(Imp(target or "", spec, None))
    for m in source_matches(RE_EXPORT_FROM):
        what, spec = m.group(1), m.group(2)
        if what.startswith("*"):
            alias = re.search(r"as\s+(" + _ID + ")", what)
            if alias:
                fi.imports.append(Imp(alias.group(1), spec, None))
            else:
                fi.reexports.append(("*", spec, "*"))
        else:
            for part in what.strip("{} \n").split(","):
                name, _, alias = re.sub(r"^type\s+", "", part.strip()).partition(" as ")
                if name.strip():
                    fi.reexports.append(((alias or name).strip(), spec, name.strip()))
    exported_names = set()
    for m in RE_EXPORT_LIST.finditer(code):
        for part in m.group(1).split(","):
            name, _, alias = part.strip().partition(" as ")
            if name.strip():
                exported_names.add(name.strip())
                if alias.strip() == "default":
                    fi.default_export = name.strip()
    for m in RE_EXPORT_DEFAULT.finditer(code):
        if m.group(1) not in ("function", "class", "async"):
            fi.default_export = m.group(1)
    for m in RE_MODULE_EXPORTS.finditer(code):
        target = m.group(1)
        if target.startswith("{"):
            exported_names.update(p.strip().split(":")[0].strip() for p in target.strip("{} ").split(","))
        else:
            fi.default_export = target
            exported_names.add(target)

    # ---- definitions ----
    claimed: set = set()

    def make(kind, name, start, body_start, body_end, parent=None, head=""):
        signature = text[start:body_start].rstrip()
        if signature.endswith("{"):
            signature = signature[:-1].rstrip()
        if signature.endswith("=>"):
            signature = signature[:-2].rstrip()
        signature = " ".join(signature.split())
        if len(signature) > 160:
            signature = signature[:157] + "..."
        qual = f"{parent.name}.{name}" if parent else name
        doc, doc_at = _jsdoc(text, start)
        first = doc_at if doc_at is not None else start     # the doc comment belongs to the symbol
        segment = "\n".join(ln.rstrip() for ln in text[first:body_end + 1].splitlines())
        if parent:
            exported = not name.startswith("_")
        else:
            exported = bool(re.match(r"\s*export\b", head)) or "exports." in head or name in exported_names
        sym = Sym(
            id=sym_id(kind, path, qual),
            kind=kind,
            name=name,
            qual=qual,
            file=path,
            line=line_of(first),
            end=line_of(body_end),
            signature=signature,
            doc=doc,
            parent=parent.id if parent else None,
            exported=exported,
            hash=sha1(segment),
        )
        if kind != "class":
            sym.calls = [(chain, line_of(pos)) for chain, pos in _calls_in(code, body_start, body_end + 1, jsx).items()]
        if parent is None and re.search(r"\bexport\s+default\b", head):
            fi.default_export = name
        return sym

    classes = []
    for m in RE_CLASS.finditer(code):
        if depth_at(m.start()) != 0:
            continue
        open_at = m.end() - 1
        close_at = bmatch.get(open_at)
        name = m.group(1) or ("default" if re.search(r"\bdefault\b", m.group(0)) else None)
        if close_at is None or not name:
            continue
        cls = make("class", name, m.start(), open_at, close_at, head=m.group(0))
        if m.group(2):
            cls.bases = [m.group(2)]
        claimed.add(open_at)
        classes.append((cls, open_at, close_at))

    methods = []
    for cls, open_at, close_at in classes:
        inner = depth_at(open_at + 1)
        names = []
        for mm in RE_METHOD.finditer(code, open_at + 1, close_at):
            name = mm.group(2)          # private `#name` stays distinct from `name`
            if depth_at(mm.start(2)) != inner or name in NOT_METHOD:
                continue
            q = _match(code, mm.end() - 1, "(", ")")
            if q < 0:
                continue
            a = _after_params(code, q + 1)
            if a < len(code) and code[a] == "{" and a in bmatch and a not in claimed:
                claimed.add(a)
                start = mm.start(1) if mm.group(1) else mm.start(2)
                methods.append(make("method", name, start, a, bmatch[a], parent=cls))
                names.append(name)
        for mm in RE_FIELD.finditer(code, open_at + 1, close_at):
            name = mm.group(1)
            if depth_at(mm.start(1)) != inner:
                continue
            body = _fn_body(code, mm.end(), bmatch)
            if body and body[0] not in claimed:
                claimed.add(body[0])
                methods.append(make("method", name, mm.start(1), body[0], body[1], parent=cls))
                names.append(name)
        cls.hash = sha1(f"{cls.signature}|{cls.doc}|{','.join(sorted(names))}")
        # attribute types: `this.x = new Foo()`, TS `constructor(private x: Foo)`, `this.x = x` with `x: Foo`
        body = code[open_at:close_at]
        for m in RE_THIS_NEW.finditer(body):
            cls.attrs.setdefault(m.group(1), tuple(m.group(2).split(".")))
        ctor = next((s for s in methods if s.parent == cls.id and s.name == "constructor"), None)
        if ctor:
            for m in RE_PARAM_PROP.finditer(ctor.signature):
                cls.attrs.setdefault(m.group(1), tuple(m.group(2).split(".")))
            params = {m.group(1): tuple(m.group(2).split(".")) for m in RE_PARAM_TYPE.finditer(ctor.signature)}
            for m in RE_THIS_ASSIGN.finditer(body):
                if m.group(2) in params:
                    cls.attrs.setdefault(m.group(1), params[m.group(2)])

    functions = []
    # React's wrapper call owns the component, rather than the anonymous callback
    # passed to it. Require a real React import instead of guessing arbitrary HOCs.
    react_wrappers = set()
    for imp in fi.imports:
        if imp.module == "react":
            if imp.name in (None, "default"):
                react_wrappers.add(imp.alias + ".forwardRef")
            elif imp.name == "forwardRef":
                react_wrappers.add(imp.alias)
    for m in RE_WRAPPED_BINDING.finditer(code):
        if depth_at(m.start()) != 0 or m.group(2) not in react_wrappers:
            continue
        pos = m.end()
        if code[pos:pos + 1] == "<":
            close = _type_arguments_end(code, pos)
            if close < 0:
                continue
            pos = close + 1
        while pos < len(code) and code[pos].isspace():
            pos += 1
        if code[pos:pos + 1] != "(":
            continue
        end = _match(code, pos, "(", ")")
        callback = pos + 1
        while callback < len(code) and code[callback].isspace():
            callback += 1
        body = _fn_body(code, callback, bmatch)
        if end < 0 or not body or body[0] in claimed:
            continue
        claimed.add(body[0])
        functions.append(make("function", m.group(1), m.start(), body[0], end, head=m.group(0)))
    for m in RE_BINDING.finditer(code):
        if depth_at(m.start()) != 0:
            continue
        body = _fn_body(code, m.end(), bmatch)
        if not body or body[0] in claimed:
            continue
        claimed.add(body[0])
        functions.append(make("function", m.group(1) or m.group(2), m.start(), body[0], body[1], head=m.group(0)))
    for m in RE_FUNC.finditer(code):
        if depth_at(m.start()) != 0:
            continue
        name = m.group(1) or ("default" if re.search(r"\bdefault\b", m.group(0)) else None)
        q = _match(code, m.end() - 1, "(", ")")
        if not name or q < 0:
            continue
        a = _after_params(code, q + 1)
        if a < len(code) and code[a] == "{" and a in bmatch and a not in claimed:
            claimed.add(a)
            functions.append(make("function", name, m.start(), a, bmatch[a], head=m.group(0)))

    top_level = sorted([c for c, _, _ in classes] + functions, key=lambda s: s.line)
    by_class = {c.id: [] for c, _, _ in classes}
    for meth in methods:
        by_class[meth.parent].append(meth)
    for sym in top_level:
        fi.symbols.append(sym)
        if sym.kind == "class":
            fi.symbols.extend(sorted(by_class[sym.id], key=lambda s: s.line))

    # ---- calls made at import time (entry-point hints) ----
    spans = sorted((line, end) for line, end in ((s.line, s.end) for s in top_level))
    starts = [a for a, _ in spans]

    def inside(line: int) -> bool:
        k = bisect.bisect_right(starts, line) - 1
        return k >= 0 and spans[k][0] <= line <= spans[k][1]

    for chain, pos in _calls_in(code, 0, len(code), jsx).items():
        if not inside(line_of(pos)):
            fi.main_calls.append(chain)
    return dedupe_ids(fi)
