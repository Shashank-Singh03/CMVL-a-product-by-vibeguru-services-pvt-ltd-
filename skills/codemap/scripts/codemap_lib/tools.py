"""Locate optional external tools (browser, ffmpeg, text-to-speech) on Windows, macOS and Linux."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def find_browser() -> str | None:
    env = os.environ.get("CODEMAP_BROWSER")
    if env and Path(env).exists():
        return env
    candidates = []
    if sys.platform == "win32":
        for base in filter(None, [os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")]):
            candidates += [Path(base, "Google/Chrome/Application/chrome.exe"),
                           Path(base, "Microsoft/Edge/Application/msedge.exe"),
                           Path(base, "Chromium/Application/chrome.exe"),
                           Path(base, "BraveSoftware/Brave-Browser/Application/brave.exe")]
    elif sys.platform == "darwin":
        candidates += [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                       Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
                       Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                       Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")]
    for c in candidates:
        if c.exists():
            return str(c)
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "msedge", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def find_ffmpeg() -> str | None:
    env = os.environ.get("CODEMAP_FFMPEG")
    if env and Path(env).exists():
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:  # pip install imageio-ffmpeg ships a static ffmpeg for every OS
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def find_tts() -> tuple:
    """Returns (engine name, description) for the built-in OS voice, or (None, hint)."""
    if sys.platform == "win32":
        if shutil.which("powershell"):
            return "sapi", "Windows SAPI voices (via PowerShell)"
        return None, "PowerShell not found"
    if sys.platform == "darwin":
        if shutil.which("say"):
            return "say", "macOS `say`"
        return None, "`say` not found"
    for name in ("espeak-ng", "espeak"):
        if shutil.which(name):
            return name, f"{name}"
    return None, "install espeak-ng (apt install espeak-ng) for narration"


def install_hint(tool: str) -> str:
    if tool == "ffmpeg":
        return {"win32": "winget install Gyan.FFmpeg   (or: pip install imageio-ffmpeg)",
                "darwin": "brew install ffmpeg   (or: pip install imageio-ffmpeg)"}.get(
            sys.platform, "sudo apt install ffmpeg   (or: pip install imageio-ffmpeg)")
    if tool == "browser":
        return "install Google Chrome, Microsoft Edge or Chromium (or set CODEMAP_BROWSER=/path/to/browser)"
    return ""


def doctor() -> list:
    """Environment report as printable lines."""
    lines = [f"python   {sys.version.split()[0]}  ok"]
    git = shutil.which("git")
    if git:
        try:
            ver = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip()
        except OSError:
            ver = "git"
        lines.append(f"git      {ver.replace('git version ', '')}  ok (diff mode)")
    else:
        lines.append("git      missing -> diff mode needs git (or use `codemap mark` snapshots)")
    browser = find_browser()
    lines.append(f"browser  {'ok  ' + browser if browser else 'missing -> ' + install_hint('browser')}  (MP4 frames)")
    ffmpeg = find_ffmpeg()
    lines.append(f"ffmpeg   {'ok  ' + ffmpeg if ffmpeg else 'missing -> ' + install_hint('ffmpeg')}  (MP4 encode)")
    engine, desc = find_tts()
    lines.append(f"voice    {'ok  ' + desc if engine else 'missing -> ' + desc}  (MP4 narration)")
    from .video import piper_dir, piper_python, piper_ready
    if piper_ready():
        lines.append(f"piper    ok  neural voice ready ({piper_dir()})  (used automatically)")
    elif piper_python():
        lines.append("piper    installed; its voice (~63 MB) downloads on the first `codemap video --tts piper`")
    else:
        lines.append("piper    optional -> pip install piper-tts  (natural-sounding free voice)")
    lines.append("ready:   analyze + Markdown + HTML explorer + in-browser walkthrough (no extra installs)")
    if browser and ffmpeg:
        lines.append("ready:   narrated MP4 export" if (engine or piper_ready())
                     else "ready:   silent MP4 export (--tts none); narrated export still needs a voice")
    else:
        lines.append("not yet: MP4 export (needs browser + ffmpeg)")
    return lines
