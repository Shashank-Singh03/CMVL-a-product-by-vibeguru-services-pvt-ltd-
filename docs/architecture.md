# Architecture

Codemap builds an initial graph through deterministic stages. An optional structural review lets the user's existing
model inspect source and propose evidence-backed graph corrections before naming chunks and assessing risk hints.
Validation and rendering remain standard-library Python. See [model review](model-review.md) for the protocol and limits.

## Design principles

1. **Source-backed structure.** Parsers supply the initial graph. Model corrections require current source evidence,
   are labeled as inferences, and keep uncertainty explicit. Matching evidence is not proof of semantic correctness.
2. **Human-sized views.** A view shows at most 9 boxes (Miller's 7±2), plus up to 3 dimmed neighbours for context.
   Anything larger is split into another level or folded into a "+N more" box.
3. **Every changed file is shown.** Code, contracts, infrastructure, pipelines, data, dependencies and docs all become
   boxes. A file nothing can parse still gets a box, and its changed lines go to the agent.
4. **Folders first.** Directory names carry human intent. Graph clustering is used only when a folder is too flat to read.
5. **Workflow order.** Inside a view, boxes are ordered so that callers come before callees. The view reads left to right like a pipeline.
6. **One intermediate representation (IR) feeds every output.** Markdown, HTML and video all render from `ir.json`.
7. **Same input, same map.** Every tie is broken by id, so runs are byte-for-byte repeatable (minus timestamps). That is
   what makes the label cache and the video cache reliable.
8. **Cheap to re-run.** Agent labels are cached by content hash, video scenes by what they show, the encode by its inputs.

## Pipeline and module map

```mermaid
flowchart TD
  scope[changes.py<br/>scope + hunks] --> scan[scan.py + formats.py<br/>discover + classify files]
  scan --> py[parse_python.py<br/>ast]
  scan --> js[parse_js.py<br/>JS/TS scanner]
  scan --> gen[parse_generic.py<br/>19 more languages]
  scan --> cfg[parse_config.py + yamlite.py<br/>API · infra · CI · data · deps · docs]
  py --> routes[routes.py<br/>HTTP routes + client calls]
  js --> routes
  gen --> routes
  cfg --> graph
  routes --> graph[graph.py<br/>imports · calls · inheritance · references · PageRank]
  graph --> diff[diffmap.py<br/>added / modified / deleted per item]
  diff --> risks[risks.py<br/>risk flags]
  risks --> review[review.py<br/>source-backed corrections + uncertainty]
  review --> layers[hierarchy.py<br/>areas · groups · units · views]
  layers --> labels[labels.py<br/>auto summaries · worksheet · merge · cache]
  layers --> story[story.py<br/>beat plan + narration]
  labels --> render[render.py<br/>Markdown/Mermaid + HTML]
  story --> render
  render --> video[video.py<br/>voice + stills + ffmpeg]
```

| File | Responsibility |
|---|---|
| `cli.py` | Command line: `analyze`, `review`, `render`, `video`, `mark`, `demo`, `doctor` |
| `review.py` | Source/graph fingerprints, correction requests, evidence validation, graph edits and provenance |
| `pipeline.py` | Orchestrates the three pipelines; keeps the agent-facing output to about seven lines |
| `changes.py` | Chooses the scope (base, baseline, last commit); parses `git diff --unified=0`; snapshot diffs without git |
| `scan.py`, `formats.py` | Finds files (via `git ls-files`, or a filesystem walk) and classifies each into a format and an area |
| `parse_python.py` | Python parser on `ast`: symbols, signatures, docstrings, calls, callbacks, attribute types |
| `parse_js.py` | JavaScript/TypeScript scanner (also the `<script>` blocks of Vue, Svelte and Astro files) |
| `parse_generic.py` | One engine for Java, Kotlin, Scala, Groovy, C#, Go, Rust, C, C++, PHP, Swift, Dart, Solidity, shell, PowerShell, R, Ruby, Lua, Elixir |
| `parse_config.py`, `yamlite.py` | Parsers for API contracts, infrastructure, CI, data, dependencies, notebooks, settings, Markdown; a small YAML/JSON reader with line numbers |
| `routes.py` | HTTP routes on handlers, and the client calls that hit them |
| `graph.py` | Module resolution, call resolution with confidences, inheritance, explicit references, entry points, PageRank |
| `tsconfig.py` | Local TypeScript/JavaScript path aliases and config inheritance, with ambiguous targets unresolved |
| `diffmap.py` | Maps hunks onto items; finds deleted items; builds the per-item diff snippets |
| `risks.py` | Risk rules on changed lines, structural API/schema checks, whole-file audit |
| `hierarchy.py` | Builds the layers, folds, ghosts, workflow order and labels for each view |
| `labels.py` | Free summaries, the worksheet, merging `labels.json`, the label cache |
| `story.py` | Plans the walkthrough beats and writes the deterministic narration |
| `render.py` | Markdown with Mermaid, and the single-file HTML |
| `video.py`, `tools.py` | Text-to-speech, headless-browser stills, ffmpeg assembly; finding external tools |
| `algo.py` | PageRank, Louvain, Tarjan SCCs, workflow ordering |
| `demo.py` | The built-in polyglot sample project and its simulated AI change |

## 1. Scope

If neither `--repo` nor `--base` is given, the first matching rule wins:

1. A baseline from `codemap mark` exists: diff against it. In git this is a `git stash create` snapshot plus copies of untracked
   files. Outside git it is a full copy of the files.
2. There are uncommitted changes: diff the working tree against `HEAD`, including untracked files.
3. The tree is clean: explain the last commit (`HEAD~1..HEAD`).
4. Otherwise, map the whole folder.

`--base <branch>` uses the merge-base, so `--base main` shows only this branch's work and not what landed on `main` since.
Commit messages in the range become the default "why". Old file versions are read with `git cat-file blob <rev>:<path>`
(`git show` also stats its argument as a path, which fails past Windows' 260-character limit in deep folders).

## 2. File discovery and classification

- **Git repositories:** `git ls-files --cached --others --exclude-standard`, so `.gitignore` is respected.
- **Elsewhere:** a filesystem walk that skips `node_modules`, virtual environments, `dist`/`build`, caches, `vendor` and
  similar folders, but keeps `.github`, `.gitlab` and `.circleci`.
- **Always skipped:** binaries, files over 600 KB, minified files and source maps, files marked `@generated` or
  `DO NOT EDIT`, `.d.ts` declarations, and lockfiles (listed under "also changed" when they change).
- **Test files:** included when explaining changes, excluded for `--repo` maps (override with `--tests` or `--no-tests`).

`formats.classify(path, head)` gives each file a **format** (`java`, `kubernetes`, `deps-npm`, …) and an **area**
(`code`, `api`, `data`, `infra`, `ci`, `deps`, `ai`, `config`, `docs`, `other`) from its name, extension and, for YAML and
JSON, the first 4 KB of content: `openapi:` means a contract, `apiVersion:` + `kind:` a Kubernetes manifest (also inside
Helm `templates/`), `AWSTemplateFormatVersion` CloudFormation, `services:` with `image:`/`build:` a Compose file.
In a whole-repo map, docs, generic settings and plain text are left out; in a diff, every changed file is mapped.

## 3. Parsing

Every parser produces the same `FileInfo`: imports, items (classes, functions, methods, or config items), entry-point
hints, and per-item signature, first doc sentence, call chains, decorators, span, content hash, plus `keys` (names other
files can reference) and `refs` (references to such keys, with a confidence).

**Python (standard-library `ast`; syntax-based structure, conservative linking).**
- **Lexical ownership:** nested/conditional declarations use qualified IDs; calls inside a nested callable belong to it.
- **Stable receivers:** single constructed locals and explicit homogeneous instance lists can resolve methods.
  Rebound names, unknown writes, escaping containers and runtime factories stay unresolved.
- **Calls** are recorded as attribute chains, for example `self.store.save` becomes `("self", "store", "save")`.
- **Callbacks** passed as arguments, such as `map(f, xs)` or `Thread(target=self.run)`, are recorded as references.
- **Attribute types** are inferred from `self.x = Foo(...)`, `self.x = param` where `param: Foo`, and dataclass-style `x: Foo` fields.
  This is what lets `self.store.save()` resolve to `JsonStore.save`.
- **Entry points:** calls inside `if __name__ == "__main__":` count as entry-point hints.
- **Duplicate names** (property getter/setter pairs, redefinitions) become `name~2` so every id is unique.

**JavaScript / TypeScript (scanner, fast but approximate).**
1. **Blank the noise.** Comments, string contents, regex literals and template-literal text are replaced with spaces, so offsets
   and line numbers survive. `${...}` expressions stay visible, because they contain calls.
2. **Find structure.** Top-level `class`, `function` and `const f = (...) =>` / `exports.f = function` definitions are found at
   depth 0, and methods and arrow-function fields at class-body depth. Proven React forwardRef wrappers support balanced
   generic type arguments; Express inline bodies and named callbacks retain their links. Explicit unambiguous local
   router mounts compose prefixes, while dynamic, multiple or cyclic mounts remain uncomposed.
3. **Attach extras.** A JSDoc comment belongs to the symbol that follows it. JSX tags (`<List />`) count as calls.
4. **Attribute types** come from `this.x = new Foo()` and TypeScript parameter properties.

**Every other language (`parse_generic.py`, one engine configured per language).** A `Spec` describes a language:
comment and string syntax (nested comments, raw strings, heredocs, triple quotes, preprocessor lines), container keywords
(`class`, `interface`, `struct`, `trait`, `impl`, `object`, `enum`, `record`, `protocol`, `module`, …), how functions are
declared (keyword-introduced like `func`/`fn`/`def`/`function`, or typed like Java's `public int count(...)`), import
syntax, and the self-reference word (`this`, `self`, `$this`, `@`).
1. **Blank** comments and string contents, as for JavaScript.
2. **Find containers and callables.** Brace languages use brace matching; Ruby, Lua and Elixir use `end` matching with
   their block-opening keywords. Language quirks are handled where they matter for maps: Go receivers
   (`func (s *Store) Save`), Rust `impl` blocks and C++ `Class::method` definitions attach methods to their type; Swift
   extensions merge into the type; a C++ definition replaces its declaration.
3. **Extract** imports (packages, namespaces, `use`, `#include`, `require`), call chains (with `this.`/`self.`/`$this->`
   normalized), `new Foo()`, callbacks, field and constructor-parameter types (for `repo.findAll()` resolution),
   annotations and attributes (`@GetMapping`, `[HttpDelete]`, `#[get]`), and doc comments (`/** */`, `///`, `#`).

It is approximate by design: it never invents edges, and everything downstream is shared with Python and JavaScript.

**Contracts, infrastructure, CI, data, dependencies (`parse_config.py`).** YAML and JSON go through `yamlite.py`, a small
reader that keeps line numbers (block and flow styles, block scalars, multiple documents; anchors are kept as text).

| Format | Items | Linked through |
|---|---|---|
| OpenAPI / Swagger | an "Endpoints" container with one item per operation, a "Schemas" container with one item per schema (fields kept for breaking-change checks) | operations → handlers (`route:` keys) and → schemas (`$ref`) |
| GraphQL, Protobuf | types, queries/mutations, messages, services, RPCs, fields | type references |
| SQL, Prisma | tables (columns as members), indexes, views, migrations | foreign keys, `ON table`, `ALTER table` |
| Dockerfile | build stages | `COPY --from` / `FROM <stage>`; Compose `build:` |
| Docker Compose | services | `depends_on`, `build:` → Dockerfile |
| Kubernetes (+ Helm templates) | one item per document (`Deployment/api`, `Service/api`, …) | label selectors, Ingress backends, ConfigMap/Secret/volume/ServiceAccount references, scale targets, RBAC bindings |
| Terraform / HCL, Bicep, CloudFormation | resources, data sources, modules, variables, outputs, parameters | references to other addresses, module sources, `Ref`/`GetAtt`/`DependsOn` |
| GitHub Actions, GitLab CI, Azure Pipelines, CircleCI, Bitbucket, Cloud Build | triggers, jobs, stages | `needs`/`requires`/`dependsOn`, scripts run by steps |
| Jenkinsfile, Makefile | stages, targets | prerequisites, scripts |
| Dependency manifests | packages (runtime and dev), npm scripts | `pkg:` keys |
| Notebooks, settings, Markdown | code cells, keys (`.env` symbol signatures omit values), sections | – |

**Files with no parser** (any other text) become one opaque module unit. In a diff it carries its changed lines, so the
agent can describe it from them.

## 4. Linking

**Module resolution.**
- **Python.** Every module is indexed under all of its dotted suffixes (`pkg.sub.mod`, `sub.mod`, `mod`), so both installed
  packages and `src/` layouts resolve. Relative imports resolve by path, and package `__init__` re-exports are followed for a few hops.
- **JavaScript/TypeScript.** Relative specifiers try every extension and `index.*`; `./x.js` also maps to `x.ts`. `@/` and `~/`
  aliases try `src/`. `export * from` and `export { a as b } from` are followed. Bare package names are external.
- **Other languages.** Type imports resolve through fully qualified names (`com.acme.store.Repo`), namespace and package
  imports make their types visible (Java `import x.*`, C# `using`, PHP `use`), Go imports resolve to a package folder by
  path suffix, Rust `crate::`/`super::` paths to local items, and file imports (`#include "x.h"`, `require_relative`,
  `source`) to files. Types in the same package or namespace are visible without an import.

**Call resolution.** The first rule that matches wins:

| Pattern | Resolves to | Confidence |
|---|---|---:|
| `self.m()` / `this.m()` | method on the class or its resolved bases (also partial classes, Go methods in other files) | 0.95 |
| `name()` defined in the same file | that function or class | 0.95 |
| `name()` imported | the imported symbol | 0.9 |
| `mod.f()`, `ns.f()`, `pkg.F()` (Go) | symbol in the imported module or package | 0.9 |
| `super().m()` | method on a base class | 0.9 |
| `m()` inside a class in Ruby, Kotlin, Swift, … (implicit `self`) | method on the class | 0.9 |
| `Cls.m()`, imported `Cls.m()`, `Type::new()` | that method | 0.85–0.9 |
| `Name()` / `new Name()` resolving to a visible type | that class (a constructor call) | 0.85 |
| `self.attr.m()`, `field.m()` (Java/C#/Kotlin fields and injected constructor parameters) | method on the inferred class of the field | 0.8 |
| a function passed as a callback | that function, only if it resolves strongly | 0.6 |
| `name()` defined once anywhere in the repo | that symbol | 0.5 (0.75 in languages with one global namespace, like shell) |
| `obj.m()` where `m` is a unique, uncommon method name | that method | 0.4 |

Calls on third-party imports, platform types (`String`, `List`, …) and JavaScript globals are never guessed.
Edges below 0.6 confidence are drawn dashed.

**Explicit references.** Config items and routes link through keys rather than calls: an item's `refs` point at other
items' `keys` (a Terraform output at `tf:infra:aws_s3_bucket.logs`, a Service at a label selector, a CI job at
`file:scripts/backup.sh`). These become `refs` edges, shown as "uses" in the diagrams.

**HTTP routes (`routes.py`).** Handlers get a `route:GET /notes/{}` key from decorators and annotations (FastAPI, Flask,
Starlette, NestJS, Spring including class-level `@RequestMapping` prefixes, ASP.NET including `[Route("api/[controller]")]`,
JAX-RS, PHP attributes, Rust attributes) or from registrations (Express/Koa/Fastify/Hono, Go `HandleFunc` and routers,
Laravel, Rails routes and `resources`). Client calls (`fetch`, axios, `requests`, `httpx`) get `api:` and `route:`
references, with module constants substituted (`` `${BASE}/${id}` `` with `const BASE = "/notes"`). Path parameters are
normalized to `{}` and paths match by suffix, so `/api/v1/notes/{}` on the server still matches `/notes/{}` in a client.
The result is one chain across languages: browser → contract → server.

**Entry points** add priority to an item:
- Called from a `__main__` guard or from module scope: +3.
- Serves an HTTP route, is named `main`, or is a script target: +2.
- Lives in a `cli.py`/`index.js`/`main.go`-style file: +1.

**PageRank** over the graph measures importance. It decides what stays visible, breaks ordering ties, and ranks neighbours.

## 5. Mapping the diff onto items

- Each added line goes to the **innermost** item containing it in the new file (a method rather than its class).
- Each removed line is located in the **old** file, which is re-parsed, and then matched to the current item with the same
  qualified name.
- Items present only in the old version are **deleted**; items present only in the new version are **added**.
- Blank lines never mark an item as changed. This matters because adding a method also adds blank lines inside its class.
- Lines outside every item (imports, constants) are recorded as module-level changes.
- Changed items get a short **contextual diff**: their current code with `+` and `-` lines, trimmed to the changed region
  and capped at 30 lines. The explorer, the worksheet and the video's code scenes all use it.
- Added or deleted files with no parser keep their whole text (capped) as their diff.

A class's own status (its header and attributes) is kept apart from the roll-up of its methods, so the counts stay honest:
"15 changed" means 15 items, not 15 plus their parent classes.

## 6. Risk lens

`risks.py` holds about 50 rules, each with a severity, the areas it applies to, a pattern, a plain-language message, and
whether it fires on added or removed lines (a removed `@login_required` is a finding; an added one is not).

- **Diff mode** checks every added and removed line. **Audit mode** (`--repo --audit`) checks whole files for medium and
  high rules; one combined regex per area finds candidate lines first, which keeps the CPython standard library
  (300k lines) to about 6 seconds of scanning.
- **Structural checks** compare old and new parses: a removed endpoint or RPC, a removed or retyped API field, a removed
  column, a new dependency.
- **Noise guards**, found by auditing real projects: comment lines and docs only get secret checks; placeholders
  (`changeme`, `${VAR}`, `os.environ[...]`) are not secrets; `0.0.0.0/0` counts only on source/ingress keys and not under
  an `egress` or route; `DROP TABLE x` is fine in a script that re-creates `x`; `curl … | sh` inside an `echo` or
  `$(warning)` is a hint, not a command.
- **Redaction:** recognized credentials are replaced with `[REDACTED]` before maps, worksheets, labels, caches and
  narration are written. Current and previous source teach the redactor literal values, so copies in summaries are
  also removed. Redaction precedes snippet truncation and preserves schema keys and graph references. Quoted sensitive
  assignments, prefixed keys such as `DB_PASSWORD`, plain config values, provider tokens and private-key bodies are
  recognized. This is pattern-based protection, not a guarantee that arbitrary secrets will be detected. Original source
  and the local `mark` baseline remain unredacted inputs.
- **Audit coverage:** generic settings, docs and generated text are included with `--audit`. Git ignores, excluded
  directories/tests, lockfiles, binaries and the 600 KB per-file limit still apply.

Each flag is attached to the innermost item on its line, rolled up as a ⚠ count on every container above it, listed in
the worksheet for the agent's verdict, and shown in the Markdown table, the explorer's risk panel and the video.

## 7. What gets mapped in diff mode

The map shows the changed items plus their **closest neighbours**: up to 4 callers or callees per change, ranked by
personalized PageRank seeded on the changed items (ties broken by id), with at most 40 in total and only edges of
confidence 0.5 or higher. That keeps the reader oriented ("this new method is called by the API handler") without
pulling in the whole repository. Unchanged members of a changed class are folded into a "+N unchanged" box.

## 8. Layering

**Areas.** When the mapped files span two or more areas, the root's children are area groups in a fixed order: Code, API
contracts, Data & schema, Infrastructure, CI/CD & build, Dependencies, AI & notebooks, Configuration, Docs, Other.
An area with a single unit shows that unit directly. Each area is then layered on its own.

**Units.** Each class (or container: an OpenAPI `paths:` block, a SQL table, a GraphQL type) is a unit. A file's
top-level functions or items form one module unit; a file with no items becomes an empty or opaque module unit.

**Groups** are built recursively:

1. If the files in a group hold 9 units or fewer, the group's children are those units. This is a leaf.
   At the root, more than 4 units are still split by folder, so the first screen always gives orientation.
2. Otherwise the files are partitioned:
   - Single-folder chains are compressed (`src/app/` behaves as one step).
   - The candidate parts are the subfolders and loose files at that level.
   - If there are 9 or fewer parts, done.
   - Otherwise the parts are clustered by who-calls-whom (Louvain modularity on file-level edges). The resolution rises
     (1.0, 1.5, 2, 3, 5) until a dense folder splits into enough parts. The smallest clusters are then merged into their
     best-connected neighbour until at most 9 remain.
3. A part holding a single unit is placed directly instead of being wrapped in a group.
4. Groups with a single child group are flattened, so no view ever shows just one box.

**Naming without an LLM.**
- Areas, folders and clusters: `Infrastructure`, `api/`, `futures.py +8`.
- Classes, files and items keep their identifiers; boxes also say what they are (`job`, `resource`, `Kubernetes manifest`).
  When two boxes in one view would look identical, their folder is added (`react/shallow.ts`).
- Summaries come from the docstring, the config itself ("runs acme/api:1.5.0", "steps: actions/checkout@v4"), or
  structure: "Calls load() and save(). Used by delete_note()."

## 9. Views

Every node with children gets a view.

- **Folding.** When there are more than 9 children, changed, risky, entry-point and high-PageRank children stay visible.
  The rest go into a "+N more" box, which lists them in the explorer.
- **Edges.** Each item-level edge is lifted to the lowest view that separates its two ends: one pass over all edges, with
  lowest-common-ancestor bookkeeping. Kinds: `calls`, `inherits` (dashed) and `refs` ("uses").
- **Ghosts.** A view may show up to 3 dimmed neighbours from outside it, so the reader sees where the flow comes from and goes to.
- **Order.** Strongly connected components are condensed, then sorted topologically. Among nodes that are ready, entry points
  come first, then higher PageRank. Inside a class or module, constructors come first and then the author's source order.

## 10. Labels and the worksheet

Change maps also write `codemap.diff` from every collected text hunk, without the 30-line display cap. Recognized
credentials are redacted using current and previous source; large current files are streamed for redaction even when
excluded from structural parsing. The file is for review, not applying as a patch. Binary/ignored files outside the
collected scope are not included. The explorer advertises it only when metadata says it exists.
Unreadable/oversized snapshot paths are disclosed as skipped rather than mistaken for deletions. If every change
is skipped, analysis reports that it has no readable changes instead of fabricating a map.

Editor handoff uses a relative directory from the output to the checkout. HTML omits the absolute project root and
constructs editor URLs only for local `file:` documents and non-deleted nodes. Links open the current checkout at a
recorded line, so moving files or changing source can invalidate them.

- **Free tier.** No worksheet; deterministic summaries only.
- **Lite.** Folders and clusters, items with no docstring and a short name, items with risk flags, and files with no parser.
- **Full and video.** Every item in a view, plus the changed lines for changed items (8 per item, 12 for files with no
  parser), the risk flags to confirm, and requests for a "why" and per-view captions. The video tier also lists the
  walkthrough beats to narrate.
- **Short ids.** Items get ids like `g1`, `u3`, `f12`, flags `r1`, beats `b1`, to keep both the worksheet and `labels.json` small.
- **Budget.** Items are added in priority order (groups; risky and opaque items; changed units; changed functions;
  context; the rest by PageRank) until the budget runs out. The defaults are 3k tokens for lite, 8k for full and 9k for video.
- **Cache.** Merged labels are stored in `.codemap/cache.json`, keyed by each item's content hash. The next `analyze` reuses them
  and leaves those items out of the worksheet. Container hashes include descendant content; changes refresh parent
  descriptions as well as the changed leaf. Cache format 2 retires the earlier membership-only container hashes.

## 11. The walkthrough (story)

In diff mode, the plan has four parts:

1. **Opening:** a title card, then "why" if known, then the top-level overview.
2. **Tour:** one beat per changed area, saying what changed in it.
3. **Risks:** a "Worth a second look" scene listing the high and medium flags (with the agent's notes), when there are any.
4. **Deep dives:** the biggest changed units, while the time budget (default 2 minutes) lasts. Each dive is the unit's view, its
   most-changed function, and that function's code. Dives are chosen by size of change and presented in workflow order.

A summary closes it. In repo mode, the plan is a tour of the top level and its three most important parts.
Narration is template text built from the labels. The agent can replace any sentence through `labels.json`'s `beats`.

## 12. Outputs

- **`codemap.md`.** A risk table, then one heading and one Mermaid `flowchart LR` per view, written depth-first, with a
  numbered workflow list under each. Level 2 and deeper are wrapped in `<details>`.
- **`codemap.html`.** A single self-contained file with no network access. The IR is embedded as JSON, and a small
  Sugiyama-style layout runs in the browser: longest-path ranks, barycenter ordering, and tall columns wrapped.
  It includes breadcrumbs, a details panel (signature, routes, calls, callers, diff, risk flags), a risk panel, the
  workflow list, light and dark themes, and the walkthrough player. After a video export the player uses the recorded
  narration (embedded MP3 clips); before that, the browser's speech voices. Loading it with `#still=N` renders frame N at
  exactly 1920×1080 for the video; `#v=<view>&n=<node>` opens a view with a box selected.
- **`codemap.mp4`, `codemap.webm`.** See [video.md](video.md).

## Decision log

| Decision | Why | Revisit when |
|---|---|---|
| Standard library only, no tree-sitter | Zero install friction; Python's `ast` is exact; one test suite | Exactness matters more than install friction (tree-sitter-language-pack is MIT and ships prebuilt wheels) |
| One configurable engine for 19 languages instead of 19 parsers | Maps need structure, names and calls, not full syntax trees; one engine is one set of bugs to fix | A language keeps defeating it in real repositories |
| Explicit keys/refs for config and routes | Infrastructure and routes are linked by names, not calls; the same edge machinery then works for everything | |
| Risk rules as conservative line patterns, confirmed by the agent | Deterministic, zero tokens, explainable; the agent filters what is left | Teams need custom rules (then rules as data files) |
| The host agent writes labels; no API key | The agent already has the user's subscription and the session's "why" | CI use without an agent; then add an optional direct API call (batch-priced) |
| Folders first, then Louvain | Folders encode intent; clustering alone produces arbitrary groupings | |
| At most 9 boxes, with folding | Working-memory limit; the explorer gives the details | |
| One self-contained HTML file | Works offline, can be emailed, no server, no CDN | |
| Video made of still scenes | Explainer scenes are mostly static; stills make rendering fast and cacheable | Animated transitions become a priority |
| Piper when installed, OS voices otherwise | Natural speech without a cloud service; Piper is GPL-3.0, so it stays an optional separate install | Kokoro (Apache-2.0) as an opt-in quality voice |
| MP4 with AAC plus an optional VP8/Opus WebM | MP4/AAC plays everywhere except VS Code's preview, which needs WebM with VP8 | |
| `.codemap/` holds a `*` `.gitignore` | Never pollutes the user's repository or git status | |

## Extending

- **A new language.** If it looks like an existing one, add a `Spec` in `parse_generic.py` (comment/string syntax,
  container and function patterns, imports) and its extensions in `formats.py`. Otherwise write
  `parse_<lang>(path, text) -> FileInfo`. Everything downstream is language-agnostic.
- **A new config format.** Add a classifier rule in `formats.py` and a parser in `parse_config.py` that calls
  `Builder.add(...)` with `keys` and `refs`.
- **A new risk rule.** Add a tuple to `risks.RULES` and a test with one true and one false example.
- **A new scene type.** Add a `kind` in `story.plan_beats` and narration in `story.narrate`. Then render it in `scene()` inside
  `assets/viewer.html`. The video export picks it up automatically.
- **Limits.** `MAX_VIEW` and `MAX_GHOSTS` are in `codemap_lib/__init__.py`. Relevance limits (`NEIGHBOURS_PER_CHANGE`,
  `MAX_CONTEXT`) are in `hierarchy.py`.

## Performance (measured)

Windows 11 laptop, Python 3.13, warm file cache, `analyze --repo` with the free tier, including writing all outputs:

| Codebase | Files | Items | Edges | Views | Wall time |
|---|---:|---:|---:|---:|---:|
| Demo notes product (diff, 7 formats) | 26 | 88 | 61 | 30 | 0.8 s |
| CPython `asyncio` (Python) | 33 | 1,088 | 1,196 | 135 | 0.4 s |
| spf13/cobra (Go, CI) | 23 | 314 | 543 | 54 | 0.3 s |
| spring-petclinic (Java, SQL, Kubernetes, CI) | 45 | 397 | 503 | 74 | 0.2 s |
| terraform-aws-modules/vpc (Terraform, CI) | 82 | 1,836 | 2,315 | 86 | 0.6 s |
| zod repository (TypeScript, CI, npm) | 334 | 1,612 | 1,223 | 346 | 2.6 s |
| dotnet/eShop (C#, OpenAPI, Protobuf, NuGet, CI) | 538 | 1,579 | 793 | 630 | 1.5 s |
| Entire CPython standard library | 636 | 17,119 | 19,817 | 2,871 | 6.4 s |

cobra, petclinic, terraform-aws-vpc, zod and eShop were measured with `--audit` on; the standard library takes 12.5 s with it.
The first read of freshly downloaded files can be much slower (antivirus scanning): eShop took 39 s on its first run.
