# codemap: options and troubleshooting

Load this file only when a command fails or the user asks for an option the main SKILL.md does not cover.

## Commands

| Command | What it does |
|---|---|
| `analyze [path]` | Parse, link, layer, check risks, write outputs. Zero LLM tokens. |
| `review [path]` | Validate agent-written source-backed graph corrections in `review.json`, then rebuild the map and label worksheet. `--input FILE` supplies another proposal path. |
| `render [path]` | Merge `.codemap/labels.json` into the map and rebuild the outputs. |
| `video [path]` | Export the narrated walkthrough as `.codemap/codemap.mp4` (and `codemap.webm` with `--webm`). |
| `mark [path]` | Record a baseline; later analyses explain only what changed after it. `--clear` forgets it. |
| `demo` | Build a sample polyglot project, simulate an AI change, map it (`--labeled`, `--video`). |
| `doctor` | Report what this machine can do (git, browser, ffmpeg, voices, Piper). |

## analyze flags

| Flag | Meaning |
|---|---|
| `--base REF` | Compare against a branch, tag or commit. For a branch, the merge-base is used. |
| `--repo` | Map the whole repository instead of changes. |
| `--audit` | With `--repo`: scan eligible text files, including config/docs/generated text, for medium and high flags. Normal ignored-file, directory, test, binary and size exclusions still apply. |
| `--review` / `--no-review` | Write/skip the structural review request. Default: enabled for non-free tiers. Your existing agent reads source and writes proposals; the script does not call a model. |
| `--tier free/lite/full/video` | How much the agent is asked to write. Default: `lite` for changes, `free` for `--repo`. |
| `--budget N` | Cap the worksheet at about N tokens (default: lite 3000, full 8000, video 9000). Highest-value items go first. |
| `--minutes M` | Target walkthrough length (default 2). |
| `--tests` / `--no-tests` | Include test files (default: included for changes, excluded for `--repo`). |
| `--out DIR` | Output folder (default `<repo>/.codemap`, which ignores itself in git). |

## Scope rules (when no `--base` and no `--repo`)

1. A baseline from `codemap mark` exists: compare against it.
2. Uncommitted changes: compare against HEAD.
3. Clean tree: explain the last commit.
4. Not a git repository and no baseline: map the whole folder.

## What gets mapped

When a change spans several areas, the top of the map is split by area, in this order.

| Area | Files | Boxes |
|---|---|---|
| Code | Python, JavaScript/TypeScript (+ Vue, Svelte, Astro), Java, Kotlin, Scala, Groovy, C#, Go, Rust, C, C++, PHP, Swift, Dart, Ruby, Lua, Elixir, R, Solidity, shell, PowerShell | classes, functions, methods |
| API contracts | OpenAPI/Swagger, GraphQL, Protobuf | endpoints, schemas, types, RPCs |
| Data & schema | SQL, Prisma | tables, columns, indexes, migrations |
| Infrastructure | Dockerfile, Compose, Kubernetes (+ Helm templates), Terraform/HCL, Bicep, CloudFormation/SAM | stages, services, workloads, resources, modules, outputs |
| CI/CD & build | GitHub Actions, GitLab CI, Azure Pipelines, CircleCI, Bitbucket, Cloud Build, Jenkinsfile, Makefile | triggers, jobs, stages, targets |
| Dependencies | package.json, requirements*.txt, pyproject, Pipfile, go.mod, Cargo.toml, pom.xml, Gradle, Gemfile, composer.json, NuGet | packages, scripts |
| AI & notebooks | Jupyter notebooks, prompt files | notebook sections |
| Config, docs, other | .env, YAML/JSON/TOML/INI settings, Markdown | keys (env signatures omit values; recognized credentials in diffs are redacted), sections |

Lockfiles, minified bundles, source maps and binaries are skipped. Any other text file that changed is still shown,
shown without extracted declarations; inspect its source and available changed lines before describing it.
In a whole-repo map, docs, generic settings and plain text are left out unless they changed.

Arrows connect areas, not just code: a browser `fetch` to the API handler (FastAPI, Flask, Express, Spring, ASP.NET,
NestJS, JAX-RS, Laravel, Rails, Go routers) and to its OpenAPI operation; Compose services to their Dockerfile;
Kubernetes Services to the Deployments they select; CI jobs to the scripts they run and the jobs they need;
Terraform resources to the resources and modules they reference; OpenAPI operations to their schemas.
Parsing outside Python and JavaScript/TypeScript is pattern-based: good for maps, not a compiler.

## Risk flags

About 50 deterministic rules run on added and removed lines and on the structure of API and data changes: hard-coded secrets (recognized values are redacted in maps, worksheets, labels, caches and narration),
TLS or JWT checks turned off, SQL/shell injection patterns, eval, unsafe deserialization, debug mode, open CORS,
removed authentication checks, IAM wildcards, networks open to 0.0.0.0/0, public or unencrypted storage, public databases,
privileged or root containers, unpinned images and CI actions, secrets echoed in CI, destructive SQL, removed API
endpoints or fields, and new dependencies. Severity is high, medium or low. They are hints for a reviewer, not verdicts;
the agent confirms each one or calls it a false positive in `labels.json` (`"risks": {"r1": "Real issue: ..."}`).

