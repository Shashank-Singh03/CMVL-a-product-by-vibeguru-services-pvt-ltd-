# Offline accuracy corpus

These twelve small cases are authored fixtures, not measurements of real projects. Their expected declarations, dependencies and risk locations were specified from the source examples and the documented ID/rule vocabulary. They were not copied from codemap's parser output. Every case records `"provenance": "authored"`.

Run the corpus from the repository root:

```powershell
python benchmarks/run_accuracy.py
```

The runner works with local fixture files and temporary repositories. It does not install packages, execute fixture application code, contact example hosts, run workflows or deploy infrastructure. The credential-shaped string in `python_diff_risk_controls.json` is an invented, nonfunctional marker.

## What the expectations mean

- `files` is the complete current source snapshot. A diff case also supplies a complete `before` snapshot; files absent from `files` have been deleted.
- `expected.symbols` enumerates source declarations in codemap's stable ID notation: `c:path#Class` and `f:path#qualified.name`. This includes explicit constructors and contract/infrastructure entities. Generated root, folder, module and folding boxes are layout, not source-symbol ground truth. Ordinary resolver metadata such as `tsconfig.json` is excluded from repository-mode source-symbol scoring.
- `expected.edges` enumerates directed dependencies between those repository declarations. It includes explicit calls, typed receiver calls, route/contract references, workflow prerequisites and retained references to deleted declarations. External/platform functions have no repository target and contribute no edge.
- `expected.risks` lists exact `(rule, file, line)` warnings. Empty lists are deliberate negative controls. Whole-repository audit cases cover the medium/high audit rules; they do not certify that infrastructure is production-ready or free of every security issue.
- `notes` state semantic assumptions and boundaries. The source is intentionally compact enough to review each expectation by hand.

For normal cases the symbol and edge lists are exhaustive within this contract, so extra links count as false positives. Unknown callback targets are left unresolved rather than guessed onto same-named globals. In a change map, a retained reference to a removed declaration is useful historical context; it does not imply the current program can run successfully.

## Coverage and known limits

| Case | Main check |
| --- | --- |
| `python_lexical_bindings` | Parameters and comprehension scopes, imported functions, `self` calls |
| `python_deleted_target` | Deleted declarations and retained broken callers |
| `python_dynamic_dispatch_limit` | A real `getattr` target requiring caller-argument propagation |
| `javascript_imports` | Named aliases and namespace imports |
| `typescript_path_alias_limit` | `tsconfig` path mappings with an ambiguous same-name decoy |
| `javascript_openapi_routes` | Client, contract and registered handler links |
| `java_typed_member` | Classes, explicit constructor and concrete field call |
| `go_receivers_and_imports` | Receiver methods and a locally supplied imported package |
| `csharp_typed_member` | Namespaces, explicit constructor and concrete field call |
| `infrastructure_risk_controls` | Unsafe settings plus safe egress/private/unprivileged controls |
| `ci_risk_controls` | Job prerequisites, permissions and moving versus pinned action references |
| `python_diff_risk_controls` | Changed-line warning locations and a pure-function control |

The two `_limit` cases retain correct positive edges that a syntax-based resolver may miss. The dynamic-dispatch case remains a known gap and assumes this closed fixture and invocation through `run`, with no monkey patching or additional caller. The TypeScript case now passes through the declared path mapping; choosing a unique global name cannot substitute for resolving the alias. Failures should remain visible in reported recall. Do not erase expected edges to make current results pass.

This is a small, curated correctness check. It supplies no defensible estimate of accuracy across arbitrary repositories, frameworks or languages. It does not cover generated code, macros, reflection generally, overloaded signatures, complex conditional imports, framework dependency injection, runtime configuration or the quality of AI-written explanations. Authored fixtures also share the author's interpretation of the task; they are not an independent external benchmark or an unbiased sample of real code.

## Extending the corpus

Add one JSON object per file under `cases/`, using the same schema. Keep examples offline and include their positive links as well as opportunities for false matches. Specify expected sets before inspecting codemap's output, explain any semantic ambiguity in `notes`, and keep known misses as misses. When adding real-project measurements, record their upstream source, revision, license, selection procedure and independent annotation method separately; do not label them `authored` or combine their results with these fixtures without an explicit breakdown.

