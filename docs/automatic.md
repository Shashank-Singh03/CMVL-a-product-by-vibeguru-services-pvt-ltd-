# Automatic walkthroughs in Claude Code

The dashboard's local controls and comparison update was validated on 25 October 2026 with real HTTP requests,
desktop/mobile browser checks, and 373 tests (370 passed, 3 environment skips). See the
control-center findings (local evidence; see [beta validation scope](beta-validation.md)) for the tested scope and limitations.

Automatic mode is an opt-in workflow for Claude Code. It captures a task's source changes, asks the same Claude
conversation to review and explain them, then builds a map and, by default, a narrated video. Ordinary installation
and the manual `analyze`, `review`, `render` and `video` commands remain unchanged.

No separate model service or API key is used. Source review, labels and narration consume normal tokens in the
current Claude session; the worksheet budget is not a cap on those tokens. Parsing and media generation run in
local supervised background processes. The target application's code, tests and build scripts are not executed.

Claude's ordinary read, edit and command permissions still apply. Installing hooks does not grant blanket tool
permission. The native acceptance check used tools preapproved for that disposable invocation; other projects may
ask for approvals under their existing Claude permission policy.

## Enable it for one project

From the codemap checkout:

```bash
python install.py --agents claude --auto --project path/to/repo
python skills/codemap/scripts/codemap.py auto on path/to/repo
```

Restart Claude Code in that project. The installer adds codemap's `UserPromptSubmit`, `Stop` and `SessionEnd` hooks
to the project's `.claude/settings.local.json`. It preserves unrelated settings and hooks. Existing settings are
backed up before a change; malformed settings are refused rather than replaced.

For a deliberate user-wide installation, omit `--project`:

```bash
python install.py --agents claude --auto
```

That installs hooks in `~/.claude/settings.json`; automatic mode is enabled by default for projects using those
hooks unless disabled in the project's codemap configuration. Automatic hooks currently support Claude Code only.
`auto on` alone configures a project; it does not install Claude hooks. A plain `python install.py` does not enable
automatic hooks or remove hooks you enabled previously.

In the commands below, `CM` means `python "<installed-skill-dir>/scripts/codemap.py"`. Replace it with that command
or your own shell alias. Run it from the project directory, or pass the project as the optional path.

## What happens during a task

1. `UserPromptSubmit` records a private before snapshot and a unique task ID.
2. At `Stop`, codemap freezes the end sources and compares the two captured inventories. A task with no supported
   source changes is recorded as `skipped`; it does not request a review or make a video.
3. A background stage parses the frozen sources and writes a preliminary map and source-backed review request.
4. A bounded Stop decision block continues the same Claude conversation. Claude inspects relevant **frozen** source,
   writes the task's `review.json`, and runs `CM task review`. Unsupported relationships stay uncertain. Only files
   actually inspected belong in `reviewed_files`; a partial review does not verify the entire application.
5. Claude reads the regenerated worksheet, writes task-specific labels, the reason for the change and narration,
   then runs `CM task finish`. Rendering finishes the reviewed map and queues video separately.
6. The agent returns the map and history links. Video can continue after the response; its completion or failure
   appears in `.codemap/index.html`.

The continuation supplies exact task paths and commands. They override the generic `.codemap/labels.json` examples
in the manual skill. Do not run a new generic `mark` or `analyze` on the live checkout during this handoff: use the
task's `prepare`, `review` and `finish` commands so its baseline and frozen source remain bound together. Do not edit
application code during the walkthrough completion step.

Model corrections require matching source quotes, hashes, symbol IDs and spans. These checks validate evidence and
consistency, not semantic truth. Accepted model relationships remain identified as model inferences. The review
report and the agent's reply should disclose limited coverage, rejected proposals and unresolved behavior.

## Configuration

Settings are stored in the project's `.codemap/config.json` and copied into a task when it starts. Later changes
configure future tasks; they do not rewrite an existing task's captured sources or saved settings.

```text
CM auto status [path]
CM auto on [path] --video off
CM auto on [path] --video on --minutes 2 --budget 8000 --timeout 180
CM auto on [path] --tts os --res 720 --jobs 2
CM auto on [path] --webm on
CM auto off [path]
```

Square brackets denote optional arguments; do not type the brackets.

