"""Installer isolation and real hook-command transport checks; no user settings touched."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("codemap_installer", ROOT / "install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)

ECHO_HOOK = '''import json, sys
payload = json.load(sys.stdin)
print(json.dumps({"decision": "block", "reason": "fixture only", "action": sys.argv[-1], "payload": payload}, ensure_ascii=False))
'''


class AutoInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codemap-install-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "fixture-skill"
        (self.source / "scripts").mkdir(parents=True)
        (self.source / "SKILL.md").write_text("---\nname: codemap\n---\nfixture\n", encoding="utf-8")
        (self.source / "scripts" / "codemap.py").write_text(ECHO_HOOK, encoding="utf-8")
        self.project = self.root / "project"
        self.project.mkdir()
        self.home = self.root / "mock-home"
        self.home.mkdir()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(installer, "SOURCE", self.source).start()
        # Even an accidentally omitted --project stays in this fixture home.
        mock.patch.object(installer.Path, "home", return_value=self.home).start()

    @property
    def settings(self):
        return self.project / ".claude" / "settings.local.json"

    @property
    def installed(self):
        return self.project / ".claude" / "skills" / "codemap"

    @property
    def script(self):
        return self.installed / "scripts" / "codemap.py"

    def run_install(self, *args, project=True):
        argv = ["--agents", "claude"]
        if project:
            argv += ["--project", str(self.project)]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return installer.main(argv + list(args))

    def write_settings(self, value):
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(json.dumps(value, indent=1) + "\n", encoding="utf-8")
        return self.settings.read_bytes()

    def load_settings(self):
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def own_hooks(self, settings):
        return [hook for entries in settings.get("hooks", {}).values() for entry in entries
                for hook in entry["hooks"] if installer._owned_hook(hook, self.script)]

    def test_manual_install_leaves_settings_byte_exact(self):
        original = self.write_settings({"permissions": {"deny": ["Read(secret)"]}, "hooks": {"Stop": []}})
        self.assertEqual(self.run_install(), 0)
        self.assertTrue(self.script.is_file())
        self.assertEqual(self.settings.read_bytes(), original)
        self.assertEqual(list(self.settings.parent.glob("*.codemap-backup-*.json")), [])

    def test_auto_preserves_unrelated_settings_and_is_idempotent(self):
        unrelated = {"type": "command", "command": "echo unrelated", "timeout": 7}
        existing = {"permissions": {"allow": ["Read"]}, "env": {"EDITOR": "fixture"},
                    "hooks": {"Stop": [{"matcher": "", "hooks": [unrelated]}],
                              "PreToolUse": [{"matcher": "Edit", "hooks": [unrelated]}]}}
        original = self.write_settings(existing)
        self.assertEqual(self.run_install("--auto"), 0)
        settings = self.load_settings()
        self.assertEqual(settings["permissions"], existing["permissions"])
        self.assertEqual(settings["env"], existing["env"])
        self.assertEqual(settings["hooks"]["PreToolUse"], existing["hooks"]["PreToolUse"])
        self.assertEqual(settings["hooks"]["Stop"][0], existing["hooks"]["Stop"][0])
        self.assertEqual(len(self.own_hooks(settings)), 3)
        self.assertNotIn("StopFailure", settings["hooks"])
        self.assertEqual(settings["hooks"]["Stop"][-1]["hooks"][0]["timeout"], 60)
        backups = list(self.settings.parent.glob("*.codemap-backup-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        installed_bytes = self.settings.read_bytes()
        self.assertEqual(self.run_install("--auto"), 0)
        self.assertEqual(self.settings.read_bytes(), installed_bytes)
        self.assertEqual(list(self.settings.parent.glob("*.codemap-backup-*.json")), backups)

    def test_no_auto_keeps_skill_and_mixed_foreign_hooks(self):
        self.assertEqual(self.run_install("--auto"), 0)
        retained = self.installed / "retained-local-file.txt"
        retained.write_text("keep", encoding="utf-8")
        foreign = {"type": "command", "command": "echo keep me"}
        other_install = {"type": "command", "command": installer._hook_command(self.root / "other" / "codemap.py", "stop")}
        settings = self.load_settings()
        settings["hooks"]["Stop"][0]["matcher"] = "custom"
        settings["hooks"]["Stop"][0]["hooks"] += [foreign, other_install]
        self.write_settings(settings)
        self.assertEqual(self.run_install("--no-auto"), 0)
        self.assertEqual(retained.read_text(), "keep")
        self.assertEqual(self.load_settings(), {"hooks": {"Stop": [{"matcher": "custom", "hooks": [foreign, other_install]}]}})
        self.assertEqual(self.run_install("--no-auto"), 0)

    def test_uninstall_removes_only_this_install_hooks(self):
        self.write_settings({"theme": "dark", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo keep"}]}]}})
        self.assertEqual(self.run_install("--auto"), 0)
        self.assertEqual(self.run_install("--uninstall"), 0)
        self.assertFalse(self.installed.exists())
        self.assertEqual(self.load_settings(), {"theme": "dark", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo keep"}]}]}})

    def test_malformed_settings_refused_before_install_or_uninstall(self):
        for raw in (b'{broken', b'[]', b'{"hooks": []}', b'{"hooks":{"Stop":{}}}'):
            with self.subTest(raw=raw):
                self.settings.parent.mkdir(parents=True, exist_ok=True)
                self.settings.write_bytes(raw)
                self.assertEqual(self.run_install("--auto"), 2)
                self.assertFalse(self.installed.exists())
                self.assertEqual(self.settings.read_bytes(), raw)
                self.assertEqual(self.run_install("--uninstall"), 2)
                self.assertEqual(self.settings.read_bytes(), raw)
        self.assertFalse(list(self.settings.parent.glob("*.codemap-backup-*.json")))

    def test_dry_run_never_creates_or_changes_files(self):
        self.assertEqual(self.run_install("--auto", "--dry-run"), 0)
        self.assertFalse((self.project / ".claude").exists())
        self.assertEqual(self.run_install("--auto"), 0)
        original = self.settings.read_bytes()
        self.assertEqual(self.run_install("--uninstall", "--dry-run"), 0)
        self.assertEqual(self.settings.read_bytes(), original)
        self.assertTrue(self.script.is_file())
        self.assertEqual(self.run_install("--no-auto", "--dry-run"), 0)
        self.assertEqual(self.settings.read_bytes(), original)

    def test_user_install_uses_mock_home_settings_json(self):
        self.assertEqual(self.run_install("--auto", project=False), 0)
        settings = self.home / ".claude" / "settings.json"
        self.assertTrue(settings.is_file())
        self.assertFalse((self.home / ".claude" / "settings.local.json").exists())
        self.assertTrue((self.home / ".claude" / "skills" / "codemap" / "scripts" / "codemap.py").is_file())
        self.assertFalse(self.settings.exists())

    def test_command_recognition_is_path_scoped_and_rejects_extra_code(self):
        for platform in ("win32", "posix"):
            command = installer._hook_command(self.script, "stop", platform=platform)
            hook = {"type": "command", "command": command}
            self.assertTrue(installer._owned_hook(hook, self.script))
            self.assertFalse(installer._owned_hook(hook, self.root / "different.py"))
            self.assertFalse(installer._owned_hook({**hook, "command": command + " && echo unrelated"}, self.script))
        unix = installer._hook_command(self.root / "space ' and $ dollar" / "codemap.py", "start", platform="posix")
        self.assertEqual(shlex.split(unix, comments=True), [sys.executable, str(self.root / "space ' and $ dollar" / "codemap.py"), "hook", "start"])

    @unittest.skipUnless(sys.platform == "win32", "Windows short-path aliases")
    def test_short_alias_hooks_are_owned_and_reinstall_without_duplicates(self):
        import ctypes

        self.assertEqual(self.run_install("--auto"), 0)
        get_short = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        get_short.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint)
        get_short.restype = ctypes.c_uint
        size = get_short(str(self.script), None, 0)
        if not size:
            self.skipTest("GetShortPathNameW is unavailable for this fixture")
        buffer = ctypes.create_unicode_buffer(size)
        if not get_short(str(self.script), buffer, size):
            self.skipTest("GetShortPathNameW could not read this fixture")
        alias = Path(buffer.value)
        if os.path.normcase(str(alias)) == os.path.normcase(str(self.script)):
            self.skipTest("8.3 aliases are disabled for this fixture volume")
        self.assertEqual(alias.resolve(), self.script.resolve())
        settings = self.load_settings()
        for event, (action, _) in installer.AUTO_EVENTS.items():
            command = installer._hook_command(alias, action)
            handler = settings["hooks"][event][0]["hooks"][0]
            handler["command"] = command
            self.assertTrue(installer._owned_hook(handler, self.script))
            self.assertFalse(installer._owned_hook(handler, self.root / "unrelated" / "codemap.py"))
            self.assertFalse(installer._owned_hook({**handler, "command": command + " && echo unrelated"}, self.script))
        foreign = {"type": "command", "command": "echo unrelated fixture", "timeout": 7}
        settings["hooks"]["Stop"][0]["hooks"].append(foreign)
        self.write_settings(settings)
        self.assertEqual(self.run_install("--auto"), 0)
        updated = self.load_settings()
        self.assertEqual(len(self.own_hooks(updated)), 3)
        self.assertEqual(sum(len(entry["hooks"]) for entries in updated["hooks"].values() for entry in entries), 4)
        self.assertEqual(updated["hooks"]["Stop"][0]["hooks"], [foreign])
        original = self.settings.read_bytes()
        self.assertEqual(self.run_install("--auto"), 0)
        self.assertEqual(self.settings.read_bytes(), original)

    def test_failed_atomic_replace_preserves_original_and_backup(self):
        original = self.write_settings({"theme": "dark"})
        before, updated = installer._settings_plan(self.settings, self.script, True)
        replace = installer.os.replace
        def fail_current(source, target):
            if Path(target) == self.settings:
                raise OSError("fixture replacement failure")
            return replace(source, target)
        with mock.patch.object(installer.os, "replace", side_effect=fail_current):
            with self.assertRaises(OSError):
                installer._write_settings(self.settings, before, updated)
        self.assertEqual(self.settings.read_bytes(), original)
        backups = list(self.settings.parent.glob("*.codemap-backup-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertFalse(list(self.settings.parent.glob("*.tmp")))

    def test_concurrent_settings_change_is_not_overwritten(self):
        self.write_settings({"theme": "dark"})
        before, updated = installer._settings_plan(self.settings, self.script, True)
        changed = self.write_settings({"theme": "light"})
        with self.assertRaisesRegex(ValueError, "changed during installation"):
            installer._write_settings(self.settings, before, updated)
        self.assertEqual(self.settings.read_bytes(), changed)

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("powershell.exe"), "Windows PowerShell transport")
    def test_generated_windows_hook_stdin_stdout_roundtrip_with_hostile_path(self):
        self.project = self.root / "task's & $(Write-Output injected); `draft 日本"
        self.project.mkdir()
        self.assertEqual(self.run_install("--auto"), 0)
        payload = {"session_id": "fixture-only", "cwd": str(self.project), "prompt": "naive café 日本\n' $() & ; ` \""}
        settings = self.load_settings()
        for event, action in (("UserPromptSubmit", "start"), ("Stop", "stop"), ("SessionEnd", "abort")):
            with self.subTest(event=event):
                command = settings["hooks"][event][0]["hooks"][0]["command"]
                self.assertIn("-WindowStyle Hidden -EncodedCommand ", command)
                # This is the actual installed command, not a reconstructed Python invocation.
                process = subprocess.run(command, input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                         shell=True, capture_output=True, timeout=30)
                self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", "replace"))
                response = json.loads(process.stdout.decode("utf-8-sig"))
                self.assertEqual(response, {"decision": "block", "reason": "fixture only", "action": action, "payload": payload})
                self.assertTrue(installer._owned_hook({"type": "command", "command": command}, self.script))

    @unittest.skipUnless(sys.platform == "win32" and Path("C:/Program Files/Git/bin/bash.exe").is_file(), "Git Bash transport")
    def test_generated_windows_command_is_compatible_with_git_bash(self):
        self.project = self.root / "Bash task's & $(echo injected); 日本"
        self.project.mkdir()
        self.assertEqual(self.run_install("--auto"), 0)
        command = self.load_settings()["hooks"]["Stop"][0]["hooks"][0]["command"]
        payload = {"session_id": "fixture-only", "prompt": "café 日本", "cwd": str(self.project)}
        result = subprocess.run(["C:/Program Files/Git/bin/bash.exe", "-c", command],
                                input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(result.stdout.decode("utf-8-sig")),
                         {"decision": "block", "reason": "fixture only", "action": "stop", "payload": payload})

    @unittest.skipUnless(os.name != "nt", "POSIX shell transport")
    def test_generated_unix_hook_roundtrip(self):
        self.project = self.root / "task's & $(echo injected); draft"
        self.project.mkdir()
        self.assertEqual(self.run_install("--auto"), 0)
        command = self.load_settings()["hooks"]["Stop"][0]["hooks"][0]["command"]
        payload = {"prompt": "Unicode café 日本 and $()", "cwd": str(self.project)}
        result = subprocess.run(command, input=json.dumps(payload).encode(), shell=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["payload"], payload)


if __name__ == "__main__":
    unittest.main()
