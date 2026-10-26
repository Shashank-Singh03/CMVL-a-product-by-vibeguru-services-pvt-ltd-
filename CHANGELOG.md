# Changelog

## 0.3.0-beta.1 (2026-10-26)

First CMVL public beta, powered by the codemap engine.

- Isolated task captures, opt-in Claude task hooks, supervised generation, retry/cancel,
  and retained maps when video generation fails.
- Offline React dashboard with search, filters, history, settings, capture comparison,
  personal learning checklists, contextual definitions, and Apache ECharts analysis.
- Refined glass workspace with semantic colors, fluid gutters, outside-dismiss navigation,
  a controlled rotating snapshot, and maps/video retained inside the dashboard shell.
- Versioned bundled dashboard, tagged release assets, checksum manifest, CI, portable
  browser acceptance setup, and explicit installation/regeneration instructions.
- Recognize equivalent Windows short and long paths when checking ownership of
  automatic hooks; keep fixture path comparisons consistent on hosted Windows runners.
- Source-backed review and parser/privacy improvements listed below.

- Add source-backed structural model review: source/graph fingerprints, exact line evidence, validated symbol/link
  corrections, retained uncertainty, removal audit trails and visibly marked model inferences. `analyze --review`
  writes a request; the user's current agent writes `review.json`; `review` rebuilds the map and labeling worksheet.
- Extract exported generic React forwardRef components, preserve inline Express handler calls and named callbacks,
  and compose explicit unambiguous router mounts.
- Extract nested/conditional Python declarations with lexical ownership; resolve stable constructed locals and
  explicitly homogeneous instance lists while leaving ambiguous, rebound or escaped receivers unresolved.
- Show files without extracted declarations and model-review coverage, and replace unavailable-diff instructions.
- Add an offline accuracy corpus, explicit precision/recall and identity-level regression baselines, plus a separate
  Python AST declaration reference on real source. Known misses remain visible in the reports.
- Resolve TypeScript/JavaScript imports using local `tsconfig`/`jsconfig` path aliases, base URLs and relative config
  inheritance; leave ambiguous targets unresolved.
- Export complete collected text changes as redacted `codemap.diff` and add portable full-diff downloads plus
  local-only editor links to the explorer.
- Exclude comment/docstring examples from HTTP route and client extraction, and retain private-key redaction context
  for changed files larger than the structural parser's size limit.
- Disclose unreadable or oversized snapshot paths as skipped rather than misreporting existing files as deleted.

- Redact recognized credentials across maps, worksheets, agent labels, cached labels and narration; mask before
  display truncation and cover prefixed names, plain config values and private-key bodies.
- Keep module-level edits visible alongside class edits, and retain current callers of deleted functions/files.
- Avoid guessed Python call edges through shadowed bindings, and leave ambiguous include suffixes unresolved.
- Detect deletion of previously untracked files recorded by `mark`.
- Refresh container descriptions when descendant content changes; cache format 2 refreshes old labels once.
- Include generic config, documentation and generated text in repository audits, within normal file exclusions.
- Show analysis coverage problems; add global search and filters, browser history, keyboard navigation, expanded
  diff excerpts and immediate mute/pause controls to the explorer.
- Add end-to-end privacy, mapping, graph, cache and viewer behavior regressions.

## 0.2.0: one map for every kind of change, with sound (2026-10-14)

**Every language and every area:**
- One configurable engine parses Java, Kotlin, Scala, Groovy, C#, Go, Rust, C, C++, PHP, Swift, Dart, Solidity, shell,
  PowerShell and R (brace languages) and Ruby, Lua and Elixir (`end` languages): classes, structs, interfaces,
  functions, methods, imports, packages and namespaces, calls, fields and constructor-injected types, annotations.
  Go receivers, Rust `impl` blocks, C++ `Class::method` definitions and C# partial classes attach to their type.
- Parsers for API contracts (OpenAPI/Swagger, GraphQL, Protobuf), infrastructure (Dockerfile, Compose, Kubernetes with
  Helm templates, Terraform, Bicep, CloudFormation/SAM), CI (GitHub Actions, GitLab CI, Azure Pipelines, CircleCI,
  Bitbucket, Jenkinsfile, Makefile), data (SQL, Prisma), dependencies (npm, pip, pyproject, Pipfile, Go modules, Cargo,
  Maven, Gradle, Gemfile, Composer, NuGet), notebooks, settings (`.env` values are never stored) and Markdown.
  YAML and JSON are read by a small built-in reader that keeps line numbers.
- Vue, Svelte and Astro files are parsed through their `<script>` blocks.
- Files with no parser that changed are still mapped; their changed lines go to the worksheet and the agent describes them.
- Lockfiles, minified bundles and binaries are skipped and listed separately.

**Linking across languages and areas:** HTTP routes on handlers (FastAPI, Flask, Express, Spring, ASP.NET, NestJS,
JAX-RS, Laravel, Rails, Go routers) are linked to OpenAPI operations and to client calls (`fetch`, axios, requests);
Compose services to Dockerfiles; Kubernetes Services to the Deployments they select; CI jobs to the jobs they need and
the scripts they run; Terraform resources, modules and outputs to what they reference; OpenAPI operations to schemas.

