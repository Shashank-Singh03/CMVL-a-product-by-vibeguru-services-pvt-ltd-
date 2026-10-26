"""Small helpers shared by every stage. Standard library only."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

_SPLIT = re.compile(r"[_\-\s./]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")
_CODE_EXT = re.compile(r"\.(py|pyw|js|jsx|mjs|cjs|ts|tsx|mts|cts|java|kt|kts|go|rs|rb|php|cs|swift|dart|scala|lua|ex|exs|"
                       r"sh|ps1|r|sql|ya?ml|json|toml|ini|md|txt|tf|tfvars|hcl|bicep|proto|graphql|gql|prisma|ipynb|env|"
                       r"cfg|conf|vue|svelte|c|h|cpp|hpp)$", re.I)
_SPOKEN = {"k8s": "Kubernetes", "README": "readme", "ci": "CI", "api": "API", "db": "database", "tf": "Terraform"}


def setup_stdout() -> None:
    """Never crash on a console that cannot encode a character (Windows cp1252 pipes)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def words(identifier: str) -> list[str]:
    """Split snake_case, camelCase, kebab-case and dotted names into words."""
    out: list[str] = []
    for chunk in _SPLIT.split(identifier):
        out.extend(_CAMEL.findall(chunk))
    return out


def humanize(identifier: str) -> str:
    """`load_user_config` -> `Load user config`, `HTTPServer` -> `HTTP server`."""
    ws = words(identifier)
    if not ws:
        return identifier
    text = " ".join(w if (w.isupper() and len(w) > 1) else w.lower() for w in ws)
    return text[0].upper() + text[1:]


def speakable(name: str) -> str:
    """Make an identifier pleasant for a text-to-speech voice."""
    name = _CODE_EXT.sub("", name.strip().rstrip("()/").rstrip("/"))
    if name in _SPOKEN:
        return _SPOKEN[name]
    spoken = []
    for part in name.split("."):
        if part in _SPOKEN:
            spoken.append(_SPOKEN[part])
            continue
        if part == "__init__":
            spoken.append("constructor")
            continue
        ws = words(part)
        if ws:
            spoken.append(" ".join(ws))
    return " ".join(spoken) or name


def first_sentence(text: str, limit: int = 140) -> str:
    text = " ".join((text or "").strip().split())
    if not text:
        return ""
    m = _SENTENCE_END.search(text)
    if m:
        text = text[: m.start()]
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def est_tokens(text: str) -> int:
    """Rough token estimate for mixed code/English (about 3.6 characters per token)."""
    return max(1, round(len(text) / 3.6))


def human_list(names: list[str], limit: int = 5) -> str:
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) > limit:
        shown = names[:limit]
        return ", ".join(shown) + f" and {len(names) - limit} more"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def read_text(path) -> str:
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def write_text(path, text: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, data, indent: int | None = None) -> None:
    text = json.dumps(data, indent=indent, ensure_ascii=False, separators=None if indent else (",", ":"))
    write_text(path, text + "\n")


def rmtree(path) -> None:
    """shutil.rmtree that also removes read-only files (git objects on Windows)."""
    import os
    import shutil
    import stat

    def force(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=force)
    else:
        shutil.rmtree(path, onerror=force)


def dedent_lines(lines: list[str]) -> list[str]:
    """Remove the common leading whitespace of diff-style lines ("+code", "-code", " code")."""
    bodies = [ln[1:] for ln in lines if ln[1:].strip()]
    if not bodies:
        return lines
    indent = min(len(b) - len(b.lstrip()) for b in bodies)
    if not indent:
        return lines
    return [ln[0] + ln[1 + indent:] if ln[1:].strip() else ln[0] for ln in lines]
