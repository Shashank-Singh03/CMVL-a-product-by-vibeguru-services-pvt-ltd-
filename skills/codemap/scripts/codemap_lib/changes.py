"""What changed? Resolves the scope (diff vs base, session baseline, or whole repo) and
collects per-file hunks from git, or from a file snapshot when there is no git."""
from __future__ import annotations

import difflib
import re
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .scan import MAX_BYTES, language_of, walk_files
from .util import read_json, sha1, write_json, write_text

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class GitError(RuntimeError):
    pass


def git(cwd, *args: str, check: bool = True) -> str:
    try:
        proc = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=str(cwd),
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise GitError("git is not installed") from exc
    if check and proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {args[0]} failed")
    return proc.stdout


def repo_root(path: Path) -> Path | None:
    try:
        return Path(git(path, "rev-parse", "--show-toplevel").strip())
    except GitError:
        return None


def rev(root: Path, ref: str) -> str | None:
    try:
        return git(root, "rev-parse", "--verify", "--quiet", ref + "^{commit}").strip() or None
    except GitError:
        return None


# ---------------------------------------------------------------- diff model

@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    minus: list = field(default_factory=list)   # [(old line no, text)]
    plus: list = field(default_factory=list)    # [(new line no, text)]


@dataclass
class FileDiff:
    path: str            # current path (the old path for deleted files)
    old_path: str
    status: str          # added | deleted | modified | renamed
    hunks: list = field(default_factory=list)
    binary: bool = False

    @property
    def add(self) -> int:
        return sum(len(h.plus) for h in self.hunks)

    @property
    def dele(self) -> int:
        return sum(len(h.minus) for h in self.hunks)


@dataclass
class Scope:
    mode: str                  # "diff" | "repo"
    kind: str = ""             # "git" | "files" (how the diff is computed)
    base: str = ""             # commit sha (git)
    label: str = ""            # human description of the comparison
    reason: str = ""           # why this scope was chosen


@dataclass
class ChangeSet:
    files: dict = field(default_factory=dict)        # path -> FileDiff
    old_texts: dict = field(default_factory=dict)    # path -> previous source (filled lazily)
    commits: list = field(default_factory=list)      # commit subjects/bodies in range
    skipped: list = field(default_factory=list)      # [{path, reason}]: excluded/failed reads, never inferred deletions
    _old_loader: object = None

    def old_text(self, path: str) -> str | None:
        if path not in self.old_texts:
            self.old_texts[path] = self._old_loader(path) if self._old_loader else None
        return self.old_texts[path]


