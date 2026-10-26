"""Small, conservative tsconfig/jsconfig path resolver; no TypeScript dependency.

Only repository-local configuration and mapped source files are consulted. This
does not emulate package exports, project references or compiler resolution modes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

MAX_CONFIG_BYTES = 600_000


def _jsonc(text: str) -> dict:
    """Read JSON with comments/trailing commas without changing quoted strings."""
    chars = list(text.lstrip("\ufeff"))
    i, quoted = 0, False
    while i < len(chars):
        char = chars[i]
        if quoted:
            if char == "\\":
                i += 2
                continue
            if char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "/" and i + 1 < len(chars) and chars[i + 1] in ("/", "*"):
            line = chars[i + 1] == "/"
            end = i + 2 if line else i + 3
            while end < len(chars) and not (chars[end] == "\n" if line else
                                           chars[end - 1] == "*" and chars[end] == "/"):
                end += 1
            if not line and end == len(chars):
                raise ValueError("unterminated config comment")
            end = end if line else end + 1
            chars[i:end] = ["\n" if c == "\n" else " " for c in chars[i:end]]
            i = end
            continue
        i += 1
    i, quoted = 0, False
    while i < len(chars):
        char = chars[i]
        if quoted:
            if char == "\\":
                i += 2
                continue
            if char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == ",":
            end = i + 1
            while end < len(chars) and chars[end].isspace():
                end += 1
            if end < len(chars) and chars[end] in ("}", "]"):
                chars[i] = " "
        i += 1

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate config key")
            result[key] = value
        return result
    value = json.loads("".join(chars), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("config must be an object")
    return value


@dataclass
class Config:
    base_url: Path | None = None
    paths: dict | None = None
    paths_dir: Path | None = None
    valid: bool = True


class TSConfigResolver:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.loaded: dict = {}
        self.nearest: dict = {}

    def _inside(self, path: Path) -> bool:
        try:
            path.relative_to(self.root)
            return True
        except ValueError:
            return False

    def _path(self, origin: Path, value: str) -> Path:
        if "${" in value:
            raise ValueError("config interpolation is unsupported")
        target = (origin / value.replace("\\", "/")).resolve()
        if not self._inside(target):
            raise ValueError("config path leaves repository")
        return target

    def _load(self, path: Path, stack=()) -> Config:
        if path in self.loaded:
            return self.loaded[path]
        if path in stack or len(stack) >= 16 or not self._inside(path):
            return Config(valid=False)
        try:
            if path.stat().st_size > MAX_CONFIG_BYTES:
                raise ValueError("config too large")
            data = _jsonc(path.read_text(encoding="utf-8-sig"))
            result = Config()
            if "extends" in data:
                base = data["extends"]
                if not isinstance(base, str) or not base.startswith(("./", "../", ".\\", "..\\")):
                    raise ValueError("only relative file extends is supported")
                parent = self._path(path.parent, base)
                if parent.suffix != ".json":
                    parent = Path(str(parent) + ".json")
                inherited = self._load(parent, stack + (path,))
                if not inherited.valid:
                    raise ValueError("invalid extended config")
                result = Config(inherited.base_url, inherited.paths, inherited.paths_dir)
            options = data.get("compilerOptions", {})
            if not isinstance(options, dict):
                raise ValueError("compilerOptions must be an object")
            if "baseUrl" in options:
                if not isinstance(options["baseUrl"], str):
                    raise ValueError("baseUrl must be a string")
                result.base_url = self._path(path.parent, options["baseUrl"])
            if "paths" in options:
                mappings = options["paths"]
                if not isinstance(mappings, dict):
                    raise ValueError("paths must be an object")
                for pattern, targets in mappings.items():
                    if pattern.count("*") > 1 or not isinstance(targets, list) or not targets:
                        raise ValueError("unsupported paths pattern")
                    if any(not isinstance(t, str) or t.count("*") > pattern.count("*") or "${" in t
                           for t in targets):
                        raise ValueError("unsupported paths target")
                result.paths, result.paths_dir = mappings, path.parent
        except (OSError, UnicodeError, ValueError, RecursionError):
            result = Config(valid=False)
        self.loaded[path] = result
        return result

    def _config(self, importer: str) -> Config | None:
        folder = (self.root / importer).parent.resolve()
        if folder in self.nearest:
            return self.nearest[folder]
        here, result = folder, None
        while self._inside(here):
            path = next((here / name for name in ("tsconfig.json", "jsconfig.json")
                         if (here / name).is_file()), None)
            if path is not None:
                result = self._load(path)
                break
            if here == self.root:
                break
            here = here.parent
        self.nearest[folder] = result
        return result

    def resolve(self, importer: str, spec: str, find) -> tuple:
        """(handled, target); `find` distinguishes ambiguity from a missing file."""
        config = self._config(importer)
        if config is None:
            return False, None
        if not config.valid:
            return True, None
        patterns = config.paths or {}
        matches = []
        for pattern in patterns:
            if "*" not in pattern:
                if pattern == spec:
                    matches = [(pattern, "")]
                    break
            else:
                before, after = pattern.split("*")
                if spec.startswith(before) and spec.endswith(after) and len(spec) >= len(before) + len(after):
                    matches.append((pattern, spec[len(before):len(spec) - len(after) if after else None]))
        if matches:
            longest = max(len(p.split("*", 1)[0]) for p, _ in matches)
            best = [(p, captured) for p, captured in matches if len(p.split("*", 1)[0]) == longest]
            if len(best) != 1:
                return True, None
            pattern, captured = best[0]
            origin = config.base_url or config.paths_dir
            for target in patterns[pattern]:
                try:
                    base = self._path(origin, target.replace("*", captured)).relative_to(self.root).as_posix()
                except ValueError:
                    return True, None
                handled, found = find(base)
                if handled:
                    return True, found
            if config.base_url is None:
                return True, None
        if config.base_url is not None:
            try:
                base = self._path(config.base_url, spec).relative_to(self.root).as_posix()
            except ValueError:
                return True, None
            return True, find(base)[1]
        return False, None
