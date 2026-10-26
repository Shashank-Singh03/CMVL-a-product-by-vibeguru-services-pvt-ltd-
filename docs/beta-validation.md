# Beta validation scope

The beta is based on the local Windows development build and authored disposable
fixtures. Public CI reruns the portable checks on its declared matrix; a platform
listed as a target is not a claim that every native speech/browser combination has
already been certified.

The 26 October dashboard refinement passed 73 targeted Python checks (72 passed,
one environment skip), followed by a 37-test viewer/navigation/server subset with
all checks passing. Six browser suites checked dashboard basics, navigation,
learning, comparison, live controls, and the refined workspace. The brand suite
covered local files, static HTTP, and the scoped local server; both themes; widths
from 360px through 3221px; real recorded video playback; selected map restoration;
outside/focus/Escape navigation dismissal; and automatic/manual/paused rotation.
Those checks had no page errors, console errors or external runtime requests.

Earlier full regression runs and native Claude checks are historical evidence for
their respective builds. A 24 October native Windows fixture used Claude Code
2.1.71 with two sequential one-file Python changes. Each produced a reviewed map
and a non-silent narrated MP4 with separate task windows. That does not establish
complex-project parser accuracy, broad native-agent compatibility, or generation
latency for large projects. The authored eleven-format learning fixture checked
the UI's pending-review state, not model correctness.

Generated benchmark reports, screenshots and private task captures remain local
and are excluded from the public repository. The reproducible runners and authored
cases are included; see [benchmark methodology](../benchmarks/README.md),
[browser testing](browser-testing.md), and the release's workflow results.

Known limits remain: dynamic code and ambiguous relationships can be unresolved;
rule findings need review; recognized-pattern redaction is not a guarantee that all
secrets are detected; captures describe time intervals rather than proving edit
authorship; and manual learning checklists do not measure knowledge or correctness.
