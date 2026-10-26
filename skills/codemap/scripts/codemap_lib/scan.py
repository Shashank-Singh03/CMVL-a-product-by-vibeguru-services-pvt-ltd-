"""Find files, decide what each one is, and hand it to the right parser."""
from __future__ import annotations

import os
import re
from pathlib import Path

from .formats import classify, worth_mapping
from .model import FileInfo
from .parse_config import parse_config
from .parse_generic import SPECS, parse_generic
from .parse_js import parse_js
from .parse_python import parse_python
from .util import read_text, sha1

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "bower_components", ".venv", "venv", "env", "__pycache__",
    "dist", "build", "out", "target", ".next", ".nuxt", ".svelte-kit", ".turbo", ".cache", ".parcel-cache",
    ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages", "vendor", "third_party",
    "coverage", "htmlcov", ".idea", ".vscode", ".codemap", ".terraform", ".gradle", "Pods", "DerivedData",
    "obj", "bin", ".dart_tool", "_build", "deps",
}
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "e2e", "testing"}
MAX_BYTES = 600_000
SCRIPT_BLOCK = re.compile(r"(<script\b[^>]*>)([\s\S]*?)</script>", re.I)


def language_of(path: str) -> str | None:
    """The format of a path, or None for files codemap never maps (lockfiles, minified bundles)."""
    return classify(path)[0]


def in_skipped_dir(path: str) -> bool:
    return any(seg in SKIP_DIRS or seg.endswith(".egg-info") for seg in path.split("/")[:-1])


def is_test(path: str) -> bool:
    parts = path.lower().split("/")
    name = parts[-1]
    if any(p in TEST_DIRS for p in parts[:-1]):
        return True
    return (name.startswith("test_") or name.endswith(("_test.py", "_tests.py", "_test.go", "test.java", "tests.cs", "_spec.rb"))
            or ".test." in name or ".spec." in name or name == "conftest.py")


def walk_files(root: Path, sub: str = "") -> list:
    """Candidate files under root/sub without git (skips vendored and generated folders)."""
    base = root / sub if sub else root
    found = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")
                             and (not d.startswith(".") or d in (".github", ".circleci", ".gitlab")))
        for name in sorted(filenames):
            rel = Path(dirpath, name).relative_to(root).as_posix()
            if language_of(rel):
                found.append(rel)
    return found


def keep(path: str, include_tests: bool) -> bool:
    return bool(language_of(path)) and not in_skipped_dir(path) and (include_tests or not is_test(path))


def looks_generated(text: str) -> bool:
    lines = text.count("\n") + 1
    return len(text) / lines > 300 or "@generated" in text[:2000] or "DO NOT EDIT" in text[:2000]


def _script_only(text: str) -> str:
    """Keep only <script> blocks of a Vue/Svelte/Astro component; everything else becomes blank lines."""
    out = []
    pos = 0
    for m in SCRIPT_BLOCK.finditer(text):
        out.append(re.sub(r"[^\n]", " ", text[pos:m.start(2)]))
        out.append(m.group(2))
        pos = m.end(2)
    out.append(re.sub(r"[^\n]", " ", text[pos:]))
    body = "".join(out)
    if text.lstrip().startswith("---"):                      # Astro frontmatter script
        fm = re.match(r"\s*---\n([\s\S]*?)\n---", text)
        if fm:
            body = fm.group(0).replace("---", "   ") + body[len(fm.group(0)):]
    return body


def parse_source(path: str, text: str, fmt: str | None = None, domain: str | None = None) -> FileInfo:
    if fmt is None:
        fmt, domain = classify(path, text[:4000])
    if fmt == "python":
        fi = parse_python(path, text)
    elif fmt in ("javascript", "typescript"):
        fi = parse_js(path, text)
    elif fmt in ("vue", "svelte", "astro"):
        fi = parse_js(path, _script_only(text))
        fi.lang = fmt
    elif fmt in SPECS:
        fi = parse_generic(path, text, fmt)
    else:
        fi = parse_config(path, text, fmt or "text")
    fi.lang = fmt or "text"
    fi.domain = domain or "other"
    fi.source_hash = sha1(text)
    return fi


def parse_files(root: Path, paths: list, changed: set | None = None, diff_mode: bool = False,
                audit: bool = False) -> tuple:
    """Parse every relevant path; returns ({path: FileInfo}, {path: source text}).

    Code, infra, API, data, CI and dependency files are always parsed (they make the graph);
    docs, generic config and unknown text files only when they changed.
    """
    files, texts = {}, {}
    changed = changed or set()
    for path in paths:
        full = root / path
        try:
            if full.stat().st_size > MAX_BYTES:
                continue
            raw = full.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:8192]:
            continue
        text = read_text(full)
        fmt, domain = classify(path, text[:4000])
        is_changed = path in changed
        if not audit and not worth_mapping(fmt, domain, diff_mode and is_changed):
            continue
        if looks_generated(text) and not is_changed and not audit:
            continue
        files[path] = parse_source(path, text, fmt, domain)
        texts[path] = text
    return files, texts
