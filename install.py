#!/usr/bin/env python3
"""Install the codemap skill into AI coding agents' skill folders (Windows, macOS, Linux).

  python install.py                      user-wide for Claude Code + the shared .agents folder
  python install.py --project PATH       only for one project (PATH/.claude/skills, PATH/.agents/skills)
  python install.py --agents claude      pick targets: claude, agents, gemini, cursor, copilot (comma separated)
  python install.py --uninstall          remove codemap from the chosen targets
  python install.py --auto               opt in to automatic Claude task hooks
  python install.py --no-auto            remove our Claude hooks; keep the installed skill
  python install.py --dry-run            show what would happen

Which folder each agent reads (as of October 2026):
  claude   ~/.claude/skills  or  <project>/.claude/skills     Claude Code (Copilot and Cursor also read it)
  agents   ~/.agents/skills  or  <project>/.agents/skills     OpenAI Codex, Gemini CLI, Cursor, GitHub Copilot, OpenCode, ...
  gemini   ~/.gemini/skills  or  <project>/.gemini/skills     Gemini CLI
  cursor   ~/.cursor/skills  or  <project>/.cursor/skills     Cursor
  copilot                          <project>/.github/skills     GitHub Copilot (project only)
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import shutil
import stat
import sys
import tempfile
import uuid
from pathlib import Path

SKILL = "codemap"
SOURCE = Path(__file__).resolve().parent / "skills" / SKILL
sys.path.insert(0, str(SOURCE / "scripts"))
from codemap_lib.claude_hooks import (AUTO_MARKER, hook_command as _hook_command,
                                     owned_hook as _owned_hook, ps_literal as _ps_literal,
                                     powershell_script as _powershell_script)
from codemap_lib import VERSION
TARGETS = {
    "claude": ("~/.claude/skills", ".claude/skills"),
    "agents": ("~/.agents/skills", ".agents/skills"),
    "gemini": ("~/.gemini/skills", ".gemini/skills"),
    "cursor": ("~/.cursor/skills", ".cursor/skills"),
    "copilot": (None, ".github/skills"),
}
DEFAULT = ("claude", "agents")
AUTO_EVENTS = {"UserPromptSubmit": ("start", 30), "Stop": ("stop", 60), "SessionEnd": ("abort", 30)}


def _force_remove(func, path, _exc):
    Path(path).chmod(stat.S_IWRITE)
    func(path)


def _remove(path: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_force_remove)
    else:
        shutil.rmtree(path, onerror=_force_remove)


def _is_ours(folder: Path) -> bool:
    skill_md = folder / "SKILL.md"
    return skill_md.exists() and f"name: {SKILL}" in skill_md.read_text(encoding="utf-8", errors="replace")


def _settings_plan(path: Path, script: Path, enable: bool):
    """Read/validate before installation changes; return bytes for an optional atomic write."""
    original = path.read_bytes() if path.exists() else None
    try:
        settings = json.loads(original.decode("utf-8-sig")) if original is not None else {}
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"refuse malformed Claude settings at {path}; original was not changed") from exc
    if not isinstance(settings, dict):
        raise ValueError(f"Claude settings must be a JSON object: {path}")
    previous = json.loads(json.dumps(settings))
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"Claude hooks must be an object: {path}")
    for event, (action, timeout) in AUTO_EVENTS.items():
        entries = hooks.get(event, [])
        if not isinstance(entries, list):
            raise ValueError(f"Claude {event} hooks must be a list: {path}")
        kept = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                raise ValueError(f"invalid Claude {event} hook entry: {path}")
            remaining = [item for item in entry["hooks"] if not _owned_hook(item, script)]
            if remaining == entry["hooks"]:
                kept.append(entry)
            elif remaining:
                kept.append({**entry, "hooks": remaining})
        if enable:
            kept.append({"hooks": [{"type": "command", "command": _hook_command(script, action), "timeout": timeout}]})
        if kept or event in hooks and entries == []:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if hooks or "hooks" in previous and previous["hooks"] == {}:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    if settings == previous:
        return original, None
    return original, (json.dumps(settings, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _atomic_bytes(path: Path, data: bytes, mode: int) -> None:
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_settings(path: Path, original: bytes | None, updated: bytes | None) -> None:
    if updated is None:
        return
    current = path.read_bytes() if path.exists() else None
    if current != original:
        raise ValueError(f"Claude settings changed during installation; retry: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if original is not None else 0o600
    if original is not None:
        backup = path.with_name(path.name + ".codemap-backup-" + uuid.uuid4().hex + ".json")
        _atomic_bytes(backup, original, mode)
    _atomic_bytes(path, updated, mode)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Install the codemap skill for AI coding agents.")
    p.add_argument("--project", help="install into this project instead of your user folder")
    p.add_argument("--agents", default=",".join(DEFAULT), help=f"targets: {', '.join(TARGETS)} (default: {','.join(DEFAULT)})")
    p.add_argument("--uninstall", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    auto = p.add_mutually_exclusive_group()
    auto.add_argument("--auto", action="store_true", help="opt in to automatic Claude task hooks")
    auto.add_argument("--no-auto", action="store_true", help="remove our Claude hooks without changing the installed skill")
    p.add_argument("--force", action="store_true", help="replace an existing folder named codemap that is not ours")
    args = p.parse_args(argv)

    if not (SOURCE / "SKILL.md").exists():
        print(f"cannot find the skill at {SOURCE}", file=sys.stderr)
        return 1
    chosen = [a.strip() for a in args.agents.split(",") if a.strip()]
    unknown = [a for a in chosen if a not in TARGETS]
    if unknown:
        print(f"unknown target(s): {', '.join(unknown)}; choose from {', '.join(TARGETS)}", file=sys.stderr)
        return 2

    base = Path(args.project).resolve() if args.project else None
    if (args.auto or args.no_auto) and "claude" not in chosen:
        print("automatic hooks support Claude only; include claude in --agents", file=sys.stderr)
        return 2
    settings_plan = None
    if "claude" in chosen and (args.auto or args.no_auto or args.uninstall):
        claude = (base / ".claude") if base else Path.home() / ".claude"
        settings = claude / ("settings.local.json" if base else "settings.json")
        script = claude / "skills" / SKILL / "scripts" / "codemap.py"
        try:
            settings_plan = (settings, *_settings_plan(settings, script, args.auto and not args.uninstall))
        except (OSError, ValueError) as exc:
            print(f"codemap: {exc}", file=sys.stderr)
            return 2
        if args.dry_run and args.auto and not args.uninstall:
            print(f"would enable Claude task hooks {settings}")
    if args.no_auto or args.uninstall:
        if settings_plan:
            settings, original, updated = settings_plan
            print(f"{'update' if updated is not None else 'unchanged'} Claude hooks {settings}")
            if not args.dry_run:
                try:
                    _write_settings(settings, original, updated)
                except (OSError, ValueError) as exc:
                    print(f"codemap: {exc}", file=sys.stderr)
                    return 1
        if args.no_auto and not args.uninstall:
            return 0
    status = 0
    for name in chosen:
        user_dir, project_dir = TARGETS[name]
        if base is None and user_dir is None:
            print(f"skip {name}: it only supports project installs (use --project PATH)")
            continue
        root = (base / project_dir) if base else Path.home() / user_dir.removeprefix("~/")
        dest = root / SKILL
        if args.uninstall:
            if dest.exists() and _is_ours(dest):
                print(f"remove  {dest}")
                if not args.dry_run:
                    _remove(dest)
            else:
                print(f"absent  {dest}")
            continue
        if dest.exists() and not _is_ours(dest) and not args.force:
            print(f"refuse  {dest} exists and is not codemap (use --force)", file=sys.stderr)
            status = 1
            continue
        print(f"install {dest}")
        if args.dry_run:
            continue
        if dest.exists():
            _remove(dest)
        shutil.copytree(SOURCE, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if name == "claude" and args.auto and settings_plan:
            try:
                settings, original, updated = settings_plan
                _write_settings(settings, original, updated)
                print(f"{'updated' if updated is not None else 'unchanged'} Claude hooks {settings}")
            except (OSError, ValueError) as exc:
                print(f"codemap: {exc}", file=sys.stderr)
                status = 1
    if not args.uninstall and not args.dry_run and status == 0:
        print(f"done. CMVL {VERSION} installed, including the offline dashboard. Restart your agent, then ask it e.g. \"explain what you just changed\" or \"map this repo\".")
    return status


if __name__ == "__main__":
    sys.exit(main())
