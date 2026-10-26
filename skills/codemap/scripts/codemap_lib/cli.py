"""Command line for maps, walkthroughs, task capture and the local dashboard."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import VERSION
from .util import setup_stdout

EPILOG = """examples:
  codemap analyze                    explain uncommitted changes (or the last commit)
  codemap analyze --base main        explain this branch vs main
  codemap analyze --repo             map the whole repository (add --audit to risk-check every file)
  codemap analyze --tier full        also write a worksheet for an AI agent to name chunks
  codemap analyze --repo --review    ask your agent to check the structure against source
  codemap review                    validate review.json and rebuild the graph
  codemap render                     merge .codemap/labels.json into the outputs
  codemap video                      narrated MP4 (needs ffmpeg + Chrome/Edge); --webm for VS Code
  codemap mark                       remember "now" so a later analyze explains only newer edits
  codemap demo                       try it on a built-in sample project
  codemap serve --open                open this project's local dashboard with task controls
"""


def _tts_value(value: str) -> str:
    if value in ("auto", "piper", "os", "none") or value.startswith("cmd:"):
        return value
    raise argparse.ArgumentTypeError("use auto, piper, os, none or cmd:<command>")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="codemap", description="Layered, human-sized maps of code and code changes.",
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"codemap {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="parse, build the layered map, render outputs (zero tokens)")
    a.add_argument("path", nargs="?", default=".", help="project folder or sub-folder (default: .)")
    a.add_argument("--base", help="git ref to compare against (branch, tag, commit)")
    a.add_argument("--repo", action="store_true", help="map the whole repository instead of changes")
    a.add_argument("--tier", choices=["free", "lite", "full", "video"],
                   help="free = no LLM; lite = name unclear chunks; full = all chunks + why; video = full + narration")
    a.add_argument("--budget", type=int, help="max worksheet tokens (default: lite 3000, full 8000, video 9000)")
    a.add_argument("--minutes", type=float, default=2.0, help="target walkthrough length (default 2)")
    t = a.add_mutually_exclusive_group()
    t.add_argument("--tests", dest="tests", action="store_true", default=None, help="include test files")
    t.add_argument("--no-tests", dest="tests", action="store_false", help="exclude test files")
    a.add_argument("--audit", action="store_true", help="with --repo: risk-check eligible text, including config and docs")
    review_options = a.add_mutually_exclusive_group()
    review_options.add_argument("--review", dest="structural_review", action="store_true", default=None,
                                help="write a source-backed structural review request for your agent (default for non-free tiers)")
    review_options.add_argument("--no-review", dest="structural_review", action="store_false",
                                help="skip the structural review request")
    a.add_argument("--out", help="output folder (default: <repo>/.codemap)")

    check = sub.add_parser("review", help="validate the agent's source-backed graph corrections and rebuild the map")
    check.add_argument("path", nargs="?", default=".")
    check.add_argument("--out")
    check.add_argument("--input", help="agent correction JSON (default: <out>/review.json)")

    r = sub.add_parser("render", help="merge labels.json and rebuild codemap.md / codemap.html")
    r.add_argument("path", nargs="?", default=".")
    r.add_argument("--out")

    v = sub.add_parser("video", help="export the walkthrough as an MP4")
    v.add_argument("path", nargs="?", default=".")
    v.add_argument("--out")
    v.add_argument("--tts", default="auto", type=_tts_value,
                   help="auto (Piper if installed, else OS voice) | piper | os | none | 'cmd:<command with {text} and {out}>'")
    v.add_argument("--voice", help="Piper voice (e.g. en_US-amy-medium) or system voice (e.g. 'Microsoft Zira Desktop')")
    v.add_argument("--webm", action="store_true", help="also write codemap.webm (plays with sound inside VS Code)")
    v.add_argument("--rate", type=int, default=0, help="speech rate -5..5 (default 0)")
    v.add_argument("--res", type=int, choices=[720, 1080], default=1080)
    v.add_argument("--jobs", type=int, default=4, help="parallel browser processes for frames")
    v.add_argument("--fps", type=int, default=15, help="frame rate; scenes are stills, so 15 looks the same as 30")

    m = sub.add_parser("mark", help="record a baseline: later analyses explain only what changed after it")
    m.add_argument("path", nargs="?", default=".")
    m.add_argument("--clear", action="store_true", help="forget the baseline")
    m.add_argument("--out")

    d = sub.add_parser("demo", help="build a sample project, simulate an AI change, and map it")
    d.add_argument("--dir", help="where to create the demo (default: <temp>/codemap-demo)")
    d.add_argument("--labeled", action="store_true", help="apply sample AI labels (shows what --tier full looks like)")
    d.add_argument("--video", action="store_true", help="also export the MP4")
    d.add_argument("--webm", action="store_true", help="with --video: also write a WebM (plays with sound in VS Code)")

    sub.add_parser("doctor", help="check what this machine can do (git, browser, ffmpeg, voice)")

    server = sub.add_parser("serve", help="serve this project's dashboard with local task controls and settings")
    server.add_argument("path", nargs="?", default=".", help="project folder (default: .)")
    server.add_argument("--port", type=int, default=8765, help="loopback port, or 0 to select an available port (default: 8765)")
    server.add_argument("--open", action="store_true", help="open the local dashboard in your browser")

    auto = sub.add_parser("auto", help="enable, pause or configure automatic walkthroughs in this project")
    auto.add_argument("action", nargs="?", choices=["on", "off", "status"], default="status")
    auto.add_argument("path", nargs="?", default=".")
    auto.add_argument("--video", choices=["on", "off"])
    auto.add_argument("--webm", choices=["on", "off"])
    auto.add_argument("--minutes", type=float)
    auto.add_argument("--budget", type=int)
    auto.add_argument("--timeout", type=int, help="seconds per background analysis, review, render or video stage")
    auto.add_argument("--tts", choices=["auto", "os", "piper", "none"])
    auto.add_argument("--res", type=int, choices=[720, 1080])
    auto.add_argument("--jobs", type=int, choices=[1, 2, 3, 4])

    hook = sub.add_parser("hook", help="Claude hook entry point (reads event JSON on stdin)")
    hook.add_argument("event", choices=["start", "stop", "abort"])

    task = sub.add_parser("task", help="inspect, finish, retry or cancel a captured coding task")
    task.add_argument("action", choices=["prepare", "review", "finish", "status", "retry", "cancel", "history", "_worker", "_stage"])
    task.add_argument("path", nargs="?", default=".")
    task.add_argument("--id", dest="task_id")
    task.add_argument("--wait", type=float, default=0, help="wait up to 180 seconds for analysis, review or map rendering")
    task.add_argument("--stage", choices=["analysis", "review", "render", "video"])
    task.add_argument("--token", help=argparse.SUPPRESS)
    task.add_argument("--open", action="store_true", help="open the local history page")
    return p


def main(argv=None) -> int:
    setup_stdout()
    args = _parser().parse_args(argv)
    from .pipeline import CodemapError, analyze, mark, out_dir_for, render, review, video
    try:
        if args.cmd == "serve":
            if not 0 <= args.port <= 65535:
                raise CodemapError("choose a server port from 0 to 65535")
            from .server import serve
            serve(args.path, port=args.port, open_browser=args.open)
            return 0
        elif args.cmd == "hook":
            from . import automatic
            try:
                raw = sys.stdin.read(1_048_577)
                if len(raw) > 1_048_576:
                    raise ValueError("oversized event")
                response = automatic.hook(args.event, json.loads(raw))
            except Exception:
                # Fail open for the coding session and never echo raw event,
                # source, prompt or exception text into its transcript.
                response = {"systemMessage": "Codemap automatic walkthrough was not completed. Check local task history and doctor."}
            print(json.dumps(response, ensure_ascii=True))
            return 0
        elif args.cmd == "auto":
            from . import automatic
            values = {k: getattr(args, k) for k in ("minutes", "budget", "timeout", "tts", "res", "jobs")}
            values.update({k: None if getattr(args, k) is None else getattr(args, k) == "on" for k in ("video", "webm")})
            print(json.dumps(automatic.configure(args.path, args.action, **values), indent=2))
        elif args.cmd == "task":
            from . import automatic, tasks
            root = automatic.project_root(args.path)
            if args.action == "history":
                path = automatic._history(root)
                if args.open:
                    import webbrowser
                    webbrowser.open(path.resolve().as_uri())
                print(path)
                return 0
            if args.task_id is None:
                raise CodemapError("task --id is required")
            if args.action in ("_worker", "_stage"):
                if (args.stage is None or not isinstance(args.token, str) or len(args.token) != 32
                        or any(c not in "0123456789abcdef" for c in args.token)):
                    raise CodemapError("invalid background job identity")
                function = automatic.worker if args.action == "_worker" else automatic.stage_worker
                return function(root, args.task_id, args.stage, args.token)
            if args.action == "prepare":
                result = automatic.prepare(root, args.task_id, args.wait)
            elif args.action == "review":
                result = automatic.review_task(root, args.task_id, args.wait)
            elif args.action == "finish":
                result = automatic.finish(root, args.task_id, args.wait)
            elif args.action == "retry":
                result = automatic.retry(root, args.task_id, args.stage)
            elif args.action == "cancel":
                result = automatic.cancel(root, args.task_id)
            else:
                result = automatic.summary(automatic.refresh(root, args.task_id))
            print(json.dumps(result, indent=2))
            return 1 if result.get("status") in ("capture_failed", "analysis_failed", "review_failed", "render_failed", "video_failed") else 0
        elif args.cmd == "analyze":
            analyze(args.path, out=args.out, base=args.base, repo_mode=args.repo, tier=args.tier,
                    budget=args.budget, minutes=args.minutes, include_tests=args.tests, audit=args.audit,
                    structural_review=args.structural_review)
        elif args.cmd == "review":
            ir = review(out_dir_for(args.path, args.out), patch_path=args.input)
            return 2 if ir.get("review", {}).get("rejected") else 0
        elif args.cmd == "render":
            render(out_dir_for(args.path, args.out))
        elif args.cmd == "video":
            video(out_dir_for(args.path, args.out), tts=args.tts, voice=args.voice, rate=args.rate,
                  res=args.res, jobs=args.jobs, fps=args.fps, webm=args.webm)
        elif args.cmd == "mark":
            mark(args.path, out=args.out, clear=args.clear)
        elif args.cmd == "doctor":
            from .tools import doctor
            print(f"codemap {VERSION} doctor")
            for line in doctor():
                print("  " + line)
        elif args.cmd == "demo":
            from . import demo
            root, uses_git = demo.create(Path(args.dir) if args.dir else None)
            if not uses_git:
                mark(root)
            demo.apply_agent_changes(root)
            print(f"demo project: {root}  (a pretend AI agent just changed code, API, infra and CI in 20 files)")
            ir = analyze(root, tier="video" if args.labeled else "free")
            out_dir = root / ".codemap"
            if args.labeled:
                demo.write_sample_labels(out_dir, ir)
                render(out_dir)
            if args.video:
                video(out_dir, webm=args.webm)
            print(f"open: {out_dir / 'codemap.html'}")
        return 0
    except CodemapError as exc:
        print(f"codemap: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # keep agent-facing output short; full trace with CODEMAP_DEBUG=1
        if os.environ.get("CODEMAP_DEBUG"):
            raise
        name = type(exc).__name__
        print(f"codemap: {name}: {exc} (set CODEMAP_DEBUG=1 for a traceback)", file=sys.stderr)
        return 1
