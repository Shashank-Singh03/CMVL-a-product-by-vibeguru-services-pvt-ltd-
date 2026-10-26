"""Generated hook commands shared by installation and runtime scope election."""
from __future__ import annotations

import base64
import json
import os
import re
import shlex
import sys
from pathlib import Path

AUTO_MARKER = "codemap-auto-v1"


def ps_literal(value):
    return "'" + value.replace("'", "''") + "'"


def powershell_script(python, script, action):
    return "\n".join((
        "# " + AUTO_MARKER,
        "# installed-script: " + json.dumps(script, ensure_ascii=True),
        "$OutputEncoding = [System.Text.UTF8Encoding]::new($false)",
        "[Console]::InputEncoding = $OutputEncoding",
        "[Console]::OutputEncoding = $OutputEncoding",
        "$env:PYTHONIOENCODING = 'utf-8'",
        "$codemapHookInput = [Console]::In.ReadToEnd()",
        f"$codemapHookInput | & {ps_literal(python)} {ps_literal(script)} hook {action}",
        "if ($null -eq $LASTEXITCODE) { exit 1 }",
        "exit $LASTEXITCODE",
    ))


def hook_command(script, action, *, platform=None, python=None):
    python, script = str(python or sys.executable), str(script)
    if (platform or sys.platform) == "win32":
        encoded = base64.b64encode(powershell_script(python, script, action).encode("utf-16-le")).decode("ascii")
        return "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand " + encoded
    return " ".join(shlex.quote(value) for value in (python, script, "hook", action)) + " # " + AUTO_MARKER


def _same_path(first, second):
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def owned_hook(item, script, action=None):
    if not isinstance(item, dict) or item.get("type") != "command" or not isinstance(item.get("command"), str):
        return False
    command = item["command"]
    match = re.fullmatch(r"powershell\.exe -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand ([A-Za-z0-9+/=]+)", command)
    if match:
        try:
            decoded = base64.b64decode(match[1], validate=True).decode("utf-16-le")
        except (ValueError, UnicodeError):
            return False
        invoke = re.search(r"^\$codemapHookInput \| & '((?:[^']|'')*)' '((?:[^']|'')*)' hook (start|stop|abort)$", decoded, re.M)
        if not invoke:
            return False
        python, actual, actual_action = invoke[1].replace("''", "'"), invoke[2].replace("''", "'"), invoke[3]
        return (_same_path(actual, script) and (action is None or action == actual_action)
                and decoded == powershell_script(python, actual, actual_action))
    if not command.endswith(" # " + AUTO_MARKER):
        return False
    try:
        parts = shlex.split(command, comments=True)
    except ValueError:
        return False
    return (len(parts) == 4 and parts[2] == "hook" and parts[3] in ("start", "stop", "abort")
            and (action is None or action == parts[3]) and _same_path(parts[1], script)
            and command == hook_command(Path(parts[1]), parts[3], platform="posix", python=parts[0]))