| Option | Default | Meaning |
|---|---:|---|
| `--video on/off` | `on` | Encode a narrated video after the reviewed map; `off` keeps review, labels and HTML. |
| `--webm on/off` | `off` | Also make WebM for players such as VS Code's preview. |
| `--minutes` | `2` | Target walkthrough length, from 0.1 to 10 minutes. |
| `--budget` | `8000` | Label worksheet budget, from 256 to 32000; source review can use more tokens. |
| `--timeout` | `180` | Seconds per background analysis, review, render or video stage, from 10 to 3600. |
| `--tts` | `auto` | `auto`, `os`, `piper` or `none`; Piper is optional and its voice may need a separate download. |
| `--res` | `1080` | Video resolution: 720 or 1080. |
| `--jobs` | `2` | Parallel browser frame workers, from 1 to 4. |

Turning auto mode off stops future automatic handoffs in that project. It does not cancel an already queued task;
use `task cancel` for that. Video settings are separate from enabling or disabling automatic mode.

## History and task controls

Open `.codemap/index.html`, directly or through a local static server such as Live Server. To regenerate the
dashboard from existing task records and open it, run this from the codemap checkout:

```bash
python skills/codemap/scripts/codemap.py task history path/to/repo --open
```

The React dashboard is bundled into that HTML file. Viewing it needs no Node installation, CDN, separate backend
or internet connection. Search task titles, change summaries and IDs; filter by status or choose **All tasks**,
**Videos** or **Needs attention**; and sort newest or oldest first. Counters show the recorded task totals. Task
details show stage progress, available change counts, warnings and a copyable task ID. Dates use the browser's
local time. Finished videos play in the page, and light/dark appearance is remembered in the browser. Press `/`
to focus search and Escape to close a detail or video view. **Learning** explains a selected captured change;
**Analysis** summarizes the available saved task data. **NEW** marks a task not yet viewed in this browser's saved
history, rather than a new source edit or completed review.

When served over HTTP, **Refresh** fetches the current history HTML and updates its public task metadata. Active
tasks poll about every five seconds while preserving an open video player. When opened with `file://`, Refresh
reloads the page and there is no background polling. A development server's separate live-reload feature can still
reload the page when outputs change; disable that feature for uninterrupted playback while other tasks finish.
Dashboard polling remains available.

A static server serves saved results; it does not run task stages or change settings. Run
`python skills/codemap/scripts/codemap.py serve path/to/repo --open` for the local server with connected retry,
cancel and settings controls. **Compare** also works offline. See the [local control center](control-center.md).
The agent and CLI remain available for the same actions:

```text
CM task history --open
CM task status --id <32-hex-task-id>
CM task prepare --id <32-hex-task-id> --wait 60
CM task review --id <32-hex-task-id> --wait 60
CM task finish --id <32-hex-task-id> --wait 60
CM task cancel --id <32-hex-task-id>
```

Replace the angle-bracket placeholders too. All task commands accept an optional project path after the action.
`history` needs no ID; the other public task commands do. `prepare`, `review` and `finish` accept `--wait` from 0 to
180 seconds; the CLI default is 0. Waiting limits that command's wait, not the worker's stage deadline. `finish`
waits for map rendering when requested and returns without waiting for video.

The history shows separate capture, analysis, review, labeling and video states. An **Open map** link can point to
a preliminary map while model review is pending. **Play video** and **Download MP4** appear only when a completed
task records a ready video and its file exists. Missing promised output files are shown as unavailable. Active
and completed tasks can show attribution warnings when their capture windows overlap. History reads saved state;
a stale worker state is reconciled by task status/recovery commands.

For frontend development, see the [dashboard build instructions](../dashboard/README.md). Run `npm ci` and
`npm run build` in `dashboard/`, then regenerate a project's history to embed the rebuilt assets. These build
tools are needed only when changing the dashboard. Existing installations need the updated skill files before
regenerating history. The compiled React and Apache ECharts assets are included inline, not downloaded at runtime.

If a stage fails, its completed map and captured sources are retained. Retry the relevant stage:

```text
CM task retry --id <32-hex-task-id> --stage analysis
CM task review --id <32-hex-task-id> --wait 60
CM task finish --id <32-hex-task-id> --wait 60
CM doctor
CM task retry --id <32-hex-task-id> --stage video
```

