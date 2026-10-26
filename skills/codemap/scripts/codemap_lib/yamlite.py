"""A small YAML/JSON reader that keeps line numbers (standard library only).

It covers the subset that Kubernetes, Compose, GitHub Actions, GitLab CI, OpenAPI and
CloudFormation files use: block mappings and sequences, plain/quoted scalars, flow
collections, block scalars (| and >), comments, several documents (---), tags (!Ref)
and anchors/aliases (kept as text). Every node knows the lines it spans, which is what
lets codemap map a diff hunk onto "the Deployment", "the build job" or "GET /notes".
"""
from __future__ import annotations

import json
import re


class Node:
    __slots__ = ("key", "kind", "value", "children", "line", "end")

    def __init__(self, key=None, kind="scalar", value=None, line=0, end=0):
        self.key = key
        self.kind = kind            # "map" | "seq" | "scalar"
        self.value = value
        self.children: list = []
        self.line = line
        self.end = end

    def get(self, key, default=None):
        if self.kind == "map":
            for c in self.children:
                if c.key == key:
                    return c
        return default

    def at(self, *path):
        node = self
        for p in path:
            if node is None:
                return None
            if isinstance(p, int):
                node = node.children[p] if node.kind == "seq" and p < len(node.children) else None
            else:
                node = node.get(p)
        return node

    def text(self, default: str = "") -> str:
        if self.kind != "scalar" or self.value is None:
            return default
        return str(self.value)

    def items(self) -> list:
        return [(c.key, c) for c in self.children] if self.kind == "map" else []

    def py(self):
        if self.kind == "map":
            return {c.key: c.py() for c in self.children}
        if self.kind == "seq":
            return [c.py() for c in self.children]
        return self.value

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def __repr__(self) -> str:
        return f"Node({self.key!r}, {self.kind}, L{self.line}-{self.end})"


# ---------------------------------------------------------------- scalars

_NUM = re.compile(r"^[-+]?(\d+|\d*\.\d+([eE][-+]?\d+)?)$")


def scalar(text: str):
    t = text.strip()
    if not t:
        return None
    if t[0] in "&" and " " in t:                       # anchor: &name value
        t = t.split(" ", 1)[1].strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        inner = t[1:-1]
        return inner.replace("''", "'") if t[0] == "'" else inner.replace('\\"', '"').replace("\\n", "\n")
    low = t.lower()
    if low in ("true", "yes", "on"):
        return True if low != "on" else "on"
    if low in ("false", "no", "off"):
        return False if low != "off" else "off"
    if low in ("null", "~"):
        return None
    if _NUM.match(t):
        try:
            return int(t) if re.match(r"^[-+]?\d+$", t) else float(t)
        except ValueError:
            return t
    return t


def _flow(text: str, line: int) -> Node:
    """Parse a flow collection like [a, b] or {k: v, x: [1, 2]}."""
    pos = 0

    def parse() -> Node:
        nonlocal pos
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        if pos < len(text) and text[pos] in "[{":
            closing = "]" if text[pos] == "[" else "}"
            node = Node(kind="seq" if closing == "]" else "map", line=line, end=line)
            pos += 1
            while pos < len(text):
                while pos < len(text) and text[pos] in " \t,\n":
                    pos += 1
                if pos < len(text) and text[pos] == closing:
                    pos += 1
                    break
                if node.kind == "seq":
                    node.children.append(parse())
                else:
                    key = _token(":")
                    if pos < len(text) and text[pos] == ":":
                        pos += 1
                    val = parse()
                    val.key = scalar(key) if key else key
                    node.children.append(val)
            return node
        return Node(kind="scalar", value=scalar(_token(",]}")), line=line, end=line)

    def _token(stops: str) -> str:
        nonlocal pos
        start = pos
        quote = None
        while pos < len(text):
            ch = text[pos]
            if quote:
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif ch in stops or (stops == ":" and ch in ",}"):
                break
            pos += 1
        return text[start:pos].strip()

    try:
        return parse()
    except IndexError:
        return Node(kind="scalar", value=text, line=line, end=line)


