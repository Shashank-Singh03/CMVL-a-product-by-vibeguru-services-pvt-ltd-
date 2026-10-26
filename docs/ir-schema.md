# Data formats

Everything codemap writes lives in `<repo>/.codemap/`. That folder contains a `.gitignore` of `*`, so it never shows up in
`git status`, and it is safe to delete at any time.

| File | Written by | Read by | Purpose |
|---|---|---|---|
| `ir.json` | `analyze`, `render` | `render`, `video` | The intermediate representation: the whole map |
| `worksheet.md` | `analyze` (tiers other than free) | the agent | Compact list of items to name and risk flags to confirm |
| `labels.json` | the agent | `render` | Titles, summaries, why, captions, risk notes, narration |
| `review-request.json`, `review.md` | `analyze --review` | the agent | Current source/graph fingerprint, inventory and correction instructions |
| `review.json` | the agent | `review` | Source-backed symbol/link proposals and explicit uncertainty |
| `review-report.json`, `review.applied.json` | `review` | people | Accepted/rejected corrections, source evidence and removal audit; redacted proposal copy |
| `labels.previous.json` | `analyze` | nobody | The previous answers, moved aside because their short ids may not match a new analysis |
| `cache.json` | `render` | `analyze` | Agent labels keyed by content hash, reused on re-runs |
| `baseline.json`, `baseline/` | `mark` | `analyze` | Session baseline: a git snapshot id or copies of the files |
| `codemap.md`, `codemap.html`, `codemap.mp4`, `codemap.webm` | `render`, `video` | people | The outputs |
| `codemap.diff` | `analyze`, `render` (redaction) | people | All collected changed text lines, without the per-symbol cap; redacted and for review only |
| `video/` | `video` | `video`, `render` | Cached audio clips (`a_*.wav`, `a_*.mp3`), stills (`s_*.png`), `narration.json` (which clip belongs to which sentence), the joined narration, the frame list and encode stamps |

## `ir.json`

Optional `review` metadata records status, reported inspected files, accepted symbols/edge operations, rejection reasons
and uncertainty. Recovered nodes carry `provenance`; callers carry `review_links` with model reasons and exact evidence.
`stats.coverage` discloses files without extracted declarations and omitted candidates. Internal `analysis` options pin
the original scope and baseline identity for `review`; they are excluded from the HTML payload along with `root`.
See [model-review.md](model-review.md) for the correction protocol and limits.

Optional `artifacts` metadata advertises `diff: "codemap.diff"` and a plain relative `source_base` directory.
The HTML renderer accepts only safe relative metadata and omits the absolute `root`. Local `file:` maps use
`source_base` for editor links at recorded lines; hosted maps and deleted nodes have no editor link. Moving the map
away from its original layout can break those links. Keep `codemap.diff` beside the HTML/Markdown when sharing.

```jsonc
{
  "version": "0.2.0",
  "generated": "2026-10-14 16:24",
  "repo": "notes-app",                   // folder name (or sub-folder when a path was given)
  "root": "/abs/path/to/repo", "sub": "",
  "scope": {
    "mode": "diff",                      // "diff" | "repo"
    "label": "uncommitted changes vs HEAD 0b176be",
    "reason": "auto: working tree has changes",
    "base": "0b176be…",                  // commit the diff is against (git)
    "files": 20, "add": 175, "del": 14   // every changed file
  },
  "tier": "video",
  "stats": { "files": 26, "symbols": 88, "edges": 61,
             "languages": {"python": 9, "java": 3, "kubernetes": 1, "terraform": 2, "…": 1},   // format per file
             "domains": {"code": 16, "infra": 5, "api": 1, "ci": 1, "deps": 1, "docs": 1, "other": 1},
             "routes": 6,                // HTTP routes found on handlers and contracts
             "risks": {"high": 2, "medium": 3, "low": 2},
             "parse_errors": [], "parse_seconds": 0.2, "external": {"dataclasses": 2} },
  "nodes": { "<id>": { /* see below */ } },
  "views": [ { /* see below */ } ],
  "plan":  [ { /* planned beats, see below */ } ],
  "beats": [ { /* the same beats with narration text, built by render */ } ],
  "risks": [ { /* risk flags, see below */ } ],
  "why": [ {"source": "agent", "text": "…"}, {"source": "commits", "text": "…"} ],
  "commits": ["Add note deletion\n\nbody…"],
  "other_files": [ {"path": "package-lock.json", "status": "modified", "add": 30, "del": 2, "note": "lockfile"} ],
  "short": { "g1": "g:@code", "u16": "c:app/service.py#NoteService", "f35": "f:app/service.py#NoteService.delete_note" }
}
```