For a review failure, inspect the task's `review-report.json`, correct its proposal and rerun `task review`. For a
render failure, check the regenerated worksheet and `labels.json`, then rerun `task finish`. `retry` also accepts
`--stage review` or `--stage render`, but the normal review/finish commands provide the input checks first.

Video needs a supported browser and ffmpeg; a usable voice is needed for spoken audio. Run `doctor` for local tool
diagnostics. If video tools fail, the map remains usable: fix the tools and retry **video**, without recapturing the
task or asking the model to repeat the source review. A cancelled task is retained; begin a new coding task rather
than trying to resume its capture. Capture failures also require resolving the capture problem and starting a new
task, not pretending the incomplete snapshot is a complete map.

A hard process kill during synchronous before/end capture can leave a stale `capturing` or `freezing` history
record. Background-worker status refresh does not repair that partial capture. Begin a new coding task so a later
prompt establishes a fresh capture; do not treat the old record as a complete map or reuse its partial sources.

## Learning and analysis

Choose a captured task in **Learning** to follow five steps:

1. **Understand the goal** from the recorded explanation and review status.
2. **Learn the vocabulary** with expandable definitions selected for the observed languages and engineering areas.
3. **Read the highlighted changes**, or explore mapped context when the saved analysis has no recorded edits.
4. **Follow the connections** between the selected items, distinguishing parsed links from model inferences.
5. **Check your understanding** with questions and explanations you can reveal after considering your own answer.

The glossary covers relevant JavaScript, TypeScript, Java, C++, Python, DevOps, CI/CD, API and data concepts when
those formats appear in the saved analysis. It explains general terms rather than asserting that every language
feature is used. Added and removed lines are highlighted separately. The lessons use a bounded, redacted selection
from the saved analysis IR, without reading private source snapshots. They are not a complete project course or
the full diff. Newer tasks receive the page's learning-data budget first, capped at 1 MB overall; older or oversized
material shows an unavailable reason and a link to its saved map instead of silently claiming full coverage.

Source-backed model review can still be pending while a lesson is available. Its status remains explicit, and
parser-derived explanations and links are provisional. Supported formats already feed the analysis engine; the
dashboard makes them easier to explore. Format support does not guarantee complete parsing or correct links:
dynamic dispatch, dependency injection and template-generated behavior can remain unresolved. A recorded model
review is also not proof of semantic correctness.

**Mark understood** is a manual reading checklist. It does not verify knowledge, execute tests or advance the
task's generation stages. Completion is stored by task ID, lesson revision and step ID in browser-local storage;
a changed lesson revision starts a fresh checklist. Viewed-task markers and theme preference are local too.
Persistence needs available browser storage and the same browser profile and origin. A different server port,
another browser or blocked storage can give a separate or unsaved checklist; no account sync is provided.

**Analysis** uses bundled Apache ECharts for four views, each with a readable data table: **Languages and formats**,
**Areas of the system**, **Change activity** and **Generation status**. These report observed parsed-file groups,
code/API/infrastructure/CI/CD/data areas, added and removed lines, and task statuses. File totals can count the
same file across several saved captures. They are not unique repository file counts, accuracy scores, productivity
measures or whole-project completion percentages. Rule risk flags need inspection; they are not confirmed defects.

Generation progress in the workspace, task cards and Analysis counts finished recorded stages out of the task's
total. It is separate from the personal lesson checklist and source-review coverage, and is not an estimate of
time remaining. For example, **2 / 5 stages** can coexist with **0 / 11 files** listed in source review and an
available five-step lesson. None of those figures means that 40% of the application has been verified.

## Private files and capture limits

Each task has an isolated folder:

```text
.codemap/
  config.json
  index.html
  tasks/<task-id>/
    state.json                  # private task metadata and manifests
    work/                       # private end-source snapshot
    output/
      baseline/                 # private before-source snapshot
      baseline.json
      codemap.html
      codemap.md
      codemap.diff
      review.md / review-request.json / review-report.json
      worksheet.md / labels.json
      codemap.mp4               # only after successful video generation
      codemap.webm              # optional
```

