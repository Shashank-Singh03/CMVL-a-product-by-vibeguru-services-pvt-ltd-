"""Redact recognized credentials before analysis data leaves the in-memory pipeline.

Parsing and change detection still use original source. One redactor learns literal
values from current/previous source, then removes those values wherever they occur
in a map, label, worksheet or narration. This is deliberately separate from whether
a risk finding is suppressed or has already been truncated for display.
"""
from __future__ import annotations

import re

REDACTED = "[REDACTED]"
SENSITIVE_KEY = r"(?:[A-Za-z_][\w-]*[_-])?(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|client[_-]?secret|(?:private|secret)[_-]?key|token)"
_KEY = SENSITIVE_KEY
_LITERAL = re.compile(
    r"(?i)\b" + _KEY + r"\b[\"']?\s*[:=]\s*(?P<quote>[\"'`])"
    r"(?P<value>(?:\\.|(?!(?P=quote))[^\r\n])*)(?P=quote)")
_OPEN_LITERAL = re.compile(
    r"(?i)\b" + _KEY + r"\b[\"']?\s*[:=]\s*(?P<quote>[\"'`])"
    r"(?P<value>(?:\\.|(?!(?P=quote))[^\r\n])*)$")
_ENV = re.compile(r"(?im)^\s*(?:export\s+)?" + _KEY + r"\s*=\s*([^\s\"'`#]+)")
CONFIG_LITERAL_PATTERN = (r"(?im)^\s*(?:-\s*)?(?:export\s+)?[\"']?" + _KEY +
                          r"[\"']?\s*[:=]\s*(?![\"'`])(?P<value>[^\s\"'`#;,{}\[\]]{6,})")
_CONFIG_LITERAL = re.compile(CONFIG_LITERAL_PATTERN)
_CONFIG_VALUE = re.compile(r"(?im)^\s*(?:-\s*)?(?:export\s+)?[\"']?" + _KEY +
                           r"[\"']?\s*[:=][ \t]*(?P<value>[^\r\n]+)")
_TOKENS = [re.compile(p) for p in (
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    r"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
    r"\bxox[baprs]-[A-Za-z0-9-]{10,}",
    r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}",
    r"\bAIza[0-9A-Za-z_-]{35}\b",
    r"\b(?:sk|rk)_live_[0-9a-zA-Z]{20,}",
    r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
)]
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?P<kind>(?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY)-----"
    r"[\s\S]*?(?:-----END (?P=kind)-----|\Z)")
_PRIVATE_BEGIN = re.compile(r"-----BEGIN (?P<kind>(?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY)-----")
_PLACEHOLDER = re.compile(
    r"(?ix)^(?:example|dummy|changeme|change_me|your[_-].*|<[^>]+>|\$\{.*|\{\{.*|"
    r"x{3,}|placeholder|redacted|\[redacted\]|\*{3,}|fake|test[_-]?key|sample|"
    r"(?:example|dummy|fake|sample|placeholder|test[_-]?key)[_-].*)$")
_REFERENCE = re.compile(r"(?i)^(?:os\.environ|process\.env|getenv\b|secrets\.|vault\b|!(?:Ref|Sub|GetAtt)\b)")


def is_placeholder(value: str) -> bool:
    """Check the matched value, never an unrelated comment elsewhere on its line."""
    return not value or bool(_PLACEHOLDER.fullmatch(value.strip()) or _REFERENCE.match(value.strip()))


def secret_value(match) -> str:
    """Extract the literal from the risk scanner's assignment-shaped match."""
    literal = _LITERAL.search(match.group(0))
    bare = _CONFIG_LITERAL.search(match.group(0)) if literal is None else None
    return literal.group("value") if literal else (bare.group("value") if bare else match.group(0))