def _unquote(p: str) -> str:
    p = p.rstrip("\t")
    if p.startswith('"') and p.endswith('"'):
        p = p[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape").encode("latin-1").decode("utf-8", "replace")
    return p


def parse_diff(text: str) -> dict:
    """Parse `git diff --unified=0` output into FileDiffs keyed by current path."""
    files: dict = {}
    lines = text.split("\n")
    i, n = 0, len(lines)
    cur = None
    while i < n:
        line = lines[i]
        if line.startswith("diff --git "):
            cur = FileDiff(path="", old_path="", status="modified")
            m = re.match(r'^diff --git "?a/(.+?)"? "?b/(.+?)"?$', line)
            if m:
                cur.old_path, cur.path = m.group(1), m.group(2)
            i += 1
            while i < n and not lines[i].startswith(("diff --git ", "@@")):
                h = lines[i]
                if h.startswith("new file mode"):
                    cur.status = "added"
                elif h.startswith("deleted file mode"):
                    cur.status = "deleted"
                elif h.startswith("rename from "):
                    cur.old_path, cur.status = _unquote(h[12:]), "renamed"
                elif h.startswith("rename to "):
                    cur.path = _unquote(h[10:])
                elif h.startswith("--- ") and h[4:] != "/dev/null":
                    cur.old_path = _unquote(h[4:])[2:] if _unquote(h[4:]).startswith("a/") else _unquote(h[4:])
                elif h.startswith("+++ ") and h[4:] != "/dev/null":
                    cur.path = _unquote(h[4:])[2:] if _unquote(h[4:]).startswith("b/") else _unquote(h[4:])
                elif h.startswith("Binary files"):
                    cur.binary = True
                i += 1
            if cur.status == "deleted":
                cur.path = cur.old_path
            elif cur.status == "added":
                cur.old_path = cur.path
            files[cur.path] = cur
            continue
        if line.startswith("@@") and cur is not None:
            m = HUNK_RE.match(line)
            i += 1
            if not m:
                continue
            o_start, o_len = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            n_start, n_len = int(m.group(3)), int(m.group(4) if m.group(4) is not None else 1)
            hunk = Hunk(o_start, o_len, n_start, n_len)
            o_no, n_no, left_minus, left_plus = o_start, n_start, o_len, n_len
            while i < n and (left_minus > 0 or left_plus > 0 or lines[i].startswith("\\")):
                body = lines[i]
                if body.startswith("\\"):
                    pass
                elif body.startswith("-") and left_minus > 0:
                    hunk.minus.append((o_no, body[1:]))
                    o_no, left_minus = o_no + 1, left_minus - 1
                elif body.startswith("+") and left_plus > 0:
                    hunk.plus.append((n_no, body[1:]))
                    n_no, left_plus = n_no + 1, left_plus - 1
                elif body.startswith(" "):
                    o_no, n_no = o_no + 1, n_no + 1
                    left_minus, left_plus = left_minus - 1, left_plus - 1
                else:
                    break
                i += 1
            cur.hunks.append(hunk)
            continue
        i += 1
    return files


def diff_texts(old: str, new: str) -> list:
    """Hunks between two texts, numbered like `git diff --unified=0`."""
    a, b = old.splitlines(), new.splitlines()
    hunks = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        hunks.append(Hunk(
            old_start=i1 + 1 if i2 > i1 else i1, old_len=i2 - i1,
            new_start=j1 + 1 if j2 > j1 else j1, new_len=j2 - j1,
            minus=[(k + 1, a[k]) for k in range(i1, i2)],
            plus=[(k + 1, b[k]) for k in range(j1, j2)],
        ))
    return hunks


def _added(path: str, text: str) -> FileDiff:
    lines = text.splitlines()
    return FileDiff(path, path, "added", [Hunk(0, 0, 1, len(lines), [], [(k + 1, t) for k, t in enumerate(lines)])])


def _read_status(path: Path) -> tuple:
    """(text, reason); a missing file is distinct from an unreadable existing one."""
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            return None, "not_file"
        if info.st_size > MAX_BYTES:
            return None, "too_large"
        raw = path.read_bytes()
        if len(raw) > MAX_BYTES:
            return None, "too_large"
        if b"\x00" in raw[:8192]:
            return None, "binary"
        # Match read_text's UTF-8/BOM and universal-newline handling.
        text = raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        return text, None
    except FileNotFoundError:
        try:
            entry = path.lstat()
        except FileNotFoundError:
            return None, "missing"
        except OSError:
            return None, "unreadable"
        # stat follows symlinks: a dangling link still has a directory entry.
        return None, "not_file" if stat.S_ISLNK(entry.st_mode) else "unreadable"
    except OSError:
        return None, "unreadable"


def _read(path: Path) -> str | None:
    return _read_status(path)[0]


# ---------------------------------------------------------------- baseline ("mark")

def mark(root: Path, out_dir: Path, is_git: bool) -> dict:
    """Record the current state so a later `analyze` explains exactly what changed since now."""
    snap_dir = out_dir / "baseline"
    if snap_dir.exists():
        shutil.rmtree(snap_dir)
    copied = []
    data = {"created": time.strftime("%Y-%m-%d %H:%M"), "kind": "git" if is_git else "files"}
    if is_git and rev(root, "HEAD"):
        snapshot = git(root, "stash", "create").strip()   # commit object of the dirty tree; touches nothing
        data["base"] = snapshot or rev(root, "HEAD")
        candidates = [p for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if p]
    else:
        data["kind"] = "files"
        candidates = walk_files(root)
    for rel in candidates:
        if not language_of(rel) or rel.startswith(".codemap/"):
            continue
        text = _read(root / rel)
        if text is None:
            continue
        write_text(snap_dir / rel, text)
        copied.append(rel)
    data["files"] = copied
    write_json(out_dir / "baseline.json", data, indent=2)
    return data


# ---------------------------------------------------------------- scope + changes

def resolve_scope(root: Path, is_git: bool, base_arg: str | None, repo_mode: bool, out_dir: Path) -> Scope:
    if repo_mode:
        return Scope("repo", reason="--repo")
    if base_arg:
        if not is_git:
            raise GitError("--base needs a git repository")
        sha = rev(root, base_arg)
        if not sha:
            raise GitError(f"unknown ref: {base_arg}")
        head = rev(root, "HEAD")
        merge = git(root, "merge-base", sha, head, check=False).strip() if head else ""
        base = merge or sha
        label = f"{base_arg} ({base[:7]})" + (" merge-base" if merge and merge != sha else "")
        return Scope("diff", "git", base, label, "--base")
    baseline = read_json(out_dir / "baseline.json")
    if baseline:
        label = f"session baseline from {baseline.get('created', '?')}"
        return Scope("diff", baseline.get("kind", "files"), baseline.get("base", ""), label, "codemap mark")
    if not is_git:
        return Scope("repo", reason="not a git repository (run `codemap mark` first to explain later edits)")
    head = rev(root, "HEAD")
    if not head:
        return Scope("repo", reason="repository has no commits yet")
    dirty = git(root, "status", "--porcelain", "--untracked-files=all").splitlines()
    if any(language_of(line[3:].strip().strip('"')) for line in dirty):
        return Scope("diff", "git", head, f"uncommitted changes vs HEAD {head[:7]}", "auto: working tree has changes")
    parent = rev(root, "HEAD~1")
    if parent:
        return Scope("diff", "git", parent, f"last commit {head[:7]}", "auto: working tree clean")
    return Scope("repo", reason="single commit and nothing uncommitted")


def collect_changes(root: Path, scope: Scope, current_files: list, out_dir: Path, sub: str = "") -> ChangeSet:
    cs = ChangeSet()
    snap_dir = out_dir / "baseline"
    baseline = read_json(out_dir / "baseline.json") or {}
    snap_files = set(baseline.get("files", [])) if scope.reason == "codemap mark" else set()

    if scope.kind == "git":
        args = ["diff", "--no-color", "--no-ext-diff", "--unified=0", "-M", scope.base, "--"]
        if sub:
            args.append(sub)
        cs.files = {p: fd for p, fd in parse_diff(git(root, *args)).items() if not fd.binary}
        untracked = [p for p in git(root, "ls-files", "--others", "--exclude-standard", "-z", "--", sub or ".").split("\0") if p]
        for rel in untracked:
            if not language_of(rel) or rel.startswith(".codemap/"):
                continue
            text, reason = _read_status(root / rel)
            if text is None:
                if reason != "missing":
                    cs.skipped.append({"path": rel, "reason": reason})
                continue
            if rel in snap_files:
                old = _read(snap_dir / rel)
                if old is None:
                    cs.skipped.append({"path": rel, "reason": "baseline_unavailable"})
                    continue
                hunks = diff_texts(old, text)
                if hunks:
                    cs.files[rel] = FileDiff(rel, rel, "modified", hunks)
                    cs.old_texts[rel] = old
            else:
                cs.files[rel] = _added(rel, text)

        # Untracked files captured by `mark` are absent from git's diff. A
        # removed snapshot entry is absent from the current untracked list too.
        for rel in sorted(snap_files - set(untracked)):
            if rel in cs.files or (sub and not rel.startswith(sub.rstrip("/") + "/")):
                continue
            _text, reason = _read_status(root / rel)
            if reason != "missing":
                if reason is not None:
                    cs.skipped.append({"path": rel, "reason": reason})
                continue
            old = _read(snap_dir / rel)
            if old is not None:
                cs.files[rel] = FileDiff(rel, rel, "deleted", diff_texts(old, ""))
                cs.old_texts[rel] = old
            else:
                cs.skipped.append({"path": rel, "reason": "baseline_unavailable"})

        def load_old(path: str) -> str | None:
            fd = cs.files.get(path)
            old_path = fd.old_path if fd else path
            if old_path in snap_files:
                return _read(snap_dir / old_path)
            try:
                # cat-file, not show: `git show rev:path` also stats the argument as a file name,
                # which fails with "Filename too long" in deep Windows folders
                return git(root, "cat-file", "blob", f"{scope.base}:{old_path}")
            except GitError:
                return None
        cs._old_loader = load_old
        if scope.reason != "codemap mark":
            try:
                log = git(root, "log", "--no-merges", "--format=%s%n%b%x00", f"{scope.base}..HEAD")
                cs.commits = [c.strip() for c in log.split("\0") if c.strip()][:10]
            except GitError:
                pass
    else:  # snapshot comparison (no git)
        current = set(current_files)
        before = {p for p in snap_files if not sub or p.startswith(sub.rstrip("/") + "/")}
        for rel in sorted(current | before):
            new, reason = _read_status(root / rel)
            if reason not in (None, "missing"):
                cs.skipped.append({"path": rel, "reason": reason})
                continue
            if rel not in current and reason != "missing":
                cs.skipped.append({"path": rel, "reason": "not_scanned"})
                continue
            old = _read(snap_dir / rel) if rel in before else None
            if rel in before and old is None:
                cs.skipped.append({"path": rel, "reason": "baseline_unavailable"})
                continue
            if old is None and new is not None:
                cs.files[rel] = _added(rel, new)
            elif new is None and old is not None:
                lines = old.splitlines()
                cs.files[rel] = FileDiff(rel, rel, "deleted", [Hunk(1, len(lines), 0, 0, [(k + 1, t) for k, t in enumerate(lines)], [])])
                cs.old_texts[rel] = old
            elif old is not None and new is not None and sha1(old) != sha1(new):
                cs.files[rel] = FileDiff(rel, rel, "modified", diff_texts(old, new))
                cs.old_texts[rel] = old
        cs._old_loader = lambda path: _read(snap_dir / path)
    return cs
