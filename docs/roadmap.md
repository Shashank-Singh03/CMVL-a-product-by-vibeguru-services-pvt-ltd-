# Roadmap

v0.2 is still a draft for trying the idea out. It added every language, the non-code areas (API, infrastructure, CI,
data, dependencies), the risk lens and real audio. Below is what we'd do next, roughly in order of value.

## Done in v0.2

- [x] Every mainstream language through one configurable engine, plus API contracts, Docker/Kubernetes/Terraform/Bicep/
  CloudFormation, CI pipelines, SQL/Prisma, dependency manifests, notebooks, settings and docs (see the [CHANGELOG](../CHANGELOG.md)).
- [x] Cross-language links: client → API contract → handler, Compose → Dockerfile, Service → Deployment, CI job → script.
- [x] Anything else that changed is still mapped, and the agent describes it from its changed lines.
- [x] Risk lens: about 50 rules plus structural API and schema checks, confirmed by the agent; tuned on four real projects.
- [x] Real audio: normalized 48 kHz stereo AAC, a WebM copy for VS Code, narration embedded in the HTML, Piper voice.
- [x] Deterministic maps (stable across runs) and cached video encodes.

## v0.3: trust and reach

- [x] Offline authored accuracy corpus with explicit ground truth, precision/recall and a regression baseline.
  Separate installed-CPython and local-project declaration checks use a Python AST reference. Broader independently
  annotated real repositories remain future work.
- [x] Complete collected text diff download and local editor handoff, with credential redaction and portable metadata.

- [ ] **Run on macOS and Linux for real.** Exercise the `say` and `espeak-ng` voices, Piper, browser discovery and the tests.
  Add a GitHub Actions matrix for Windows, macOS and Linux on Python 3.9 and 3.13.
- [ ] **Optional tree-sitter backend.** Use `tree-sitter-language-pack` when it is installed, for exact parsing of the
  languages the pattern engine approximates. Keep the engine as the zero-dependency fallback.
- [ ] **A language corpus in CI.** Pin a few real repositories per language (as with spring-petclinic, cobra, eShop and
  terraform-aws-vpc in v0.2) and track symbols, links, parse errors and risk-flag counts over time.
- [ ] **Dependency-injection links.** Resolve constructor-injected interfaces to their single implementation (Spring,
  ASP.NET, NestJS), which would add most of the links missing in DI-heavy code.
- [ ] **Risk rules as data.** Move rules to a JSON file so teams can add their own, with per-rule suppression comments.
- [x] **TypeScript path aliases** from local `tsconfig.json`/`jsconfig.json` (`paths`, `baseUrl`, relative `extends`).
  Package/array inheritance, project references and compiler-specific resolution modes remain unsupported.
- [ ] **Python `src/` package roots** from `pyproject.toml`.
- [ ] **Explain the confidence.** Show in the explorer why an edge exists ("resolved via import of X").
- [ ] **Transcript "why".** Read the agent's own session log when available: Claude Code's `transcript_path`, Codex `rollout-*.jsonl`.
- [ ] **Comprehension check.** A 3-question quiz per change, generated from the map, to measure whether people actually
  understand more. This is the real success metric.

## v0.4: a better video

- [ ] **Kokoro-82M voice** (`--tts kokoro`, Apache-2.0, a 326 MB model downloaded on first use).
- [ ] **Animated transitions** for changed scenes only: box highlights that move, and code that types in. Keep the
  still-and-cache path for everything else.
- [ ] **Segment cache.** Encode each beat once and join with stream copy, so a one-line edit re-encodes one segment
  instead of the whole video (today: about 15 s at 1080p).
- [ ] **Chapters and subtitles** (`.vtt`) inside the MP4.
- [ ] **Paid voices (opt-in):** OpenAI and ElevenLabs when an API key is set.

## v0.5: workflows

- [x] **Opt-in Claude task hooks.** `UserPromptSubmit`/`Stop` capture private before/end sources, continue the existing
  agent for source-backed review and explanations, and supervise background analysis/render/video stages. Includes
  per-task history, project opt-out, retry/cancel and preservation of unrelated Claude settings. Manual mode is unchanged.
  Captures are intervals and do not verify authorship. See [automatic mode](automatic.md).
- [x] **Native Windows acceptance fixture.** On 24 October 2026, Claude Code 2.1.71 skipped a no-change task and
  completed two sequential one-file tasks through source review, labels, maps and non-silent narrated MP4s. Their
  diffs preserved separate 1→2 and 2→3 task windows. Ordinary Claude permissions apply; the fixture used invocation-specific
  approvals and no global installation. See the evidence (local evidence; see [beta validation scope](beta-validation.md)).
- [ ] **Broader automatic workflow validation.** Exercise additional Claude versions, operating systems and approval
  configurations; measure walkthrough noise, complex-project accuracy and large-workspace latency in real development.
- [ ] **Daily change summary.** A "what changed today" command across captured task windows.
- [ ] **PR mode.** `codemap analyze --base origin/main`, then a summary comment with the risk flags and a link to the
  HTML (an artifact in CI), and a GitHub Action.
- [ ] **CI labelling** without an agent: an optional direct Claude API call with batch pricing and a small model.
- [ ] **Interoperability:** export or import Understand-Anything and CodeBoarding JSON; export risk flags as SARIF.

## Ideas parked for later

- Data-flow edges (what is passed between steps), using types and signatures.
- Heat maps of churn (`git log`) layered onto the map.
- A VS Code panel that opens `codemap.html` next to the code.
- Cost hints for cloud changes (a new NAT gateway, a bigger instance type).

## Known limitations in v0.2

- Parsing outside Python is pattern-based (see the README). Macros, unusual generics, object-literal methods and
  IIFE-wrapped JavaScript modules can be missed.
- Calls through dependency injection, event emitters, reflection or computed names are not resolved.
- Risk flags are line-based: a rule sees the changed line and a few lines above it, not the whole program.
- In repo mode for huge flat codebases, the top level is groups of loosely related files. Scope with a path.
- Narration templates and risk messages are English only.