### Node ids

| Prefix | Kind | Example |
|---|---|---|
| `root` | the whole map | `root` |
| `g:@` | area group, at the top level when a change spans several areas | `g:@code`, `g:@infra` |
| `g:` | group (a folder or a cluster of files) | `g:app/`, `g:src/futures.py+8` |
| `c:` | class, or a container such as a struct, an interface, an OpenAPI `paths:` block or a SQL table | `c:app/service.py#NoteService` |
| `m:` | module unit (a file's top-level functions or items) | `m:app/cli.py`, `m:deploy/k8s.yaml` |
| `f:` | function, method, or config item (job, resource, endpoint, package, …) | `f:deploy/k8s.yaml#Deployment/notes-api` |
| `…~more` | folded box | `c:app/api.py#NotesAPI~more` |

Paths are always POSIX-style and relative to the repo root. Duplicate names inside a file get `~2`, `~3` (for example a property setter).

### Node fields

| Field | On | Meaning |
|---|---|---|
| `kind` | all | `root`, `group`, `class`, `module`, `function`, `method`, `more` |
| `name` | all | Display identifier (`NoteService`, `delete_note()`, `api/`, `Deployment/notes-api`) |
| `title` | all | Plain-English title from the agent, or absent |
| `sum` | all | One-line summary |
| `lsrc` | all | Where `sum`/`title` came from: `agent`, `cache`, `doc` (docstring) or `auto` (structure) |
| `sub` | all | What the box is, in words: `class`, `interface`, `job`, `resource`, `endpoint`, `Kubernetes manifest`, `4 files`, … |
| `domain`, `lang` | code and file nodes | Area (`code`, `api`, `data`, `infra`, `ci`, `deps`, `ai`, `config`, `docs`, `other`) and format (`java`, `terraform`, `deps-npm`, …) |
| `opaque` | module units | `true` when codemap has no parser for the file; `diff` then holds its changed lines for the agent |
| `parent`, `children` | all | The tree |
| `view` | nodes with children | Id of the view that shows this node's children |
| `status` | all | `added`, `modified`, `deleted`, `context` (unchanged neighbour, diff mode) or `same` (repo mode). Rolled up for containers. |
| `own_status` | classes, modules | The node's own change, excluding its children (used for honest counts) |
| `add`, `del` | all | Changed lines (rolled up for containers; blank lines excluded) |
| `changed` | all | Number of changed symbols at or below this node |
| `qual`, `file`, `line`, `end` | code nodes | Qualified name and location (for deleted symbols, the old line numbers) |
| `sig`, `doc` | code nodes | Signature (or first line) and first docstring sentence or generated description |
| `diff` | changed nodes | Up to 30 lines, each starting with `+`, `-` or a space |
| `routes` | handlers, endpoints | HTTP routes served, normalized: `DELETE /notes/{}` |
| `calls`, `callers` | functions and items | Up to 8 `[id, qualified name]` pairs, by importance |
| `flags`, `risk` | all | Ids of the risk flags on this node, and the number of flags at or below it |
| `rank`, `entry` | all | PageRank and entry-point score (used for ordering and folding) |
| `hidden` | classes, modules, folded boxes | `[id, name]` of members not shown (unchanged, in diff mode) |
| `members` | folded boxes | Ids folded into this box |
| `files`, `path` | groups | Number of files and folder path |
| `methods`, `bases`, `functions` | classes, modules | Counts and raw base-class names |
| `hash` | code nodes | Content hash used by the label cache |

### Views

Optional `review_edges` entries `[from_box, to_box, model_link_count]` identify model contributions after grouping
or folding. Such arrows stay dashed even if a grouped parser link has higher confidence. The main `edges` array
retains its existing five-field format.

```jsonc
{
  "id": "v7", "node": "g:app/", "level": 2,
  "children": ["c:app/api.py#NotesAPI", "c:app/cache.py#LRUCache", "…"],   // at most 9, including a folded box
  "ghosts": ["g:@api", "g:web/"],                                          // up to 3 dimmed neighbours from outside the view
  "edges": [["c:app/api.py#NotesAPI", "c:app/service.py#NoteService", 4, 0.95, "calls"]],  // [from, to, weight, confidence, kind]
  "order": ["m:app/cli.py", "c:app/api.py#NotesAPI", "…"],                 // workflow order (callers first)
  "caption": "Delete flows from the HTTP handler through the service to the file…"   // optional, from the agent
}
```

`views[0]` is always the root. Edge `kind` is `calls`, `inherits` or `refs` (a reference that is not a call: a route,
an infrastructure dependency, a schema, a job dependency). Edges below 0.6 confidence are drawn dashed.

### Beats

```jsonc
{"kind": "overview", "view": "v21", "focus": ["f:…delete_note", "…"], "node": "c:app/service.py#NoteService",
 "sid": "b11", "say": "optional agent sentence", "text": "final narration", "est": 7.4}
```

`kind` is one of `title`, `why`, `overview`, `risks`, `focus`, `code` or `outro`. `plan` keeps the structure; `render`
rebuilds `beats` from it, which means a "why" beat appears as soon as a why is known.

### Risk flags

```jsonc
{"id": "r2", "rule": "k8s.privileged", "sev": "high",            // "high" | "medium" | "low"
 "msg": "Runs a container privileged, with full access to the host.",
 "file": "deploy/k8s.yaml", "line": 21,
 "text": "privileged: true",                                     // the line, with any secret value masked
 "note": "Real issue: nothing in the API needs host access; …"}  // optional, from the agent
```

Flags are sorted by severity, then file and line. Their ids are what the worksheet and `labels.json` use.

## `worksheet.md`

The worksheet is plain text written for an LLM to read. It has five parts:

```text
# codemap worksheet | tier: video | 83 items
Task: give each item a plain-English title and a one-line summary so a newcomer understands this code.
Scope: uncommitted changes vs HEAD 0b176be | 20 files +175 -14

## Views (top-down; each line is one diagram)
v0 L0 notes-app: g1 g2 g3 u1 u2 u3 u4
v3 L1 g3: u8 u9 u10 u11

## Items (id kind where | signature | status | facts; diff lines indented)
f13 deployment Deployment/notes-api deploy/k8s.yaml:1 | apiVersion: apps/v1 | MODIFIED +4 -2 | used by: f14 | doc: runs acme/notes-api:1.5.0.
    -  replicas: 2
    +  replicas: 3
    ! r2 HIGH k8s.privileged: Runs a container privileged, with full access to the host. (line 21)
u4 file docs/runbook.txt (text file; codemap has no parser for it: describe it from these lines) | ADDED +4
    +RUNBOOK: restoring notes from a backup

## Narration beats (one spoken sentence each, <= 25 words; describe, never read code aloud)
b10 risks
b12 focus f35

## Risk flags (deterministic rules; confirm each, or say why it is a false positive)
r2 HIGH deploy/k8s.yaml:21 k8s.privileged: Runs a container privileged, with full access to the host.

## Reply: write labels.json beside this worksheet, then render using this output directory
…
```

## `labels.json` (the agent's answer)

```json
{
  "why": "Adds note deletion end to end, a read cache, per-tag statistics in the Java service, and a nightly backup.",
  "items": {
    "g3": ["Infrastructure and deployment", "Container image, local compose setup, Kubernetes deployment and backup storage."],
    "f35": ["Delete a note", "New: removes a note from storage and clears the cache; reports whether it existed."]
  },
  "views": {"v3": "Packaging, local setup, the cluster and new cloud storage all changed."},
  "risks": {"r2": "Real issue: nothing in the API needs host access; remove privileged mode."},
  "beats": {"b10": "Review these first: a privileged container, a public backup bucket and a renamed field."}
}
```

**Accepted forms:**
- Item values may be `["title", "summary"]`, `{"title": …, "sum": …}`, or a plain summary string.
- Item keys may be short ids (`f35`) or full node ids (`f:app/service.py#NoteService.delete_note`); view captions may be
  keyed by view id (`v3`) or by the view's node id (`g:@infra`). Full ids survive re-analysis, which is how
  `assets/demo-labels.json` works.
- Unknown ids are reported by `render` and ignored.

## `cache.json`

`{"2:<content hash>": ["title", "summary"]}`. The leading `2:` is the cache format version.
Version 1 labels are refreshed once because their container hashes could miss behavior changes.

The hash depends on what is labelled:
- **Functions and config items:** their source.
- **Classes:** signature, docstring and every method's content, including methods hidden in a focused view.
- **Modules:** the whole source file, including imports and constants outside functions.
- **Groups:** child ids and content hashes, propagated from descendants before visual folding.

Editing a function's body invalidates its label and the descriptions of containers that can describe that behavior.
Unchanged items still reuse their labels. Labels and video frames use stable content hashes.
