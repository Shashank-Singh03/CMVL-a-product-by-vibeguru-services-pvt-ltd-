"""Private, immutable before/end snapshots and atomic per-task state.

Captures describe intervals, not atomic filesystem transactions or verified
authorship. No repository program runs, and Git is consulted read-only.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import pipeline
from .scan import MAX_BYTES, SKIP_DIRS, keep

VERSION = 1
LOCK_SECONDS = 10
MAX_JSON_BYTES = 16_000_000
MAX_OPAQUE_FILE_BYTES = 16_000_000
MAX_OPAQUE_TOTAL_BYTES = 64_000_000
_INSTALLED_SKILL_PREFIXES = tuple(folder + "/skills/codemap/" for folder in
                                  (".claude", ".agents", ".gemini", ".cursor", ".github"))
_PROTECTED = {"version", "revision", "task_id", "root", "source_root", "task_dir", "before_root", "work_root",
              "out_dir", "session_key", "prompt_key", "before", "after", "created_at", "started_at", "frozen_at",
              "coding_active", "attribution", "overlaps", "superseded_by", "freeze_started_at", "updated_at"}


class TaskError(RuntimeError):
    pass


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest(value) -> str:
    return _hash(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))


def _task_id(value):
    if not isinstance(value, str) or len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise TaskError("invalid task ID")
    return value


def _key(value, name):
    if not isinstance(value, str) or not value or len(value) > 8_192:
        raise TaskError(f"{name} must be a bounded nonempty string")
    return _hash(value)


def _relative(value):
    if (not isinstance(value, str) or not value or value.startswith("/") or "\\" in value or ":" in value
            or any(ord(c) < 32 for c in value) or any(p in ("", ".", "..") for p in value.split("/"))):
        raise TaskError("unsafe snapshot path")
    return value


def _eligible(name):
    # Exclude only this tool's project-local installation, preserving user
    # policies, other skills, and ordinary application folders named skills.
    return keep(name, True) and not name.casefold().startswith(_INSTALLED_SKILL_PREFIXES)


def _reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _inside_path(base, path, allow_missing=False):
    """Reject links/junctions in every existing component before any read/write."""
    try:
        parts = path.relative_to(base).parts
    except ValueError as exc:
        raise TaskError("path leaves its private snapshot directory") from exc
    cursor = base
    for part in parts:
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            if allow_missing:
                continue
            raise
        if _reparse(info):
            raise TaskError("symlinks and junctions are unsupported in task snapshots")
    return path


def _root(value):
    try:
        given = Path(value).resolve()
        if not given.is_dir():
            raise TaskError("project directory is unavailable")
        located, _, _ = pipeline.locate(given)
        return located.resolve()
    except (OSError, pipeline.CodemapError, pipeline.GitError) as exc:
        raise TaskError("project directory is unavailable") from exc


def _store(root, create=False):
    target = root / ".codemap" / "tasks"
    _inside_path(root, target, allow_missing=True)
    if create:
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
    return target


def _private_ignore(root, store):
    # Call only while holding the project lock. Otherwise another first-time
    # caller can read the exclusively created file before its writer flushes.
    ignore = _inside_path(root, store / ".gitignore", allow_missing=True)
    try:
        with ignore.open("xb") as stream:
            stream.write(b"*\n")
    except FileExistsError:
        if (not stat.S_ISREG(ignore.lstat().st_mode) or ignore.stat().st_size > 16
                or ignore.read_bytes().replace(b"\r\n", b"\n") != b"*\n"):
            raise TaskError("private task ignore file is invalid")


def _layout(root, identifier):
    directory = _store(root) / _task_id(identifier)
    return {"root": str(root), "source_root": str(root), "task_id": identifier,
            "task_dir": str(directory), "out_dir": str(directory / "output"),
            "before_root": str(directory / "output" / "baseline"), "work_root": str(directory / "work")}


def _read_json(base, path):
    try:
        _inside_path(base, path)
        if not stat.S_ISREG(path.lstat().st_mode):
            raise TaskError("task metadata is not a regular file")
        if path.stat().st_size > MAX_JSON_BYTES:
            raise TaskError("task metadata exceeds its size limit")
        with path.open("rb") as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise TaskError("task metadata exceeds its size limit")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise TaskError("task metadata is invalid")
        return value
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise TaskError("task metadata is missing, unreadable or invalid") from exc


def _write_json(base, path, value):
    temporary = None
    try:
        encoded = json.dumps(value, indent=2, allow_nan=False).encode("utf-8") + b"\n"
        if len(encoded) > MAX_JSON_BYTES:
            raise TaskError("task metadata exceeds its size limit")
        _inside_path(base, path, allow_missing=True)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=".task-save-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except (OSError, ValueError, TypeError) as exc:
        raise TaskError("could not save task metadata") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def locked(root):
    """Bounded, non-reentrant project lock; automatically released after a crash.

    Public state operations acquire their own lock. Prefer transition() for CAS;
    do not nest them inside this low-level context manager.
    """
    root = _root(root)
    store = _store(root, create=True)
    path = _inside_path(root, store / ".lock", allow_missing=True)
    if path.exists() and not stat.S_ISREG(path.lstat().st_mode):
        raise TaskError("project lock is not a regular file")
    handle = None
    acquired = False
    try:
        handle = path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + LOCK_SECONDS
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TaskError("another task operation holds the project lock; retry shortly")
                time.sleep(.025)
        _private_ignore(root, store)
        yield root
    except OSError as exc:
        raise TaskError("private task storage is unavailable") from exc
    finally:
        if handle is not None:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def _load(root, identifier):
    layout = _layout(root, identifier)
    value = _read_json(root, Path(layout["task_dir"]) / "state.json")
    session_key, prompt_key = value.get("session_key"), value.get("prompt_key")
    valid_key = lambda key: isinstance(key, str) and len(key) == 64 and all(c in "0123456789abcdef" for c in key)
    if (type(value.get("version")) is not int or value["version"] != VERSION
            or type(value.get("revision")) is not int or value["revision"] < 1
            or any(value.get(k) != v for k, v in layout.items()) or not valid_key(session_key)
            or (prompt_key is not None and not valid_key(prompt_key)) or not isinstance(value.get("status"), str)
            or type(value.get("coding_active")) is not bool or not isinstance(value.get("overlaps"), list)
            or value.get("attribution") not in ("window", "ambiguous")):
        raise TaskError("task metadata identity or location is invalid")
    for other in value["overlaps"]:
        _task_id(other)
    return value


def load(root, task_id):
    root = _root(root)
    with locked(root):
        return _load(root, task_id)


def _save(root, state):
    state["revision"] += 1
    state["updated_at"] = time.time()
    _write_json(root, Path(state["task_dir"]) / "state.json", state)
    return state


def _session(root, session_key):
    path = _store(root) / "sessions" / (session_key + ".json")
    _inside_path(root, path, allow_missing=True)
    if not path.exists():
        return path, {"version": VERSION, "session_key": session_key, "current": None, "prompts": {}}
    pointer = _read_json(root, path)
    if pointer.get("version") != VERSION or pointer.get("session_key") != session_key or not isinstance(pointer.get("prompts"), dict):
        raise TaskError("session task pointer is invalid")
    if pointer.get("current") is not None:
        _task_id(pointer["current"])
    for key, identifier in pointer["prompts"].items():
        if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise TaskError("session prompt pointer is invalid")
        _task_id(identifier)
    return path, pointer


def current(root, session_id):
    root, session_key = _root(root), _key(session_id, "session_id")
    if not _store(root).exists():
        return None
    with locked(root):
        _, pointer = _session(root, session_key)
        return _load(root, pointer["current"]) if pointer["current"] else None


def _open_source(root, name, flags):
    if os.name == "nt":
        return os.open(root / name, flags)
    directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        parts = name.split("/")
        for part in parts[:-1]:
            next_directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        return os.open(parts[-1], flags, dir_fd=directory)
    finally:
        os.close(directory)


def _read_bytes_status(root, name, *, limit=MAX_BYTES, hash_only=False):
    """Like changes._read_status, preserving raw bytes and rejecting link escapes."""
    try:
        path = root / _relative(name)
        _inside_path(root, path)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            return None, "not_file"
        if info.st_size > limit:
            return None, "too_large"
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = _open_source(root, name, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                return None, "changed_during_capture"
            # On Windows, verify the actual opened handle before reading bytes;
            # this also catches a parent junction swapped after path validation.
            if os.name == "nt":
                import ctypes
                import msvcrt
                from ctypes import wintypes
                function = ctypes.windll.kernel32.GetFinalPathNameByHandleW
                function.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
                function.restype = wintypes.DWORD
                buffer = ctypes.create_unicode_buffer(32_768)
                length = function(msvcrt.get_osfhandle(stream.fileno()), buffer, len(buffer), 0)
                if not length or length >= len(buffer):
                    return None, "unreadable"
                final = buffer.value
                final = "\\\\" + final[8:] if final.startswith("\\\\?\\UNC\\") else final.removeprefix("\\\\?\\")
                try:
                    Path(final).relative_to(root)
                except ValueError:
                    return None, "unsafe_path"
            if hash_only:
                digest, size = hashlib.sha256(), 0
                while chunk := stream.read(min(1024 * 1024, limit + 1 - size)):
                    digest.update(chunk)
                    size += len(chunk)
                    if size > limit:
                        return None, "too_large"
                raw = {"sha256": digest.hexdigest(), "bytes": size}
            else:
                raw = stream.read(limit + 1)
            after = path.lstat()
            if _reparse(after) or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                return None, "changed_during_capture"
        if hash_only:
            return raw, None
        if len(raw) > limit:
            return None, "too_large"
        if b"\x00" in raw[:8192]:
            return None, "binary"
        return raw, None
    except FileNotFoundError:
        return None, "missing"
    except TaskError:
        return None, "unsafe_path"
    except OSError:
        return None, "unreadable"


def _write_bytes(base, name, raw):
    path = _inside_path(base, base / _relative(name), allow_missing=True)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)


def _capture(root, destination, previous=()):
    started = time.time()
    _inside_path(root, destination, allow_missing=True)
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    try:
        located, is_git, sub = pipeline.locate(root)
        paths = {name for name in pipeline.list_sources(located, is_git, sub, include_tests=True) if _eligible(name)}
        if is_git:
            # list_sources intentionally drops non-files; retain their entries as
            # skipped evidence instead of silently losing a new dangling link.
            listed = pipeline.git(located, "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", sub or ".")
            paths.update(name for name in listed.split("\x00") if name and _eligible(name))
    except (OSError, pipeline.CodemapError, pipeline.GitError) as exc:
        raise TaskError("source discovery failed during task capture") from exc
    files, skipped, missing, opaque_bytes = {}, [], [], 0
    for name in sorted(paths | set(previous)):
        raw, reason = _read_bytes_status(root, name)
        if name not in paths and reason is None:
            reason = "not_scanned"
        if reason is not None:
            if reason == "missing" and name in previous:
                missing.append(name)
            else:
                omission = {"path": name, "reason": reason}
                if reason in ("binary", "too_large"):
                    limit = min(MAX_OPAQUE_FILE_BYTES, MAX_OPAQUE_TOTAL_BYTES - opaque_bytes)
                    fingerprint, fingerprint_reason = _read_bytes_status(root, name, limit=limit, hash_only=True)
                    if fingerprint_reason is None:
                        omission.update(fingerprint)
                        opaque_bytes += fingerprint["bytes"]
                    else:
                        omission["hash_reason"] = fingerprint_reason
                skipped.append(omission)
            continue
        _write_bytes(destination, name, raw)
        files[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    return {"started_at": started, "finished_at": time.time(), "files": files, "skipped": skipped,
            "missing": missing, "digest": _digest(files), "capture": "interval; authorship is not verified"}


def _active_states(root):
    for folder in _store(root).iterdir():
        if folder.name in ("sessions", ".lock") or not folder.is_dir():
            continue
        _task_id(folder.name)
        _inside_path(root, folder)
        state = _load(root, folder.name)
        if state.get("coding_active"):
            yield state


def start(root, session_id, prompt_id=None):
    root, session_key = _root(root), _key(session_id, "session_id")
    prompt_key = _key(prompt_id, "prompt_id") if prompt_id is not None else None
    with locked(root):
        pointer_path, pointer = _session(root, session_key)
        if prompt_key is not None and prompt_key in pointer["prompts"]:
            return _load(root, pointer["prompts"][prompt_key])
        active = list(_active_states(root))
        identifier = uuid.uuid4().hex
        layout = _layout(root, identifier)
        directory = _inside_path(root, Path(layout["task_dir"]), allow_missing=True)
        directory.mkdir(mode=0o700)
        state = {"version": VERSION, "revision": 1, **layout, "session_key": session_key, "prompt_key": prompt_key,
                 "status": "capturing", "coding_active": True, "attribution": "window", "overlaps": [],
                 "started_at": time.time(), "before": None, "after": None,
                 "capture_note": "Snapshots record capture intervals. They do not verify isolated authorship."}
        state["created_at"] = state["started_at"]
        state["updated_at"] = state["started_at"]
        for other in active:
            if other["session_key"] == session_key:
                other.update(status="interrupted", coding_active=False, superseded_by=identifier,
                             attribution="ambiguous", interruption="A new prompt began before end capture completed.")
            else:
                state["attribution"] = other["attribution"] = "ambiguous"
                state["overlaps"].append(other["task_id"])
                other["overlaps"] = sorted(set(other["overlaps"] + [identifier]))
            _save(root, other)
        _write_json(root, directory / "state.json", state)
        pointer["current"] = identifier
        if prompt_key is not None:
            pointer["prompts"][prompt_key] = identifier
        _write_json(root, pointer_path, pointer)
        try:
            state["before"] = _capture(root, Path(layout["before_root"]))
            state["status"] = "active"
            return _save(root, state)
        except (OSError, TaskError) as exc:
            state.update(status="capture_failed", coding_active=False, capture_error="Start snapshot could not be completed safely.")
            _save(root, state)
            raise TaskError("start snapshot could not be completed safely; begin a new task") from exc


def _verify_tree(root, manifest):
    if not root.is_dir():
        raise TaskError("task snapshot directory is missing")
    expected = manifest.get("files")
    if not isinstance(expected, dict) or manifest.get("digest") != _digest(expected):
        raise TaskError("task snapshot manifest is invalid")
    actual = set()
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in list(dirs):
            _inside_path(root, Path(directory) / name)
        for name in names:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if _eligible(relative):
                actual.add(relative)
    if actual != set(expected):
        raise TaskError("task snapshot files changed; do not use this capture")
    for name, metadata in expected.items():
        raw, reason = _read_bytes_status(root, name)
        if reason is not None or metadata != {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}:
            raise TaskError("task snapshot content changed; do not use this capture")


def freeze(root, task_id):
    root = _root(root)
    with locked(root):
        state = _load(root, task_id)
        _inside_path(root, Path(state["before_root"]))
        _inside_path(root, Path(state["work_root"]), allow_missing=True)
        if state.get("after") is not None:
            _verify_tree(Path(state["work_root"]), state["after"])
            _verify_tree(Path(state["before_root"]), state["before"])
            return state
        if not state.get("coding_active") or state.get("before") is None:
            raise TaskError("task end capture is unavailable or interrupted; begin a new task")
        _verify_tree(Path(state["before_root"]), state["before"])
        state.update(status="freezing", coding_active=False, freeze_started_at=time.time())
        _save(root, state)
        try:
            after = _capture(root, Path(state["work_root"]), state["before"]["files"])
            created = datetime.fromtimestamp(state["started_at"], timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            baseline = {"kind": "files", "created": created, "files": sorted(state["before"]["files"])}
            _write_json(root, Path(state["out_dir"]) / "baseline.json", baseline)
            state.update(status="frozen", frozen_at=time.time(), after=after)
            return _save(root, state)
        except (OSError, TaskError) as exc:
            state.update(status="capture_failed", capture_error="End snapshot could not be completed safely.")
            _save(root, state)
            raise TaskError("end snapshot could not be completed safely; begin a new task") from exc


def changed(state):
    before, after = state.get("before"), state.get("after")
    if not before or not after:
        return None
    def omitted(manifest):
        result = {}
        for item in manifest.get("skipped", []):
            if item.get("reason") not in ("binary", "too_large") or "sha256" not in item or "bytes" not in item:
                return None
            result[item["path"]] = {key: item[key] for key in ("reason", "sha256", "bytes")}
        return result
    before_omitted, after_omitted = omitted(before), omitted(after)
    if before_omitted is None or after_omitted is None or before_omitted != after_omitted:
        return None
    return before["digest"] != after["digest"]


def verify(root, task_id):
    root = _root(root)
    with locked(root):
        state = _load(root, task_id)
        if state.get("after") is None:
            raise TaskError("task has no completed end snapshot")
        _inside_path(root, Path(state["before_root"]))
        _inside_path(root, Path(state["work_root"]))
        _verify_tree(Path(state["before_root"]), state["before"])
        _verify_tree(Path(state["work_root"]), state["after"])
        baseline = _read_json(root, Path(state["out_dir"]) / "baseline.json")
        if baseline.get("kind") != "files" or baseline.get("files") != sorted(state["before"]["files"]):
            raise TaskError("task comparison baseline changed")
        return True


def transition(root, task_id, expected, **fields):
    if not isinstance(expected, dict) or any(key in _PROTECTED for key in fields):
        raise TaskError("task identity and snapshot fields cannot be updated")
    root = _root(root)
    with locked(root):
        state = _load(root, task_id)
        if any(_digest(state.get(key)) != _digest(value) for key, value in expected.items()):
            return None
        state.update(fields)
        if state.get("status") in ("cancelled", "failed", "interrupted", "skipped", "complete", "capture_failed"):
            state["coding_active"] = False
        return _save(root, state)


def update(root, task_id, **fields):
    return transition(root, task_id, {}, **fields)