## video flags

| Flag | Meaning |
|---|---|
| `--tts auto` | Default. Piper if installed and its voice is downloaded, otherwise the OS voice. |
| `--tts piper` | Piper neural voice (`pip install piper-tts`). The voice model (~63 MB) downloads once. |
| `--tts os` | Windows SAPI, macOS `say`, Linux `espeak-ng`. |
| `--tts "cmd:<command>"` | Any TTS program. `{text}` is replaced by a UTF-8 text file, `{out}` by the WAV to write. |
| `--tts none` | Silent video; captions stay on screen. |
| `--voice NAME` | Piper voice (e.g. `en_US-amy-medium`) or system voice (e.g. `"Microsoft Zira Desktop"`, `Samantha`). |
| `--webm` | Also write `codemap.webm` (VP8 + Opus), which plays with sound inside VS Code. |
| `--rate N` | Speech rate from -5 to 5. |
| `--res 720` / `1080` | Output resolution. |
| `--jobs N` | Parallel browser processes for the frames (default 4). |
| `--fps N` | Frame rate (default 15; scenes are stills, so higher only makes bigger files). |

The MP4 carries loudness-normalized 48 kHz stereo AAC. After a video export, `codemap.html` also embeds the same
narration as small MP3 clips, so its "Watch walkthrough" player speaks with the recorded voice in any browser.
Without a video export the player uses the browser's own speech voices.

## Environment variables

| Variable | Meaning |
|---|---|
| `CODEMAP_BROWSER` | Path to Chrome, Edge or Chromium for the video frames. |
| `CODEMAP_FFMPEG` | Path to ffmpeg. |
| `CODEMAP_PIPER_PYTHON` | A Python that has `piper-tts` installed, if not the one running codemap. |
| `CODEMAP_PIPER_DIR` | Where Piper voices are stored (default `~/.cache/codemap/piper`). |
| `CODEMAP_DEBUG=1` | Show full tracebacks. |

## Troubleshooting

Change analyses write `codemap.diff` beside the HTML/Markdown with all collected changed text lines. It is redacted
and intended for review, not applying. Local HTML opened from its original output folder offers **Open in VS Code**
at recorded source lines; hosted/deleted-source views omit the editor link. Keep the diff beside shared maps.

JS/TS links read the nearest local `tsconfig.json` or `jsconfig.json`, including JSONC, `paths`, `baseUrl` and relative
file `extends`. Package/array inheritance, project references and compiler-specific resolution modes are not modeled;
ambiguous configured targets remain unresolved.

| Symptom | Fix |
|---|---|
| `python` not found (Windows) | Use `py -3`, or install Python 3.9+ from python.org. The Microsoft Store alias may need disabling. |
| "nothing to explain" | There are no changes in scope. Use `--base <ref>`, or `--repo` for a full map. |
| Video plays without sound in VS Code | VS Code's built-in player drops AAC audio. Re-run with `--webm` and open `codemap.webm`, or open the MP4 in a browser or media player. |
| "ffmpeg not found" | Windows: `winget install Gyan.FFmpeg`. macOS: `brew install ffmpeg`. Linux: `sudo apt install ffmpeg`. Anywhere: `pip install imageio-ffmpeg`. |
| "no Chrome/Edge/Chromium found" | Install one, or set `CODEMAP_BROWSER=/path/to/browser`. |
| No system voice (Linux) | `sudo apt install espeak-ng`, `pip install piper-tts`, or use `--tts none`. |
| "Piper is not installed" | `pip install piper-tts` in the Python that runs codemap, or set `CODEMAP_PIPER_PYTHON`. |
| "could not download the Piper voice" | The first Piper run needs internet once; or place `<voice>.onnx` and `.onnx.json` in `CODEMAP_PIPER_DIR`. |
| A file shows as "text file" | codemap has no parser for that format; the agent describes it from its changed lines. |
| Map too big or too vague | Pass a sub-folder path, or use `--base` to focus on a change. |
| Unknown ids reported by `render` | `labels.json` was written for an older analysis. Re-run `analyze`, then label the new worksheet. |

## Agent-specific notes

- **Claude Code**: to save main-context tokens on big worksheets, hand the worksheet to a subagent running a small model (for example Haiku) and ask it to write `labels.json`.
- **Gemini CLI**: activating a skill asks the user for consent; that is expected.
- **All agents**: labels are cached by content hash in `.codemap/cache.json`, so re-runs only ask about code that changed.

## Privacy

Everything runs locally. Nothing is uploaded, except that `--tts piper` downloads its voice model once.
The worksheet is read only by the agent you are already using. Recognized credentials are redacted before output;
unusual secret formats can be missed. Local `mark` snapshots retain original source for comparisons.
