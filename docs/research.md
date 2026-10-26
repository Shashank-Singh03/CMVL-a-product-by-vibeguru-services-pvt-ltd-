# Research behind codemap

Two research passes were run on 2026-10-13 before building v0.1:

1. Feasibility and architecture.
2. Tokens, video and time.

This file consolidates what they found and how it shaped the design. Platform support and prices change quickly, so
re-check the linked sources before relying on a specific number.

**Evidence labels:**
- **[measured]** run on the development machine: Windows 11, i5-13500HX, 16 GB RAM, no GPU.
- **[source]** taken from the linked documentation.
- **[judgment]** an opinion.

## 1. Verdict

It is feasible, and most building blocks already exist:
- error-tolerant parsers;
- def/ref extraction;
- PageRank for code ranking (aider's repo map);
- community detection;
- Mermaid;
- headless-browser rendering;
- offline TTS;
- a cross-agent skill format.

What nobody shipped was this combination: **deterministic-first and token-frugal, with views sized for working memory,
focused on an agent's session diff, and one intermediate representation driving text, HTML and a narrated video.** [judgment]

The hard parts are:
- **Call resolution** without a language server. Dynamic dispatch, dependency injection and event buses defeat name matching.
- **Layers that match human intuition.** This is the core intellectual property, and it is hard to evaluate.
- **Real data flow.** It has to be approximated from calls and signatures.
- **"Why"**, which needs the session transcript or commit messages.
- **Video weight.** A browser, ffmpeg and possibly a voice model.

## 2. Agent platform support for `SKILL.md`

Anthropic published Agent Skills as an open standard ([spec](https://agentskills.io/specification)). It requires `name`
(at most 64 characters, matching the folder name) and `description` (at most 1024 characters). Optional fields are
`license`, `compatibility`, `metadata` and an experimental `allowed-tools`.
Disclosure is progressive: about 100 tokens of metadata always, then a body of under 5k tokens and 500 lines, then
resources on demand. [source]

| Agent | Skill folders | Notes [source] |
|---|---|---|
| Claude Code | `.claude/skills/`, `~/.claude/skills/`, plugins | Extra frontmatter fields exist, but using Claude-only fields elsewhere can break. On Windows without Git Bash the shell is PowerShell. ([docs](https://code.claude.com/docs/en/skills)) |
| OpenAI Codex (CLI, IDE, app) | `.agents/skills` (searched up to the repo root), `~/.agents/skills` | Initial skill list capped at 2% of context or 8,000 characters ([docs](https://learn.chatgpt.com/docs/build-skills)) |
| Gemini CLI | `.gemini/skills` or `.agents/skills`, plus user folders | Activation asks the user for consent ([docs](https://geminicli.com/docs/cli/skills/)) |
| GitHub Copilot (cloud agent, CLI, VS Code and JetBrains agent mode) | `.github/skills`, `.claude/skills`, `.agents/skills` | ([docs](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills)) |
| Cursor | `.agents/skills`, `.cursor/skills`; also reads `.claude/skills` and `.codex/skills` | ([docs](https://cursor.com/docs/context/skills)) |
| Others with documented support | Junie, OpenCode, Goose, Amp, Kiro, Roo Code, Factory, OpenHands, Tabnine | Distribution helper: [`npx skills add`](https://github.com/vercel-labs/skills) |

**Decision:** ship one portable `SKILL.md` that uses only spec fields, install it to `.claude/skills` and `.agents/skills`
(see `install.py`), avoid Claude-only `!command` injection, and make every command work in both bash and PowerShell 5.1.

## 3. Prior art

| Tool | What it does | What we reused | Gap |
|---|---|---|---|
| aider repo map ([1](https://aider.chat/docs/repomap.html)) | tree-sitter def/ref graph, ranked to fit a 1k-token budget | The rank-then-budget idea | Built for the LLM's context, not for people |
| Repomix `--compress` ([guide](https://repomix.com/guide/code-compress)) | Signatures without bodies | The "skeleton" worksheet idea | No graph, no views |
| GitDiagram ([repo](https://github.com/ahmedkhaleel2004/gitdiagram)) | LLM reads the file tree and README and produces Mermaid | Mermaid output | No static analysis, no diffs |
| CodeBoarding (MIT) ([repo](https://github.com/CodeBoarding/CodeBoarding)) | Language-server analysis, LLM names components, nested diagrams, PR maps | Closest philosophy | Needs an LLM API key, language servers, Python 3.12; not a skill; no video |
| Understand-Anything (MIT, 86k★) ([repo](https://github.com/Lum1104/Understand-Anything)) | tree-sitter plus 5 LLM agents, layered dashboard, `/understand-diff` | Strongest competitor | One demo run took about **200k tokens and 30 minutes** ([Better Stack](https://betterstack.com/community/guides/ai/understand-anything/)); views not capped; no video |
| GitNexus ([repo](https://github.com/abhigyanpatwari/GitNexus)) | Knowledge graph, Leiden clusters, change detection over MCP | Ideas only | PolyForm Noncommercial license; aimed at agents, not people |
| DeepWiki, CodeRabbit walkthroughs | Hosted wiki; PR walkthrough with sequence diagrams | The diagram-per-change user experience | Hosted (SaaS), LLM-heavy, flat |
| Cline "Explain Changes" ([docs](https://docs.cline.bot/features/explain-changes)) | Inline explanations on diffs | — | **Deprecated**, a sign that diff annotations alone don't stick |
| CodeSee, Sourcegraph Cody, Swimm | Visualization and documentation tools | They validate "static analysis + controlled LLM" | Standalone visualization tools struggled commercially |
| HyperFrames `pr-to-video` (Apache-2.0) ([skill](https://raw.githubusercontent.com/heygen-com/hyperframes/main/skills/pr-to-video/SKILL.md)) | PR to narrated MP4 | Validates the format | Picks 2–4 diff hunks with no structural analysis; no layered walkthrough |

## 4. Parsing options

- **tree-sitter-language-pack 1.22.1** (MIT, 372 grammars) [measured]:
  - Installed as a prebuilt wheel on Windows with no compiler.
  - The first use downloaded a 22 MB platform bundle.
  - After that, parsing plus the tags query took 7 ms per file.
  - Linux wheels require glibc 2.34 or newer.
  - Tags queries exist for Python, JS, TS, TSX, Go, Java, Rust, C#, PHP, Ruby, Kotlin and C++.
- **Language servers and SCIP** are precise but heavy to install and slow to index (the CodeBoarding route).
  ctags gives definitions only. GitHub's stack-graphs was archived in September 2025. [source]
- **networkx 3.7** has native Leiden and Louvain, but `nx.pagerank` needs numpy. [measured]

**v0.1 decision:** standard library only. Python uses `ast`, which is exact. JS and TS use a dependency-free scanner.
PageRank and Louvain are implemented in about 100 lines. tree-sitter is the planned path to more languages.

## 5. How big should a "human chunk" be?

- Miller's 7±2 ([doi](https://doi.org/10.1037/h0043158)) and Cowan's 4±1 ([doi](https://doi.org/10.1017/S0140525X01003922)).
  **Decision:** a target of 4–7 boxes per view, a hard maximum of 9, and about 12 or fewer edges.
- The C4 model ([c4model.com](https://c4model.com/)) uses system, container, component and code levels. codemap's
  layers (system, groups, units, functions) mirror it, but adapt their depth to the code's size.
- Signature-only summaries cost about 75% fewer tokens for about 23% lower summary quality at module level
  ([arXiv 2503.10737](https://arxiv.org/abs/2503.10737)). That is why codemap sends signatures plus changed lines only.
- Anthropic's study of AI-assisted coding found AI-assisted developers scored about 50% versus 67% on comprehension
  ([study](https://www.anthropic.com/research/AI-assistance-coding-skills)). This is the problem codemap targets, and a
  model for how to evaluate it.

## 6. Tokens

**How skill disclosure costs out in Claude Code** [source: [skills docs](https://code.claude.com/docs/en/skills)]:
- The description is truncated at 1,536 characters.
- The whole listing gets 1% of the context window.
- The body stays in context after it triggers.
- After compaction, the first 5k tokens of each invoked skill are re-attached.

**Measured skeleton ratio** [measured]: on CPython's standard library, signatures plus first docstring lines are 12–15%
of the source.

**Prices** [source: [pricing](https://platform.claude.com/docs/en/about-claude/pricing), as reported 2026-10-13]:

| Model | Input / output per million tokens |
|---|---|
| Haiku 5.5 | $0.10 / $0.50 |
| Sonnet 5.5 | $2 / $10 |
| Opus 5.5 | $4 / $20 |

Cache reads cost about 0.1× of input, and the Batch API is about 50% off.

**Ranked techniques:** diff scoping, skeletons, content-hash caching, free labels for self-explanatory names, model
routing (small models for leaves), hierarchical summaries, terse JSON, subagent isolation, and batch pricing for CI.
See [tokens.md](tokens.md) for codemap's own measured numbers.

## 7. Video: rendering stacks

All measured on the development machine with the same scene (a 40-node SVG graph, a highlighted code panel and captions),
2 minutes at 1080p30 [measured]:

| Stack | License | Install | 2-minute render | Notes |
|---|---|---|---:|---|
| Remotion 4 | Free for individuals and companies of 3 or fewer; otherwise a paid company license ([license](https://www.remotion.pro/license)) | ~514 MB | 110 s | Excellent and mature; licensing rules it out as a free default |
| HyperFrames 0.8 | Apache-2.0 ([repo](https://github.com/heygen-com/hyperframes)) | ~120 MB plus Chrome | 171 s | HTML/GSAP; agent skills included; telemetry on by default |
| Custom HTML + headless Chromium + ffmpeg | Apache/MIT | ~53 MB plus system Chrome | 86 s | Fastest browser path; full control over caching |
| Revideo 0.11 | MIT | — | not measured | Headless rendering API |
| Motion Canvas 3.17 | MIT | — | — | No headless rendering; last release February 2025 |
| Manim CE 0.22 | MIT | ~370 MB | ≈630 s | Beautiful but slow |
| Pillow frames to ffmpeg | permissive | ~10 MB | 48 s | Lower quality text |
| Stills plus ffmpeg crossfades | LGPL/GPL ffmpeg | ffmpeg | 29 s | Very robust |
| Playwright `recordVideo` | Apache-2.0 | ~150 MB | ≥120 s | Real-time only, 800 px default, drops frames |

**Speedups found:**
- Hold static frames instead of re-rendering them, which is about 5× faster.
- Cache scene segments.
- Use separate browser processes, not tabs: 4–6 processes reached 31–42 fps.
- 720p is 1.6–2.4× faster.
- Time each scene from its audio.
- Pass `--disable-background-networking --disable-component-update` to Chrome, which avoided random stalls.

**v0.1 decision:** HTML scene templates, captured as stills by a headless browser and held by ffmpeg. This is the
research's recommended default, reduced to its fastest form. The cost is no continuous animation in the MP4.

## 8. Video: text-to-speech

All on the same 330-word narration, about 2 minutes of audio [measured]:

| Engine | License | Size | Time | Quality | Cost |
|---|---|---|---:|---|---|
| OS voices (SAPI / `say` / espeak-ng) | built in | 0 | 0.28 s (SAPI) | robotic | $0 |
| Piper 1.8 | **GPL-3.0** | ~150 MB | 27 s | good | $0 |
| Kokoro-82M (onnx) | Apache-2.0 weights | 326 MB fp32 | 76 s (fp32 was faster than int8 on x86) | best local | $0 |
| Coqui XTTS-v2 | non-commercial | ~2 GB | slower than real time | very good | no commercial use |
| edge-tts | unofficial client, unclear terms | ~1 MB | network | very good | opt-in only |
| OpenAI TTS | paid API | 0 | network | good | ≈$0.015/min |
| ElevenLabs | paid API | 0 | network | best | ≈$0.036–0.07/min |

**v0.1 decision:** OS voices by default (zero install, instant). The browser player uses browser voices.
Kokoro is the planned quality option.

## 9. Time budget (laptop, no GPU)

| Stage | Research estimate | codemap v0.1 measured |
|---|---|---|
| Parse and graph 50k lines of code | 2–5 s | asyncio (≈15k lines) 0.5 s; whole standard library (636 files) 5.5 s |
| LLM labelling | 5–15 s for a diff, 30–90 s for a repo | depends on the agent; worksheets 1–9k tokens |
| TTS, 2 minutes | SAPI 0.3 s, Piper 27 s, Kokoro 76 s | SAPI 0.7 s for 107 s of audio |
| Render, 2 minutes at 1080p | 29–171 s depending on stack | 13.8 s for a 107 s walkthrough (9.7 s of stills, 3.4 s of encoding) |

## 10. Risks the research flagged

- **Trust:** wrong edges erode trust. codemap's answer: the LLM never creates edges, every edge has a confidence,
  weak edges are dashed, and every node links to its source lines.
- **Competition:** Understand-Anything could add a frugal mode. codemap's answer: cost, session-diff focus and video.
- **Platform drift:** skill folders have already changed once (Codex). codemap's answer: `install.py` keeps the target
  table in one place.
- **Windows specifics:** the Microsoft Store `python` alias, CRLF line endings, path separators, read-only git objects.
  All four were hit and handled in v0.1.
- **Repository noise:** generated and vendored code. codemap's answer: git ignore rules, skip lists, and generated-file markers.
