# Tokens and cost

codemap is built so that anyone can afford to run it. The free tier spends zero LLM tokens, and the other tiers
send the agent a compact worksheet instead of source files.

## Where tokens go

| Cost | When | Size (measured) |
|---|---|---|
| Skill metadata (name + description) | Always in the agent's context once installed | about 185 tokens |
| `SKILL.md` body | When the skill triggers | about 1.1k tokens in total |
| `references/options.md` | Only if a command fails or the user asks | about 2.4k tokens |
| Command output | Every `analyze`, `render` or `video` call | about 180 tokens (seven short lines) |
| Worksheet (agent input) | Tiers `lite`, `full`, `video` only | see below |
| `labels.json` (agent output) | Tiers `lite`, `full`, `video` only | roughly 35–45 tokens per item |

The scripts themselves never enter the context; only their short output does. The agent is told never to read
`ir.json`, `codemap.html` or `codemap.md` back into the conversation.

## Measured worksheet sizes

"Source" is what reading the mapped files directly would cost, at about 3.6 characters per token. Repository maps use
the default budgets (3k for lite, 8k for full); the last column shows the worksheet with no budget at all.

| Case | Source | `free` | `lite` | `full` | `full`, no cap |
|---|---:|---:|---:|---:|---:|
| Demo change (20 files, +175/−14; 7 areas) | ≈3.4k (the diff) | 0 | 2.4k (53 items) | 5.0k (83 items) | 5.0k |
| CPython `asyncio` (Python, 33 files) | ≈145k | 0 | 3.6k (82 items) | 8.6k (173 items) | 28.9k (735 items) |
| zod `src/` (TypeScript, 134 files) | ≈373k | 0 | 3.5k (93 items) | 8.6k (250 items) | 22.1k (624 items) |
| spring-petclinic (Java, SQL, Kubernetes, CI; 45 files) | ≈29k | 0 | 3.5k (74 items) | 8.5k (182 items) | 13.6k (294 items) |
| terraform-aws-vpc (Terraform, CI; 82 files) | ≈130k | 0 | 3.6k (64 items) | 8.6k (199 items) | 16.7k (391 items) |

The demo's `video` tier adds 14 narration slots to `full` (5.1k). The agent's whole answer for it (83 titles and
summaries, a "why", 10 captions, 7 risk verdicts and 14 narration lines) was about 3.5k tokens.

**What these numbers mean.**
- **Large repositories:** the worksheet is 4–100× smaller than the source, and the budget keeps it bounded however big
  the repository is.
- **Small changes:** the worksheet is about the size of the diff itself. The value there is that the agent writes
  explanations for a structure that is already layered and linked, with risk flags already found, instead of building
  that picture in its head.
- **Same-session use:** if the agent that made the change runs the skill, it already has the code and the "why" in context.
  `lite` is the sweet spot there.

## Tiers

| Tier | Agent writes | Use it for |
|---|---|---|
| `free` | nothing | CI, huge repositories, people with no budget. Names come from identifiers, docstrings and config. |
| `lite` (default for changes) | names and summaries only for unclear items (folders, clusters, undocumented code, files with no parser, risky items) | Everyday "what did you change?" |
| `full` | every item plus "why", view captions and a verdict per risk flag, with changed lines included | Reviews and onboarding |
| `video` | full plus one spoken sentence per scene | When a narrated video is wanted |

`--budget N` caps any worksheet. Items are added in priority order (groups, items with risk flags or no parser, changed
classes and modules, changed functions, context, then the rest by PageRank) until the budget is used. Whatever is left
keeps its free label.

## Techniques, by impact

1. **Diff scoping.** Only changed items and their closest neighbours are mapped.
2. **Signatures instead of bodies.** The worksheet holds signature, docstring, calls and called-by. Only changed items
   get their changed lines, capped at 8 per item (12 for files with no parser, which have nothing else to go on).
3. **Free labels first.** Docstrings, identifiers and config (an image name, a job's steps, an endpoint's summary)
   name a lot already; `lite` asks only about the rest.
4. **Content-hash cache.** Labels are stored by the hash of the code they describe, so a re-run asks only about items
   whose code changed. Because the map is deterministic, the same code always maps to the same items.
5. **Short ids and terse JSON.** `g1`, `u3`, `f12`, `r1` and `["Title", "Summary"]` pairs keep the output small.
6. **Hard budget.** The worksheet never grows past its cap, however big the repository.
7. **Subagent delegation (Claude Code).** For big worksheets, the agent can hand labelling to a subagent on a small model.
   That moves about 10k tokens out of the main conversation, which keeps it below compaction and plan limits.

## Cost estimates

Prices below are as reported by our research agent on 2026-10-13, per million tokens, input / output.
Check [the pricing page](https://platform.claude.com/docs/en/about-claude/pricing) before relying on them.

- Haiku 5.5: $0.10 / $0.50
- Sonnet 5.5: $2 / $10
- Opus 5.5: $4 / $20

Each row adds the skill overhead (about 1.5k input tokens) to the measured worksheet. Output is estimated at about 40 tokens per item.

| Case | Input | Output | Haiku 5.5 | Sonnet 5.5 | Opus 5.5 |
|---|---:|---:|---:|---:|---:|
| Demo change, `video` | ≈6.6k | ≈3.5k | ≈$0.002 | ≈$0.05 | ≈$0.10 |
| asyncio, `lite` | ≈5.1k | ≈3.3k | ≈$0.002 | ≈$0.04 | ≈$0.09 |
| asyncio, `full` | ≈10.1k | ≈7k | ≈$0.005 | ≈$0.09 | ≈$0.18 |
| Any repo, `free` | ≈1.5k | ≈0.2k | ≈$0 | ≈$0.005 | ≈$0.01 |

The only overhead in the `free` row is the skill text and command output.
Inside Claude Code on a subscription, the real currency is context space and rate limits rather than dollars, which is
why the skill keeps its own text small and prints only a few lines.

## Comparison

The research for this project found a well-known LLM-heavy alternative that used about 200k tokens and 30 minutes on a
single demo repository (see [research.md](research.md)). codemap's design point is the opposite: build the structure
deterministically, then spend a few thousand tokens on naming.