class SecretRedactor:
    def __init__(self):
        self.values: set[str] = set()
        self._replacement = None

    def add_text(self, text: str, env: bool = False, config: bool = False) -> None:
        found = {m.group("value") for m in _LITERAL.finditer(text)
                 if len(m.group("value")) >= 6 and not is_placeholder(m.group("value"))}
        for pattern in _TOKENS:
            found.update(m.group(0) for m in pattern.finditer(text))
        for match in _PRIVATE_KEY.finditer(text):
            found.add(match.group(0))
            # Diffs/signatures may split a key across separate strings or lines.
            found.update(line.strip() for line in match.group(0).splitlines()
                         if line.strip() and not line.strip().startswith("-----"))
        if env:
            found.update(m.group(1) for m in _ENV.finditer(text)
                         if len(m.group(1)) >= 6 and not is_placeholder(m.group(1)))
        if env or config:
            found.update(m.group("value") for m in _CONFIG_LITERAL.finditer(text)
                         if not is_placeholder(m.group("value")))
            for match in _CONFIG_VALUE.finditer(text):
                value = re.split(r"\s+#", match.group("value"), maxsplit=1)[0].strip()
                if len(value) >= 6 and value[:1] not in "\"'`|>{[" and not is_placeholder(value):
                    found.add(value)
        if found - self.values:
            self.values.update(found)
            self._replacement = None

    def add_lines(self, lines, env: bool = False, config: bool = False) -> None:
        """Learn a large source file without dropping multiline private-key context."""
        private_kind = None
        for line in lines:
            self.add_text(line, env=env, config=config)
            begin = _PRIVATE_BEGIN.search(line)
            if begin:
                private_kind = begin.group("kind")
            if private_kind is not None:
                value = line.strip()
                if value and "-----BEGIN " not in value and "-----END " not in value and value not in self.values:
                    self.values.add(value)
                    self._replacement = None
                if "-----END " + private_kind + "-----" in line:
                    private_kind = None

    def add_object(self, value) -> None:
        if isinstance(value, str):
            self.add_text(value)
        elif isinstance(value, dict):
            for item in value.values():
                self.add_object(item)
        elif isinstance(value, (list, tuple)):
            if all(isinstance(item, str) for item in value) and any("-----BEGIN " in item for item in value):
                self.add_text("\n".join(item[1:] if item[:1] in "+- " and not item.startswith("-----") else item
                                        for item in value))
            for item in value:
                self.add_object(item)

    def text(self, text: str) -> str:
        if self.values:
            if self._replacement is None:
                self._replacement = re.compile("|".join(re.escape(v) for v in sorted(self.values, key=lambda v: (-len(v), v))))
            text = self._replacement.sub(lambda _m: REDACTED, text)
            # A parser may have shortened a signature/docstring in the middle of
            # a credential. Remove recognizable prefixes as well as whole values.
            for value in sorted(self.values, key=lambda v: (-len(v), v)):
                if len(value) < 12:
                    continue
                start = text.find(value[:12])
                while start >= 0:
                    length = 12
                    while length < len(value) and start + length < len(text) and text[start + length] == value[length]:
                        length += 1
                    text = text[:start] + REDACTED + text[start + length:]
                    start = text.find(value[:12], start + len(REDACTED))
        for pattern in _TOKENS:
            text = pattern.sub(lambda _m: REDACTED, text)
        text = _PRIVATE_KEY.sub(lambda _m: REDACTED, text)
        for pattern in (_LITERAL, _OPEN_LITERAL):
            text = pattern.sub(lambda m: m.group(0) if is_placeholder(m.group("value")) else
                               m.group(0)[:m.start("value") - m.start()] + REDACTED +
                               m.group(0)[m.end("value") - m.start():], text)
        return text

    def clean(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: self.clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.clean(v) for v in value]
        return value

    def clean_review(self, value):
        """Redact review prose/evidence without corrupting protocol IDs or enums."""
        protocol = {"version", "analysis_id", "id", "from", "to", "parent", "lexical_parent", "kind", "status",
                    "op", "certainty", "confidence", "hash", "sha256", "language", "lang", "source_backed_only"}
        if isinstance(value, dict):
            return {key: item if key in protocol else self.clean_review(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.clean_review(item) for item in value]
        return self.text(value) if isinstance(value, str) else value

    def clean_ir(self, ir: dict) -> dict:
        """Clean public text, keeping schema keys, enums, hashes and graph IDs intact.

        A password can itself be 'children', 'root' or 'function'. Replacing every
        string in JSON would then break the schema or the graph's references.
        """
        result = dict(ir)
        if "artifacts" in ir:
            result["artifacts"] = dict(ir["artifacts"])
            source_base = result["artifacts"].get("source_base")
            if isinstance(source_base, str) and self.text(source_base) != source_base:
                # A relative directory can coincidentally contain a credential.
                # Omit this optional handoff rather than publish or corrupt it.
                result["artifacts"].pop("source_base", None)
        for field in ("repo", "commits"):
            if field in ir:
                result[field] = self.clean(ir[field])
        result["scope"] = dict(ir.get("scope", {}))
        for field in ("label", "reason"):
            if field in result["scope"]:
                result["scope"][field] = self.clean(result["scope"][field])
        result["stats"] = dict(ir.get("stats", {}))
        if "parse_errors" in result["stats"]:
            result["stats"]["parse_errors"] = self.clean(result["stats"]["parse_errors"])
        if "coverage" in result["stats"]:
            result["stats"]["coverage"] = self.clean(result["stats"]["coverage"])
        if "review" in ir:
            result["review"] = self.clean_review(ir["review"])
        result["nodes"] = {}
        for nid, node in ir.get("nodes", {}).items():
            cleaned = dict(node)
            for field in ("name", "qual", "file", "path", "sig", "doc", "sum", "title", "sub", "diff", "bases", "routes",
                          "provenance", "review_links"):
                if field in node:
                    cleaned[field] = (self.clean_review(node[field]) if field in ("provenance", "review_links")
                                      else self.clean(node[field]))
            for field in ("calls", "callers", "hidden"):
                if field in node:
                    cleaned[field] = [[item[0], self.text(item[1])] for item in node[field]]
            result["nodes"][nid] = cleaned
        for field, text_fields in (("views", ("caption",)), ("why", ("text",)),
                                   ("risks", ("msg", "text", "note", "file")),
                                   ("other_files", ("path", "note")),
                                   ("plan", ("say", "text")), ("beats", ("say", "text"))):
            if field in ir:
                result[field] = [dict(item, **{key: self.clean(item[key]) for key in text_fields if key in item})
                                 for item in ir[field]]
        return result
