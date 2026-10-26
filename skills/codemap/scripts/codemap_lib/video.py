"""MP4 export: narration audio + one still per beat (headless Chromium) + ffmpeg.

Explainer scenes are mostly static while the narrator talks, so each beat is a single
still held for exactly as long as its audio. That makes rendering about as fast as
taking screenshots, and unchanged beats are reused from a cache.

Voices (`--tts`):
  auto   Piper if it is installed and its voice is downloaded, otherwise the OS voice (default)
  piper  Piper neural TTS (pip install piper-tts); the voice model downloads once (~63 MB)
  os     Windows SAPI, macOS `say`, Linux espeak-ng
  cmd:…  any command, e.g. "cmd:my-tts --in {text} --out {out}" ({text} = UTF-8 text file, {out} = WAV)
  none   silent video (captions are still on screen)

Audio is loudness-normalized (EBU R128, -16 LUFS) and encoded as 48 kHz stereo AAC so it plays
loud and clear everywhere. The same narration is embedded into codemap.html as small MP3 clips,
so the in-browser walkthrough has real audio even where the browser has no speech voices.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue

from .story import est_seconds
from .tools import find_browser, find_ffmpeg, find_tts, install_hint
from .util import sha1, write_json

PAD = 0.45           # silence after each beat (seconds)
SAMPLE_RATE = 22050
PIPER_VOICE = "en_US-ljspeech-medium"   # trained on LJ Speech, which is public domain (no use restrictions)
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"


class VideoError(RuntimeError):
    pass


# ---------------------------------------------------------------- voices

def _sapi(jobs: list, voice: str | None, rate: int, work: Path) -> None:
    job_file = work / "tts_jobs.json"
    write_json(job_file, [{"text": t, "out": str(p)} for t, p in jobs])
    quoted = str(job_file).replace("'", "''")
    voice_line = f"$s.SelectVoice('{voice.replace(chr(39), chr(39) * 2)}')" if voice else ""
    script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$jobs = Get-Content -Raw -Encoding UTF8 -LiteralPath '{quoted}' | ConvertFrom-Json
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
{voice_line}
$s.Rate = {rate}
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo({SAMPLE_RATE}, 16, 1)
foreach ($j in $jobs) {{ $s.SetOutputToWaveFile($j.out, $fmt); $s.Speak($j.text) }}
$s.SetOutputToNull()
$s.Dispose()
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise VideoError("Windows speech failed: " + (proc.stderr.strip()[-400:] or "unknown error"))


def _say(jobs: list, voice: str | None, rate: int, work: Path) -> None:
    for text, out in jobs:
        txt = work / (out.stem + ".txt")
        txt.write_text(text, encoding="utf-8")
        cmd = ["say", "-o", str(out), "--file-format=WAVE", f"--data-format=LEI16@{SAMPLE_RATE}", "-f", str(txt)]
        if voice:
            cmd += ["-v", voice]
        if rate:
            cmd += ["-r", str(175 + rate * 15)]
        subprocess.run(cmd, check=True, capture_output=True)


def _espeak(engine: str, jobs: list, voice: str | None, rate: int, work: Path) -> None:
    for text, out in jobs:
        txt = work / (out.stem + ".txt")
        txt.write_text(text, encoding="utf-8")
        cmd = [engine, "-w", str(out), "-f", str(txt), "-s", str(165 + rate * 15)]
        if voice:
            cmd += ["-v", voice]
        subprocess.run(cmd, check=True, capture_output=True)


def _command(template: str, jobs: list, work: Path) -> None:
    for text, out in jobs:
        txt = work / (out.stem + ".txt")
        txt.write_text(text, encoding="utf-8")
        proc = subprocess.run(template.format(text=str(txt), out=str(out)), shell=True, capture_output=True, text=True)
        if proc.returncode != 0 or not out.exists():
            raise VideoError(f"voice command failed: {proc.stderr.strip()[-300:] or template}")


PIPER_HELPER = '''import json, sys, wave
from piper import PiperVoice
try:
    from piper import SynthesisConfig
except ImportError:
    SynthesisConfig = None
jobs = json.load(open(sys.argv[1], encoding="utf-8"))
voice = PiperVoice.load(sys.argv[2])
cfg = SynthesisConfig(length_scale=float(sys.argv[3])) if SynthesisConfig else None
for job in jobs:
    with wave.open(job["out"], "wb") as wf:
        if cfg is not None:
            voice.synthesize_wav(job["text"], wf, syn_config=cfg)
        else:
            voice.synthesize_wav(job["text"], wf)
'''


def piper_python() -> str | None:
    """A Python interpreter that can `import piper`."""
    try:
        spec = importlib.util.find_spec("piper")
    except (ImportError, ValueError):
        spec = None
    if spec is not None and spec.origin not in (None, "namespace"):   # a bare `piper/` folder is not Piper
        return sys.executable
    env = os.environ.get("CODEMAP_PIPER_PYTHON")
    if env and Path(env).exists():
        return env
    exe = shutil.which("piper")
    if exe:
        sibling = Path(exe).with_name("python.exe" if os.name == "nt" else "python")
        if sibling.exists():
            return str(sibling)
    return None


def piper_dir() -> Path:
    return Path(os.environ.get("CODEMAP_PIPER_DIR") or Path.home() / ".cache" / "codemap" / "piper")


def piper_ready(voice: str | None = None) -> bool:
    return piper_python() is not None and (piper_dir() / f"{voice or PIPER_VOICE}.onnx").exists()


def _piper(jobs: list, voice: str | None, rate: int, work: Path, log) -> None:
    py = piper_python()
    if not py:
        raise VideoError("Piper is not installed: pip install piper-tts (or use --tts os)")
    name = voice or PIPER_VOICE
    home = piper_dir()
    home.mkdir(parents=True, exist_ok=True)
    model = home / f"{name}.onnx"
    # Piper runs with its own folder as working directory, so nothing in the user's project can shadow its modules.
    if not model.exists():
        log(f"  downloading Piper voice {name} (~63 MB, once) ...")
        proc = subprocess.run([py, "-m", "piper.download_voices", name, "--data-dir", str(home)],
                              capture_output=True, text=True, cwd=str(home))
        if proc.returncode != 0 or not model.exists():
            raise VideoError("could not download the Piper voice: " + proc.stderr.strip()[-300:])
    job_file = work / "piper_jobs.json"
    write_json(job_file, [{"text": t, "out": str(p.resolve())} for t, p in jobs])
    length = max(0.6, min(1.5, 1.0 - rate * 0.08))
    proc = subprocess.run([py, "-c", PIPER_HELPER, str(job_file.resolve()), str(model), str(length)],
                          capture_output=True, text=True, cwd=str(home))
    if proc.returncode != 0:
        raise VideoError("Piper failed: " + proc.stderr.strip()[-400:])


def resolve_engine(tts: str, voice: str | None) -> tuple:
    """(engine id, description) for the requested --tts value."""
    if tts.startswith("cmd:"):
        return tts, "custom command"
    if tts == "none":
        return "none", "silent"
    if tts == "piper" or (tts == "auto" and piper_ready(voice)):
        return "piper", f"Piper ({voice or PIPER_VOICE})"
    engine, desc = find_tts()
    if not engine:
        raise VideoError(f"no system voice found: {desc} (or use --tts none, or pip install piper-tts)")
    return engine, desc


def synthesize(beats: list, work: Path, tts: str, voice: str | None, rate: int, log=print) -> tuple:
    """Returns (one WAV path or None per beat, engine description); reuses cached audio."""
    engine, desc = resolve_engine(tts, voice)
    if engine == "none":
        return [None] * len(beats), desc
    paths, todo = [], []
    for b in beats:
        key = sha1(f"{engine}|{voice}|{rate}|{b['text']}")[:16]
        p = work / f"a_{key}.wav"
        paths.append(p)
        if not p.exists():
            todo.append((b["text"], p))
    if todo:
        if engine.startswith("cmd:"):
            _command(engine[4:], todo, work)
        elif engine == "piper":
            _piper(todo, voice, rate, work, log)
        elif engine == "sapi":
            _sapi(todo, voice, rate, work)
        elif engine == "say":
            _say(todo, voice, rate, work)
        else:
            _espeak(engine, todo, voice, rate, work)
    return paths, desc


def _duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _uniform(ffmpeg: str, paths: list, work: Path) -> list:
    """Make every clip the same WAV format so they can be joined byte-wise."""
    fmts = set()
    for p in paths:
        with wave.open(str(p), "rb") as w:
            fmts.add((w.getnchannels(), w.getsampwidth(), w.getframerate()))
    if len(fmts) <= 1:
        return paths
    out = []
    for p in paths:
        q = work / (p.stem + "_n.wav")
        if not q.exists():
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(p), "-ac", "1", "-ar", str(SAMPLE_RATE),
                            "-sample_fmt", "s16", str(q)], check=True)
        out.append(q)
    return out


def _join_audio(paths: list, durations: list, out: Path) -> None:
    with wave.open(str(paths[0]), "rb") as first:
        params = first.getparams()
    frame_bytes = params.sampwidth * params.nchannels
    with wave.open(str(out), "wb") as dst:
        dst.setnchannels(params.nchannels)
        dst.setsampwidth(params.sampwidth)
        dst.setframerate(params.framerate)
        for p, total in zip(paths, durations):
            with wave.open(str(p), "rb") as src:
                frames = src.readframes(src.getnframes())
            need = int(round(total * params.framerate)) * frame_bytes
            dst.writeframes(frames[:need] + b"\x00" * max(0, need - len(frames)))


def _clips_for_html(ffmpeg: str, beats: list, wavs: list, work: Path, jobs: int) -> None:
    """Small normalized MP3 per beat, indexed by narration text, for embedding in codemap.html."""
    index = {}
    todo = []
    for b, wav in zip(beats, wavs):
        mp3 = wav.with_suffix(".mp3")
        index[sha1(b["text"])[:16]] = mp3.name
        if not mp3.exists():
            todo.append((wav, mp3))

    def encode(item):
        src, dst = item
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(src), "-af", LOUDNORM, "-ar", "24000", "-ac", "1",
                        "-c:a", "libmp3lame", "-b:a", "48k", str(dst)], capture_output=True)

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        list(pool.map(encode, todo))
    write_json(work / "narration.json", index)


def embedded_audio(out_dir: Path, beats: list) -> dict:
    """{beat sid: data URI} for beats whose current text has a recorded clip (used by render)."""
    work = out_dir / "video"
    index = {}
    try:
        index = json.loads((work / "narration.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for b in beats:
        name = index.get(sha1(b.get("text", ""))[:16])
        if name and (work / name).exists():
            out[b["sid"]] = "data:audio/mpeg;base64," + base64.b64encode((work / name).read_bytes()).decode("ascii")
    return out


# ---------------------------------------------------------------- stills

def _still_keys(ir: dict, html_template_hash: str) -> list:
    """Cache key per beat: everything that can change what the frame shows."""
    nodes, views = ir["nodes"], {v["id"]: v for v in ir["views"]}
    total = len(ir["beats"])
    keys = []
    for i, b in enumerate(ir["beats"]):
        view = views.get(b.get("view")) or {}
        refs = set(view.get("children", [])) | set(view.get("ghosts", [])) | set(b.get("focus", []))
        refs.add(b.get("node") or "")
        if view:
            refs.add(view["node"])
        if b["kind"] == "outro":
            refs = set(nodes)
        payload = {
            "b": {k: v for k, v in b.items() if k != "audio"}, "i": i, "n": total, "v": view, "scope": ir["scope"],
            "why": ir.get("why"), "repo": ir["repo"], "risks": ir.get("risks") if b["kind"] == "risks" else None,
            "nodes": {r: {k: nodes[r].get(k) for k in ("title", "name", "status", "add", "del", "sum", "kind", "file",
                                                       "line", "end", "diff", "view", "sub", "risk")}
                      for r in sorted(refs) if r in nodes},
        }
        keys.append(sha1(html_template_hash + json.dumps(payload, sort_keys=True, default=str))[:16])
    return keys


def capture_stills(html: Path, ir: dict, work: Path, jobs: int = 4, template_hash: str = "") -> list:
    browser = find_browser()
    if not browser:
        raise VideoError("no Chrome/Edge/Chromium found: " + install_hint("browser"))
    keys = _still_keys(ir, template_hash)
    paths = [work / f"s_{k}.png" for k in keys]
    todo = [(i, p) for i, p in enumerate(paths) if not p.exists()]
    if not todo:
        return paths
    profiles: Queue = Queue()
    tmp_root = Path(tempfile.mkdtemp(prefix="codemap-browser-"))
    for k in range(max(1, jobs)):
        profiles.put(tmp_root / f"p{k}")
    url = html.resolve().as_uri()

    def shoot(item):
        i, out = item
        profile = profiles.get()
        try:
            cmd = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio", "--no-first-run",
                   "--no-default-browser-check", "--disable-background-networking", "--disable-component-update",
                   "--disable-extensions", "--force-device-scale-factor=1", f"--user-data-dir={profile}",
                   "--window-size=1920,1080", "--virtual-time-budget=2500", f"--screenshot={out}", f"{url}#still={i}"]
            subprocess.run(cmd, capture_output=True, timeout=90)
        finally:
            profiles.put(profile)
        if not out.exists():
            raise VideoError(f"browser did not produce a frame for beat {i + 1}")

    try:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            list(pool.map(shoot, todo))
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    return paths


# ---------------------------------------------------------------- assemble

def make_video(ir: dict, html: Path, out_dir: Path, tts: str = "auto", voice: str | None = None,
               rate: int = 0, res: int = 1080, jobs: int = 4, log=print, template_hash: str = "",
               fps: int = 15, webm: bool = False) -> Path:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise VideoError("ffmpeg not found: " + install_hint("ffmpeg"))
    beats = ir["beats"]
    if not beats:
        raise VideoError("nothing to narrate (no beats)")
    work = out_dir / "video"
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    wavs, voice_desc = synthesize(beats, work, tts, voice, rate, log)
    t1 = time.time()
    if wavs[0] is not None:
        wavs = _uniform(ffmpeg, wavs, work)
        durations = [_duration(p) + PAD for p in wavs]
        _clips_for_html(ffmpeg, beats, wavs, work, jobs)
    else:
        durations = [est_seconds(b["text"]) + PAD for b in beats]
    log(f"  narration: {len(beats)} beats, {sum(durations):.0f}s of audio, {voice_desc} ({time.time() - t0:.1f}s)")

    stills = capture_stills(html, ir, work, jobs, template_hash)
    t2 = time.time()
    log(f"  frames: {len(stills)} stills ({t2 - t1:.1f}s)")

    frames_txt = work / "frames.txt"
    lines = []
    for p, d in zip(stills, durations):
        lines.append(f"file '{p.resolve().as_posix()}'")
        lines.append(f"duration {d:.3f}")
    lines.append(f"file '{stills[-1].resolve().as_posix()}'")
    frames_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")

    out = out_dir / "codemap.mp4"
    width, height = (1920, 1080) if res >= 1080 else (1280, 720)
    audio = work / "narration.wav" if wavs[0] is not None else None
    # stills and clips are content-addressed, so this key changes exactly when the video would
    key = sha1("\n".join(lines) + "|" + "|".join(p.name for p in wavs if p) + f"|{width}|{fps}|{LOUDNORM}")[:16]
    targets = [(out, "mp4.key", MP4_ARGS, fps, f"{width}x{height} H.264 + " + ("AAC 48 kHz stereo" if audio else "no audio"))]
    if webm:   # VS Code's player takes WebM with VP8 only; scenes are stills, so 5 fps looks the same
        targets.append((out_dir / "codemap.webm", "webm.key", WEBM_ARGS, min(fps, 5), "VP8 + Opus for VS Code"))
    joined = False
    for target, stamp, args, rate, what in targets:
        if target.exists() and _stamp(work / stamp) == key + args[1]:
            log(f"  {target.suffix[1:]}: nothing changed, kept the existing file")
            continue
        if audio and not joined:
            _join_audio(wavs, durations, audio)
            joined = True
        t3 = time.time()
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(frames_txt)]
        if audio:
            cmd += ["-i", str(audio)]
        cmd += ["-vf", f"scale={width}:{height}:flags=lanczos,fps={rate},format=yuv420p", *args[0]]
        if audio:
            cmd += ["-af", LOUDNORM, "-ar", "48000", "-ac", "2", *args[2], "-shortest"]
        proc = subprocess.run(cmd + [str(target)], capture_output=True, text=True)
        if proc.returncode != 0:
            raise VideoError(f"ffmpeg failed writing {target.name}: " + proc.stderr.strip()[-600:])
        (work / stamp).write_text(key + args[1], encoding="utf-8")
        log(f"  {target.suffix[1:]}: {what} ({time.time() - t3:.1f}s)")
    return out


# (video options, stamp suffix, audio options) per container
MP4_ARGS = (["-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "20", "-movflags", "+faststart"],
            "h264", ["-c:a", "aac", "-b:a", "192k"])
WEBM_ARGS = (["-c:v", "libvpx", "-deadline", "good", "-cpu-used", "5", "-threads", "4", "-crf", "10", "-b:v", "1M",
              "-g", "150"], "vp8", ["-c:a", "libopus", "-b:a", "96k"])


def _stamp(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
