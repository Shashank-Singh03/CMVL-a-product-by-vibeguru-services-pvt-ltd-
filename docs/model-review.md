# Source-backed structural review

Codemap first extracts a graph with local parsers. Your existing coding agent can inspect the actual source and propose
missing declarations, missing relationships or removal of incorrect relationships. This works with the model already
running in your agent; codemap does not call a model API or require a separate key.

```powershell
python skills/codemap/scripts/codemap.py analyze <project> --repo --review --tier full
# Your agent reads .codemap/review.md and source, then writes .codemap/review.json.
python skills/codemap/scripts/codemap.py review <project>
# Read the regenerated worksheet, write labels.json, then:
python skills/codemap/scripts/codemap.py render <project>
```

`--out <folder>` is supported by every step. Use the same output folder throughout. Non-free tiers request structural
review by default; `--no-review` disables the request. Free mode stays parser-only unless `--review` is explicit.
Writing a request does not run a model: the skill instructs the user's agent to do the review.

## Evidence and validation

`review-request.json` records the current normalized source hashes, file inventory, declaration IDs/spans and graph
edges. Its deterministic `analysis_id` binds the proposal to that source and graph. `review.md` supplies instructions,
prioritized files and the response schema. Complete source files remain in the checkout, outside the request.

The agent reads relevant source, imports, scopes and targets, then writes:

```json
{
  "version": 1,
  "analysis_id": "copy the exact ID from review.md",
  "reviewed_files": ["ui.jsx"],
  "symbols": [],
  "edges": [],
  "uncertain": []
}
```

Empty correction lists are valid. `reviewed_files` is the agent's reported inspection scope, not measured reading
telemetry. A partial review must not be described as whole-repository verification.

Symbol additions supply `kind`, `name`, qualified `qual`, `file`, inclusive `line`/`end`, `certainty`, `reason` and
`evidence`. Methods also supply the enclosing class ID as `parent`. Only named class/function/method declarations
in current mapped files can be added; deleting/rewriting existing symbols is outside this initial correction format.

Relationships supply `op` (`add` or `remove`), exact `from`/`to` symbol IDs, `kind` (`calls`, `refs`, `inherits`),
`certainty`, `reason` and `evidence`. Evidence entries have `file`, one-based `line`, optional inclusive `end_line`
and `quote`. Quotes reproduce complete exact source lines, including indentation. Relationship quotes must lie
within the caller's current source span. Existing and same-patch accepted symbols can be endpoints.

Only `certainty: "supported"` proposals can apply. Uncertain proposals remain observations, never graph arrows.
The separate `uncertain` list accepts `{ "file": "ui.jsx", "line": 10, "reason": "..." }` observations.
Use it for dynamic dispatch, missing runtime configuration or insufficient evidence.

Validation rejects mismatched source/graph fingerprints, unsafe or unknown paths, deleted targets, incorrect quotes,
invalid spans/parents/identities, conflicting proposals and dangling endpoints. Confidence is fixed by the tool;
model-supplied confidence cannot upgrade a proposal. A new symbol does not silently cause additional name-based
links: relationships require their own accepted proposals.

## What the result means

Matching source text and positions does **not** prove a model's interpretation. Accepted additions carry
`source-backed model inference` provenance, with their reasons and evidence. New model links use heuristic
confidence `0.55` and appear dashed. That value is a display convention, not a calibrated probability.
Grouped arrows that include model contributions also stay dashed and disclose those contributions, even when
another relationship in the group has higher parser confidence.

The viewer shows parser-only, pending or reviewed status; files without extracted declarations; omitted candidate
files; reported review scope; accepted, rejected and uncertain counts; and evidence on recovered symbols/links.
The original parser confidences are retained. Removing an edge records its original kind, weight and confidence
in `review-report.json`. Rejected proposals stay in the report with reasons and do not alter the graph.

In a change map, a model correction changes the structural interpretation. The existing diff attribution is retained:
lines already assigned to a parsed item or module are not automatically transferred to a recovered symbol. Recovered
symbols can therefore appear as context even when their source file changed; the panel explains this and retains
the original diff for inspection. This avoids claiming that a newly discovered graph node is necessarily newly added code.

Accepted corrections regenerate the map and labeling worksheet. Label cache hashes include both incoming and outgoing
relationships, so structural corrections invalidate affected explanations. Read the fresh worksheet before labeling:
short IDs can change. `render` and video export preserve the reviewed graph already stored in `ir.json`.

## Re-running and errors

`review` reparses the original scope rather than reselecting a new automatic Git scope. Session baselines retain their
exact snapshot semantics, including marked untracked files. Baseline metadata and copied snapshots are fingerprinted;
changing them invalidates the review. A custom output subtree is excluded from its own discovery/audit.

A stale or invalid overall request fails before replacing the existing map. Individual rejected proposals produce a
report and exit code **2**; valid proposals in that review still apply. Correct the rejected items and rerun, or record
their uncertainty honestly. Exit **0** means no proposals were rejected, not that all source is correct.

Each fresh `analyze` starts from parser results and generates a new pending review. Previous model corrections are
not silently carried forward. Reapply a still-current proposal only after checking its source and fingerprint.
Recognized credentials are redacted from stored review prose/evidence and outputs. Redaction can change a quote, so
replaying such a saved proposal may require fresh exact evidence from source. Original source and external proposal
files supplied with `--input` are not rewritten.