The source snapshots contain original bytes, including any credentials in eligible source. Keep `work`, `baseline`
and private metadata local. Do not publish the entire `.codemap` folder. History never links raw snapshots, state
JSON or caches and does not display raw prompts or error text. This is not access control: a static server serving
the project root can still expose those files through their URLs, including to other devices if the server is
reachable. Keep the server local and share only inspected output artifacts. Generated maps, diffs and narration
redact recognized credential patterns; unusual formats can be missed, so inspect an artifact before sharing it.
Local source handoffs refer to the captured task sources, not a promise that the live checkout still matches.

Capture is limited to discovered eligible files and excludes dependency, build, cache and codemap output folders.
It is a sequence of file reads, **not an atomic filesystem snapshot**. Manifests verify saved before/end sources
before later stages; edits during capture can still prevent a safe capture. A task window does not prove authorship.
Known overlapping coding sessions are flagged; edits from other tools can also fall within the window.

Binary and oversized files are not included in the text map. Unchanged omissions are allowed only when bounded
fingerprints establish they stayed the same; history discloses their count. A changed eligible file that cannot be
captured, or an omission whose unchanged status cannot be established, produces `capture_failed` rather than a
complete-change claim. Fingerprinting is bounded too; very large files can prevent a complete task capture.

## Disable or remove the hooks

For a project opt-out, keep the skill and hooks installed and run `CM auto off [path]`.

To remove codemap's automatic hooks while keeping the skill, use the same installation scope:

```bash
python install.py --agents claude --no-auto --project path/to/repo
```

To uninstall the Claude skill and its recognized hooks:

```bash
python install.py --agents claude --uninstall --project path/to/repo
```

Omit `--project` only for a user-wide installation. Removal preserves unrelated Claude settings and hooks; existing
settings are backed up as `settings.local.json.codemap-backup-<id>.json` or `settings.json.codemap-backup-<id>.json`.
Those backups can contain private configuration. Compare them with current settings before restoring one, so later
unrelated changes are not lost. Uninstalling does not delete a project's saved codemap task history or private sources.

## Validation status

On **24 October 2026**, native acceptance checks passed on Windows with installed Claude Code **2.1.71**: a no-change
prompt was skipped, then two sequential coding prompts in one resumed conversation each completed source review,
labels, map rendering and a background video. Each task recorded one reviewed file. The first map contains only the
`return 1` to `return 2` change; the second contains only `return 2` to `return 3`.

Both MP4s are nonempty, about 10.5 seconds long, and contain H.264 video with AAC stereo audio at 48 kHz. Audio-level
checks confirm non-silent output. That duration is the finished video length, not a generation-time measurement.
The saved history screenshot shows both completed task cards with map, play and download links. See the
acceptance findings (local evidence; see [beta validation scope](beta-validation.md)) and their machine-readable evidence.

This documentation was updated on **25 October 2026**. The native check used only disposable fixture projects and
project-local installation; no global user configuration was installed or changed, and target application code was
not executed. Normal Claude permissions remained in force, with invocation-specific tool approvals for the check.

The fixture is one small Python file. It does not establish parser/link accuracy on complex applications, reliable
operation under every approval configuration, acceptable noise during everyday work, or latency on large projects.
Additional Claude versions, macOS, Linux and broader real-development workflows remain unvalidated. A workflow
regression run on **25 October 2026**, before the dashboard refresh, passed: **326 tests, 323 passed and
3 environment skips**, with real video tests enabled, in 97.349 seconds. It checks the recorded build with its
follow-up workflow fixes; it does not establish dashboard validation or change which build produced the native
media. See the validation record (local evidence; see [beta validation scope](beta-validation.md)) for the separate native and
regression evidence, that build's source fingerprints, and remaining capture limitations.

The **25 October 2026** learning fixture (local evidence; see [beta validation scope](beta-validation.md)) exercises eleven authored
files across JavaScript, TypeScript, Java, C++, Python, Docker, Kubernetes, Terraform, GitHub Actions, SQL and
OpenAPI. File and HTTP dashboard checks (local evidence; see [beta validation scope](beta-validation.md)), including
a narrow mobile layout, passed for this fixture without executing the fixture application. Source-backed review
was intentionally pending: the recorded state is **2 / 5
generation stages** and **0 / 11 files** listed in source review. This checks the UI's treatment of those distinct
states, not parser accuracy across real projects or completed model review.