## Real-source checks and regression baseline

```powershell
python benchmarks/run_accuracy.py --stdlib --project .
python benchmarks/run_accuracy.py --baseline benchmarks/baseline.json --fail-on-regression
```

The first command also measures six installed CPython files and this project. Python's AST supplies a separate
reference for every named declaration, including nested and conditional definitions; no project code is executed.
Generated inline route entities are counted separately rather than compared with named declarations. Calls and
risks in these real-source samples have no independent annotations and are explicitly **unscored**.

The CPython subset is selected explicitly in the runner, not sampled randomly. Its report records the Python version
and SHA-256 of normalized UTF-8 source text. CPython source is available under the Python Software Foundation License;
the report contains identities and hashes, not copies of those source files. Local-project results depend on the
checkout and are exploratory; they are not an externally reviewed benchmark. The authored and real-source numbers
remain separate in `results/latest.json` and `results/latest.md`.

The baseline saves individual existing misses, unexpected results and parse problems. A new miss or false result
fails the second command; improving a known gap passes. A changed fixture dataset requires an explicit baseline
refresh, so changing the expectations cannot silently improve the score. After reviewing a deliberate dataset change:

```powershell
python benchmarks/run_accuracy.py --write-baseline benchmarks/baseline.json
```

## Existing local repositories

`run_local_repos.py` runs whole-project maps, whole-project audits and automatic Git scope selection against a JSON manifest of `{ "id": "unique-slug", "path": "absolute/local/path" }` entries:

```powershell
python benchmarks/run_local_repos.py --manifest benchmarks/local-repos/2026-10-23/repos.json --out benchmarks/local-repos/2026-10-23
```

Use a fresh output directory to preserve an earlier run. The output must be outside every target checkout. This does not install dependencies or execute target applications, builds or tests. It fingerprints discovered input files and records Git revisions and working-tree states before and after each project. A failed analysis, parse error, structural invariant error or changed input produces a nonzero exit status.

Each output includes a graph snapshot for comparison with source-first annotations. The named Python declarations have a separate AST reference; dependency and non-Python declaration checks require independently recorded expectations. Graph counts and zero parse errors do not establish semantic correctness. Git projects with one clean commit fall back to a whole-project map; non-Git projects receive no manufactured changes.

The [2026-10-23 local evaluation](local-repos/2026-10-23/findings.md) separates operational checks, Python declaration coverage, limited manual samples, audit triage and video verification. These repositories are a convenience sample of local work, not a representative external benchmark. The manifest uses this machine's paths; use your own paths elsewhere.

For a larger local corpus, use stage deadlines, checkpoint recovery and review-request preparation:

```powershell
python benchmarks/run_local_repos.py --manifest benchmarks/corpus/2026-10-23/available-repos.json --out benchmarks/corpus/2026-10-23/run-next --timeout 180 --resume --review-requests
```

`--timeout` runs each map/audit/change stage in an isolated trusted Python worker. A timed-out stage is recorded and the
corpus continues; target applications are never executed. Checkpoints are written after every stage. `--resume` reuses
completed successful projects only when their source hashes, Git revision/state, application/runner fingerprints and
review-request mode still match. Changed inputs and failed projects are rerun. Large input fingerprints are streamed.

Manifest roots must exist during initial validation. If an input folder or fingerprint becomes unavailable later,
the runner records the affected project with unknown integrity and continues. It never calls missing input unchanged.
The [expanded local corpus](corpus/2026-10-23/README.md) preserves the original 26-path discovery and records the six
paths that became unavailable during the first run; the current accessible manifest has 20 paths. Saved baseline
results used a 120-second deadline; the example above gives a fresh future run 180 seconds.

`--review-requests` prepares `review.md` and `review-request.json` for each successful project map. These are **pending**
reviews, not model-reviewed results. The user's agent must inspect source and write validated corrections separately.
Record actual reviewed files and independent expectations; do not treat parser counts as semantic accuracy. Related
worktrees and copied projects belong to the same leakage group rather than independent accuracy samples.