**Map:** when a change spans several areas, the top level is split into Code, API contracts, Data, Infrastructure,
CI/CD, Dependencies, AI, Configuration, Docs and Other. Boxes say what they are (job, resource, endpoint, …).

**Risk lens:** about 50 deterministic rules on added and removed lines (secrets, TLS and JWT checks off, injection
patterns, eval, unsafe deserialization, debug mode, open CORS, removed auth checks, IAM wildcards, open ingress, public
or unencrypted storage, public databases, privileged or root containers, unpinned images and CI actions, secrets in CI
logs, destructive SQL) plus structural checks (removed endpoints, removed or retyped API fields and columns, new
dependencies). Secret values are masked everywhere. Flags appear in the Markdown, the explorer (badges and a panel),
the worksheet (the agent confirms each one) and a "Worth a second look" video scene. `analyze --repo --audit` scans
every file. Tuned on spring-petclinic, cobra, terraform-aws-vpc and eShop: 87 flags before tuning, 6 real ones after.

**Audio:**
- The MP4 narration is loudness-normalized (EBU R128, −16 LUFS) 48 kHz stereo AAC at 192 kbps.
- `video --webm` also writes VP8 + Opus at 5 fps, encoded straight from the stills and narration. VS Code's
  built-in player plays it with sound (it drops AAC audio from MP4 and accepts WebM only with VP8 video).
- After a video export, `codemap.html` embeds the narration as MP3 clips, so the in-browser walkthrough uses the
  recorded voice everywhere.
- Voices: `--tts auto|piper|os|none|cmd:<command>`. Piper (`pip install piper-tts`) is used automatically when its
  voice is present; the default voice, `en_US-ljspeech-medium`, was trained on public-domain recordings.
- Re-rendering an unchanged video takes 0.3 s (the encode is skipped).

**Fixes:**
- Old file versions now load in deep Windows folders (`git cat-file` instead of `git show`, which hit the 260-character
  path limit and silently turned added methods into changed ones).
- The map is deterministic: ties are broken by id, so set ordering can no longer change which neighbours are shown
  (this used to vary between runs and invalidate cached labels and frames).
- Opaque added or deleted files carry their text and line counts.
- A Compose file with only `build:` services is detected; `codemap analyze` on a folder with nothing to map says so.

**Tooling:** `doctor` reports Piper; `demo` is a polyglot product with a 20-file change and supports `--webm`;
44 tests (generic languages, YAML, config parsers, route linking, risk rules and their false-positive guards,
determinism, deep folders, polyglot end to end, and real MP4/WebM encodes with audio).

**Tested on** Windows 11 with Python 3.12 and 3.13, git 2.49, Chrome 155 and Edge, ffmpeg 9.0.2 and Piper 1.8, against
the demo, CPython `asyncio` and the whole standard library, zod, spring-petclinic, cobra, terraform-aws-vpc and eShop.

## 0.1.0: first draft (2026-10-13)

**Pipeline** (standard library only, zero LLM tokens):
- Python parser built on `ast`. It records calls, callbacks, attribute-type inference, entry points and unique ids for
  getter/setter pairs.
- JavaScript/TypeScript scanner. It blanks comments, strings and regexes while keeping `${}` expressions, and finds
  classes, methods, arrow functions, `#private` members, imports and exports, re-exports, JSDoc, JSX and callbacks.
- Symbol graph. It resolves imports (Python suffix index and relative imports; JS extensions, `index.*`, `@/` aliases,
  re-exports) and calls (each with a confidence), plus inheritance, entry points and PageRank.
- Scope selection: uncommitted changes, the last commit, `--base <ref>` (merge-base), a session baseline from
  `codemap mark` (works without git), or `--repo`.
- Diff mapping onto the innermost symbol, with deleted-symbol detection, blank lines ignored, and contextual diff snippets.
- Layering:
  - folders first, then Louvain clustering with adaptive resolution;
  - at most 9 boxes per view, with "+N more" and "+N unchanged" folding;
  - up to 3 ghost neighbours;
  - workflow ordering (callers before callees);
  - name disambiguation.

**Labels:** free summaries from docstrings and structure. A token-budgeted worksheet with tiers `lite`, `full` and
`video`, using short ids. `labels.json` merging, and a label cache keyed by content hash.

**Outputs:**
- `codemap.md`: Markdown with Mermaid diagrams.
- `codemap.html`: a single self-contained explorer with breadcrumbs, a details panel with diffs, the workflow list,
  light and dark themes, and an in-browser narrated walkthrough.
- `codemap.mp4`:
  - narration from the OS voice (SAPI, `say`, `espeak-ng`);
  - one headless Chrome/Edge still per beat;
  - an ffmpeg encode;
  - per-scene caching, so an unchanged re-render takes about 3 s.

**Tooling:**
- `codemap demo` (with `--labeled` and `--video`) and `codemap doctor`.
- `install.py` for Claude Code, `.agents` (Codex, Gemini CLI, Cursor, Copilot), Gemini, Cursor and Copilot folders.
- 15 unit and end-to-end tests, plus an optional MP4 test.

**Tested on** Windows 11 with Python 3.13, git 2.49, Chrome and Edge, and ffmpeg 9.0.2, against the demo, CPython
`asyncio`, `email` and `http`, the whole CPython standard library, the npm CLI and zod.