# ---------------------------------------------------------------- YAML

def _strip_comment(line: str) -> str:
    quote = None
    depth = 0
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'" and (i == 0 or line[i - 1] in " \t:-[{,"):
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def _split_key(content: str):
    """'key: value' -> (key, value); returns None when the line is not a mapping entry."""
    quote = None
    depth = 0
    for i, ch in enumerate(content):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'" and i == 0:
            quote = ch
        elif ch in "[{" and i == 0:
            return None
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == ":" and depth <= 0 and (i + 1 == len(content) or content[i + 1] in " \t"):
            key = content[:i].strip()
            if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
                key = key[1:-1]
            return key, content[i + 1:].strip()
    return None


class _YamlParser:
    def __init__(self, raw_lines: list, first_line: int):
        self.raw = raw_lines
        self.items = []          # (indent, content, lineno, raw index)
        for k, raw in enumerate(raw_lines):
            body = _strip_comment(raw)
            if not body.strip():
                continue
            indent = len(body) - len(body.lstrip(" "))
            self.items.append([indent, body.strip(), first_line + k, k])
        self.i = 0

    def parse(self) -> Node:
        if not self.items:
            return Node(kind="map")
        return self.block(self.items[0][0])

    def block(self, indent: int) -> Node:
        ind, content, _, _ = self.items[self.i]
        if content.startswith("- ") or content == "-":
            return self.seq(ind)
        if _split_key(content) is not None:
            return self.mapping(ind)
        node = self.value(content, self.items[self.i][2], indent)
        self.i += 1
        return node

    def value(self, text: str, line: int, parent_indent: int) -> Node:
        if text.startswith(("[", "{")):
            depth = text.count("[") + text.count("{") - text.count("]") - text.count("}")
            end = line
            while depth > 0 and self.i + 1 < len(self.items):
                self.i += 1
                more = self.items[self.i][1]
                text += " " + more
                end = self.items[self.i][2]
                depth = text.count("[") + text.count("{") - text.count("]") - text.count("}")
            node = _flow(text, line)
            node.end = end
            return node
        return Node(kind="scalar", value=scalar(text), line=line, end=line)

    def block_scalar(self, indent: int, line: int) -> Node:
        raw_index = self.items[self.i][3]
        texts = []
        last = line
        j = self.i + 1
        while j < len(self.items) and self.items[j][0] > indent:
            last = self.items[j][2]
            j += 1
        for k in range(raw_index + 1, raw_index + 1 + (last - line)):
            if k < len(self.raw):
                texts.append(self.raw[k].strip())
        self.i = j - 1
        return Node(kind="scalar", value="\n".join(texts), line=line, end=last)

    def mapping(self, indent: int) -> Node:
        node = Node(kind="map", line=self.items[self.i][2])
        while self.i < len(self.items):
            ind, content, line, _ = self.items[self.i]
            if ind != indent or content.startswith("- ") or content == "-":
                break
            kv = _split_key(content)
            if kv is None:
                break
            key, rest = kv
            if rest[:1] in ("|", ">") and re.match(r"^[|>][-+0-9]*$", rest):
                child = self.block_scalar(ind, line)
                self.i += 1
            elif rest and not re.match(r"^[&!]\S*$", rest):
                if rest.startswith("&"):                     # anchor before an inline value
                    rest = rest.split(" ", 1)[1]
                child = self.value(rest, line, ind)          # tags such as `!Ref Bucket` stay in the text
                self.i += 1
            else:
                self.i += 1
                if self.i < len(self.items) and (self.items[self.i][0] > ind or
                                                 (self.items[self.i][0] == ind and self.items[self.i][1].startswith("- "))):
                    child = self.block(self.items[self.i][0])
                else:
                    child = Node(kind="scalar", value=scalar(rest) if rest else None, line=line, end=line)
            child.key = key
            child.line = line
            child.end = max(child.end, line, self._last(child))
            node.children.append(child)
        node.end = max([c.end for c in node.children] + [node.line])
        return node

    def seq(self, indent: int) -> Node:
        node = Node(kind="seq", line=self.items[self.i][2])
        while self.i < len(self.items):
            ind, content, line, raw_k = self.items[self.i]
            if ind != indent or not (content.startswith("- ") or content == "-"):
                break
            rest = content[1:].strip()
            if not rest:
                self.i += 1
                if self.i < len(self.items) and self.items[self.i][0] > ind:
                    child = self.block(self.items[self.i][0])
                else:
                    child = Node(kind="scalar", value=None, line=line, end=line)
            else:
                col = ind + (len(content) - len(rest))
                self.items[self.i] = [col, rest, line, raw_k]      # "- key: v" starts a mapping at the item's column
                child = self.block(col)
            child.line = line
            child.end = max(child.end, line, self._last(child))
            node.children.append(child)
        node.end = max([c.end for c in node.children] + [node.line])
        return node

    @staticmethod
    def _last(node: Node) -> int:
        return max([node.end] + [_YamlParser._last(c) for c in node.children])


