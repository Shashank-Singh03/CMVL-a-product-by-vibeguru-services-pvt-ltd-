#!/usr/bin/env python3
"""Build a reproducible, installable CMVL ZIP from an approved committed Git tree.

Untracked files are never read. Private/generated paths are rejected even if they
were accidentally forced into Git. The script does not run the captured projects.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = "skills/codemap/scripts/codemap_lib/__init__.py"
REQUIRED = {
    "install.py", "README.md", "CHANGELOG.md", "DESIGN.md", VERSION_FILE,
    "skills/codemap/SKILL.md", "skills/codemap/scripts/codemap.py",
    "skills/codemap/assets/viewer.html", "skills/codemap/assets/studio.css",
    "skills/codemap/assets/Manrope-OFL.txt",
    "skills/codemap/scripts/codemap_lib/assets/history.bundle.js",
    "skills/codemap/scripts/codemap_lib/assets/history.css",
    "dashboard/package.json", "dashboard/package-lock.json",
}
ROOT_FILES = {"README.md", "CHANGELOG.md", "DESIGN.md", "install.py", ".gitignore", ".gitattributes",
              "LICENSE", "LICENSE.md", "NOTICE", "THIRD_PARTY_NOTICES.md", "SECURITY.md"}
PUBLIC_ROOTS = {"skills", "dashboard", "docs", "examples", "scripts", "tests", ".github"}
PRIVATE_PARTS = {".git", ".codemap", "node_modules", "__pycache__", ".venv", "venv", "env",
                 ".claude", ".agents", ".gemini", ".cursor", ".codex", ".aws", ".pytest_cache",
                 ".mypy_cache", ".ruff_cache", ".vite", "test-results", "playwright-report"}


class ReleaseError(ValueError):
    pass


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise ReleaseError("Git could not read the committed release tree: " +
                           result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def approved_path(name: str) -> bool:
    path = PurePosixPath(name)
    if (not name or "\\" in name or ":" in name or path.is_absolute()
            or any(part in ("", ".", "..") for part in name.split("/"))
            or any(ord(char) < 32 for char in name)):
        raise ReleaseError("Unsafe path in release tree")
    parts = path.parts
    if (any(part in PRIVATE_PARTS for part in parts)
            or any(part == ".env" or part.startswith(".env.") for part in parts)
            or name.endswith((".pyc", ".pyo", ".local", ".log", ".tmp"))):
        raise ReleaseError("Private/generated path is tracked; remove it before releasing: " + name)
    if name in ROOT_FILES:
        return True
    if parts[0] == "benchmarks":
        return (len(parts) == 2 and (parts[1] in ("README.md", "baseline.json") or parts[1].endswith(".py"))
                or len(parts) >= 3 and parts[1] == "cases")
    if parts[0] not in PUBLIC_ROOTS:
        return False
    if parts[0] == "skills":
        return len(parts) >= 3 and parts[1] == "codemap"
    if parts[0] == "examples":
        return len(parts) >= 3 and parts[1] == "demo-output"
    if "dist" in parts or "build" in parts:
        raise ReleaseError("Build output is tracked; remove it before releasing: " + name)
    return True


def version_from_source(source: bytes) -> str:
    try:
        tree = ast.parse(source.decode("utf-8"))
        versions = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets)
                    and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)]
    except (SyntaxError, UnicodeError) as exc:
        raise ReleaseError("Cannot read the committed application version") from exc
    if len(versions) != 1 or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?", versions[0]):
        raise ReleaseError("The committed application VERSION must be a single semantic version")
    return versions[0]


def build(root: Path, output: Path, ref: str = "HEAD", expect_version: str | None = None) -> dict:
    root, output = Path(root).resolve(), Path(output).resolve()
    if git(root, "status", "--porcelain", "--untracked-files=no").strip():
        raise ReleaseError("Commit or discard tracked changes before building a release")
    commit = git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode("ascii").strip()
    entries = {}
    for entry in git(root, "ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
        if not entry:
            continue
        meta, raw_name = entry.split(b"\t", 1)
        mode, kind, blob = meta.decode("ascii").split()
        name = raw_name.decode("utf-8")
        if not approved_path(name):
            continue
        if kind != "blob" or mode not in ("100644", "100755"):
            raise ReleaseError("Symlinks and submodules cannot be included in a release: " + name)
        entries[name] = (mode, git(root, "cat-file", "blob", blob))
    missing = sorted(REQUIRED - entries.keys())
    if missing:
        raise ReleaseError("The committed release is missing required files: " + ", ".join(missing))
    version = version_from_source(entries[VERSION_FILE][1])
    if expect_version is not None and version != expect_version:
        raise ReleaseError(f"Tag version {expect_version!r} does not match application VERSION {version!r}")
    prefix = "CMVL-" + version
    manifest = {"format": 1, "product": "CMVL", "version": version, "commit": commit,
                "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                          for name, (_, data) in sorted(entries.items())}}
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    stamp = int(git(root, "show", "-s", "--format=%ct", commit).decode("ascii"))
    moment = datetime.fromtimestamp(max(stamp, 315532800), timezone.utc)
    timestamp = (moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / (prefix + ".zip")
    with tempfile.NamedTemporaryFile(prefix=".cmvl-release-", suffix=".zip", dir=output, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
            for name, (mode, data) in sorted(entries.items()):
                info = zipfile.ZipInfo(prefix + "/" + name, date_time=timestamp)
                info.create_system = 3
                info.external_attr = int(mode, 8) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                zipped.writestr(info, data, compresslevel=9)
            info = zipfile.ZipInfo(prefix + "/release-manifest.json", date_time=timestamp)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zipped.writestr(info, manifest_bytes, compresslevel=9)
        temporary_path.replace(archive)
    finally:
        temporary_path.unlink(missing_ok=True)
    manifest_path = output / (prefix + ".manifest.json")
    manifest_path.write_bytes(manifest_bytes)
    sums = "".join(hashlib.sha256(file.read_bytes()).hexdigest() + "  " + file.name + "\n"
                   for file in (archive, manifest_path))
    (output / "SHA256SUMS").write_text(sums, encoding="ascii")
    return {"version": version, "commit": commit, "archive": str(archive),
            "manifest": str(manifest_path), "checksums": str(output / "SHA256SUMS"), "files": len(entries)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD", help="committed Git revision (default: HEAD)")
    parser.add_argument("--output", type=Path, default=ROOT / "dist", help="output directory (default: dist)")
    parser.add_argument("--expect-version", help="fail if the committed VERSION differs from this tag version")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(build(ROOT, args.output, args.ref, args.expect_version), indent=2))
        return 0
    except (ReleaseError, OSError, UnicodeError, ValueError) as exc:
        print("Release build failed: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
