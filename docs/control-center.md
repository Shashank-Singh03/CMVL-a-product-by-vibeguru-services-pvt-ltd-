# Local dashboard controls and task comparison

The dashboard has two modes. Saved HTML and ordinary static servers provide browsing, learning, charts and task
comparison. Codemap's local server also enables generation controls and project settings.

From the Codemap checkout, run:

```powershell
python skills/codemap/scripts/codemap.py serve path/to/project --open
```

Keep that terminal running. The command serves one selected project at `http://127.0.0.1:8765/`; `--open` opens it
in your browser. Use `--port 8766` to choose another port, or `--port 0` to select an available one. Press Ctrl+C
to stop the server. Stopping the server does not cancel queued background generation.

The page shows **Local controls connected** when it can use the server. The connected dashboard polls for saved
tasks, including new tasks that arrive while the page was idle. Search, selected comparisons, learning progress
and an open video stay in place during dashboard refresh. A disconnected page retains its saved display and
offers Reconnect; actions need a live connection.

## Retry or cancel generation

Open a task's details to see **Generation controls**. Failed analysis, review, render and video stages can be
retried when their saved inputs and captured sources are valid. Review and render retries still require the
agent's source-backed review or labels; clicking Retry does not call a model or invent that material.

**Cancel generation** asks for confirmation. It stops pending generation or review work for that captured task,
retaining its sources and any completed outputs. It does not stop the coding agent or undo application edits.
Cancelled captures cannot resume; begin a new coding task. An in-progress source capture and a capture failure
need agent/CLI recovery rather than a generation retry button.

The server checks the current task state and saved revision. If the task changed after the page loaded, refresh
before repeating the action. Failed actions do not silently run again. Existing CLI controls remain available.

## Project preferences

**Settings** controls automatic-mode enablement, video/WebM output, target video length, worksheet budget, stage
timeout, voice, resolution and worker count. Save applies validated values to this project's configuration.

These preferences apply to future tasks. Existing captures keep their pinned settings. Enabling automatic mode
does not install Claude hooks, and turning it off does not cancel an existing job. A worksheet budget is not a
cap on the coding model's total token usage. Choosing **No narration** requests a silent video.

Saved HTML and Live Server pages show instructions for starting the Codemap server instead of pretending to
save settings. See [automatic mode](automatic.md) for hook installation and the command-line equivalents.

## Compare captured tasks

Choose **Compare**, select two distinct captures, or swap their positions. The page shows the recorded goals,
dates, generation stages, review counts, language/system-area breakdowns and selected learning examples side by
side. Metric differences use capture B minus capture A. Unknown figures remain unrecorded.

This compares saved task summaries, not the source diff between their final snapshots. Each task's added/removed
line counts use its own baseline. A file can be parsed in both captures, and learning selections can omit other
changes. **In both selections** and **Only in this selection** describe the examples shown, not a complete
inventory of added/deleted project code.

**Download comparison** exports a Markdown summary with selected example labels. It excludes raw snapshots,
code excerpts and local control-session tokens. This comparison works without the local server; fewer than two
captures shows an explanation rather than manufactured results.

## Local access and private files

The server binds to `127.0.0.1` and exposes the selected dashboard, public task metadata and allowlisted generated
maps, diffs and videos. It does not serve snapshot sources, task state, IR, model worksheets or arbitrary project
files. Maps hide source-editor handoffs over HTTP; the original local map retains applicable file-based links.

Changing settings or tasks requires the local dashboard's same-origin session. Its request token stays in browser
memory and is not written into the saved HTML, localStorage or comparison exports. Host/origin checks and bounded
JSON inputs restrict the control endpoints. This is a local desktop workflow, not a multi-user hosted service.

The server runs trusted Codemap processing when requested. It does not execute target applications, tests, build
scripts or deployments. Models still work through the user's existing agent conversation. Keep the original raw
captures local and inspect redacted artifacts before sharing them.