def parse_yaml(text: str) -> list:
    """All documents in a YAML text, each a Node (map/seq/scalar) with line numbers (1-based)."""
    lines = text.splitlines()
    docs, start = [], 0
    for k, line in enumerate(lines + ["---"]):
        if line.startswith("---") or line.startswith("..."):
            chunk = lines[start:k]
            if any(_strip_comment(ln).strip() for ln in chunk):
                try:
                    docs.append(_YamlParser(chunk, start + 1).parse())
                except (IndexError, RecursionError):
                    pass
            start = k + 1
    return docs


# ---------------------------------------------------------------- JSON

def parse_json(text: str):
    """JSON into the same Node tree, with line numbers. Returns None on invalid JSON."""
    try:
        json.loads(text)
    except ValueError:
        return None
    pos = 0
    line = 1
    n = len(text)

    def skip():
        nonlocal pos, line
        while pos < n and text[pos] in " \t\r\n":
            if text[pos] == "\n":
                line += 1
            pos += 1

    def string() -> str:
        nonlocal pos
        end = pos + 1
        while text[end] != '"':
            end += 2 if text[end] == "\\" else 1
        raw = text[pos:end + 1]
        pos = end + 1
        return json.loads(raw)

    def value() -> Node:
        nonlocal pos
        skip()
        start_line = line
        ch = text[pos]
        if ch == "{":
            node = Node(kind="map", line=start_line)
            pos += 1
            skip()
            if text[pos] == "}":
                pos += 1
            else:
                while True:
                    skip()
                    key_line = line
                    key = string()
                    skip()
                    pos += 1            # ':'
                    child = value()
                    child.key = key
                    child.line = key_line
                    node.children.append(child)
                    skip()
                    if text[pos] == ",":
                        pos += 1
                        continue
                    pos += 1            # '}'
                    break
            node.end = line
            return node
        if ch == "[":
            node = Node(kind="seq", line=start_line)
            pos += 1
            skip()
            if text[pos] == "]":
                pos += 1
            else:
                while True:
                    node.children.append(value())
                    skip()
                    if text[pos] == ",":
                        pos += 1
                        continue
                    pos += 1
                    break
            node.end = line
            return node
        if ch == '"':
            return Node(kind="scalar", value=string(), line=start_line, end=start_line)
        m = re.compile(r"-?\d+(\.\d+)?([eE][-+]?\d+)?|true|false|null").match(text, pos)
        tok = m.group() if m else text[pos]
        pos += len(tok)
        return Node(kind="scalar", value=json.loads(tok) if m else tok, line=start_line, end=start_line)

    try:
        return value()
    except (IndexError, ValueError):
        return None


def parse_any(text: str, is_json: bool) -> list:
    if is_json:
        node = parse_json(text)
        return [node] if node is not None else []
    return parse_yaml(text)
