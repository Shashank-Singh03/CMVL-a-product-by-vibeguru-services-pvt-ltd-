"""Bounded lessons from the saved map, without loading or executing source files.

The course explains recorded evidence and basic vocabulary. Marking a lesson read
is a learner's own progress, not a test result or proof of understanding.
"""
from __future__ import annotations

import hashlib
import json
import re

from .formats import CI_NAMES, CODE_EXT, DEPS_FILES, OTHER_EXT

MAX_NODES = 3000
MAX_CHANGES = 12
MAX_LINES = 10
MAX_LINE_CHARS = 160
MAX_EXCERPT_CHARS = 10_000
MAX_CONNECTIONS = 12
MAX_CONCEPTS = 24
MAX_PACK_BYTES = 32_000
CHANGED = {"added", "modified", "deleted"}
KINDS = {"class", "function", "method", "module"}
DOMAINS = {"code", "api", "data", "infra", "ci", "deps", "ai", "config", "docs", "other"}
LANGUAGES = set(CODE_EXT.values()) | {value[0] for value in OTHER_EXT.values()} | set(CI_NAMES.values()) | {
    "dockerfile", "compose", "kubernetes", "helm-values", "cloudformation", "openapi", "env", "text",
    "github-actions", "azure-pipelines", "circleci", "deps-nuget", "deps-pip-setupcfg",
} | {"deps-" + value for value in DEPS_FILES.values()}

# Definitions are general vocabulary, not claims that a particular path executes.
GLOSSARY = {
    "diff": ("Diff", "A comparison of two versions. Added lines belong to the later version; removed lines belong to the earlier one. Context lines help locate the change."),
    "return": ("Return statement", "A statement that finishes the current function call and can send a value back to its caller. Changing the returned value can change what callers receive."),
    "literal": ("Literal value", "A value written directly in source, such as the number 2 or a quoted string. A changed literal can affect behavior even when no functions or connections are added."),
    "function": ("Function", "A reusable block of code that can accept inputs and may return a result. A map can show calls between functions without running them."),
    "class": ("Class", "A declaration that groups related data and behavior. Objects are instances of a class; a class definition alone does not show which objects exist at runtime."),
    "method": ("Method", "A function associated with a class or object. The receiver and runtime type can affect which implementation is called."),
    "module": ("Module", "A source unit that groups declarations and imports. Module-level changes can affect setup, constants and dependencies as well as functions."),
    "import": ("Import", "A declaration that makes another module, package or exported name available. Resolving its target helps connect changes across files."),
    "call": ("Call relationship", "A recorded relationship from a caller to a possible callee. Static resolution can miss dynamic behavior or choose an uncertain target."),
    "interface": ("Interface", "A contract describing operations or shapes that implementations must provide. Calls through interfaces may need runtime information to identify the implementation."),
    "type": ("Type", "A description of the values an expression or declaration can use. A type checker can detect some incompatible uses; it does not prove application behavior is correct."),
    "compilation": ("Compilation", "Translation from source into another form, such as machine code or bytecode. This walkthrough does not compile or run the changed application."),
    "bytecode": ("JVM bytecode", "The instruction form used in Java class files and executed by a Java Virtual Machine. Source structure is only one part of the eventual runtime behavior."),
    "pointer": ("Pointer", "A value representing an address or a null value. Access through a pointer needs appropriate lifetime and validity; a call map is not a memory-safety analysis."),
    "ownership": ("Ownership and lifetime", "The responsibility for keeping a resource alive and releasing it when no longer needed. In C++, object lifetime and ownership matter when reviewing changes."),
    "promise": ("Promise and async", "A JavaScript Promise represents an eventual result. An async function returns a Promise, and await pauses that function until the awaited result settles."),
    "callback": ("Callback", "A function passed to other code to be invoked later or under particular conditions. The target may depend on runtime values, so static links can be incomplete."),
    "container": ("Container", "An environment for running processes with configured filesystem, network and resource boundaries. A container definition does not prove that the service starts successfully."),
    "image": ("Container image", "Packaged filesystem content and metadata used to create a container. Changing a base image or build instruction can change the resulting runtime environment."),
    "deployment": ("Kubernetes Deployment", "A Kubernetes object describing a desired set of Pods and how updates should roll out. Applying the manifest and checking the rollout are separate from reading it."),
    "pod": ("Pod", "Kubernetes' unit for running one or more cooperating containers with shared settings. Resource limits, permissions and health checks influence its behavior."),
    "resource": ("Infrastructure resource", "An item that infrastructure tooling can create or manage, such as a network, bucket or workload. A declaration describes intent rather than proving deployed state."),
    "infrastructure-as-code": ("Infrastructure as code", "Infrastructure expressed as versioned configuration so tools can plan or apply changes. Review the actual plan and environment before treating a definition as deployed."),
    "pipeline": ("CI/CD pipeline", "An automated sequence for integrating changes, checking them and preparing or performing releases. The configured pipeline and its actual run results are different evidence."),
    "workflow": ("Workflow", "A configured automation triggered by events or manual action. Its jobs and permissions determine what it is allowed to do."),
    "job": ("Job", "A group of automation steps executed in a selected environment. Dependencies between jobs affect order and whether later jobs may run."),
    "step": ("Pipeline step", "One command or reusable action within a job. Changes to its inputs, permissions or shell can change what the job does."),
    "api": ("API contract", "An agreement about how components communicate, including operations, inputs and outputs. A contract file may differ from the deployed service."),
    "endpoint": ("Endpoint", "An exposed operation identified by an address and protocol-specific action, such as an HTTP method plus path. Follow both the contract and its implementation."),
    "schema": ("Schema", "A description of data structure and constraints. Schema changes can affect stored data and clients that expect the earlier structure."),
    "migration": ("Database migration", "A versioned change to database structure or data. Review compatibility and the actual migration procedure; a diagram does not test it."),
    "dependency": ("Dependency", "A package or component another part of the system relies on. Version or configuration changes can affect behavior outside the edited file."),
    "configuration": ("Configuration", "Values or declarations that select how an application or tool behaves. Their effects depend on the environment and how they are consumed."),
    "static-analysis": ("Static analysis", "Inspection of source or configuration without executing the application. It can identify structure and potential issues while missing behavior determined at runtime."),
    "source-review": ("Source-backed review", "A model review tied to recorded source quotes, spans and hashes. These checks establish matching evidence, not that every interpretation or relationship is semantically correct."),
    "model-inference": ("Model inference", "An interpretation proposed by the coding model. Source evidence can support it, but it remains an inference rather than an observed execution trace."),
    "risk-flag": ("Risk flag", "A rule-based indication that a change may need inspection. Flags can be false positives; no flags does not establish that a change is safe."),
}

LANGUAGE_CONCEPTS = {
    "javascript": ("module", "promise", "callback", "import"),
    "typescript": ("type", "interface", "module", "promise"),
    "java": ("class", "method", "bytecode", "compilation"),
    "cpp": ("pointer", "ownership", "compilation", "class"),
    "c": ("pointer", "compilation", "function"),
    "python": ("function", "module", "import"),
    "vue": ("module", "promise", "callback"), "svelte": ("module", "promise", "callback"),
    "astro": ("module", "import"), "csharp": ("class", "interface", "method"),
    "go": ("function", "interface", "compilation"), "rust": ("ownership", "compilation", "function"),
    "kotlin": ("class", "method", "bytecode"), "scala": ("class", "function", "bytecode"),
    "dockerfile": ("container", "image"), "compose": ("container", "image", "configuration"),
    "kubernetes": ("deployment", "pod", "resource"), "helm-values": ("configuration", "deployment"),
    "terraform": ("infrastructure-as-code", "resource"), "bicep": ("infrastructure-as-code", "resource"),
    "cloudformation": ("infrastructure-as-code", "resource"),
    "github-actions": ("pipeline", "workflow", "job", "step"),
    "gitlab-ci": ("pipeline", "job", "step"), "azure-pipelines": ("pipeline", "job", "step"),
    "circleci": ("pipeline", "workflow", "job"), "jenkins": ("pipeline", "job", "step"),
    "bitbucket": ("pipeline", "step"), "cloudbuild": ("pipeline", "step"), "buildspec": ("pipeline", "step"),
    "make": ("pipeline", "dependency"), "openapi": ("api", "endpoint", "schema"),
    "graphql": ("api", "schema"), "proto": ("api", "schema"), "sql": ("schema", "migration"),
    "prisma": ("schema", "migration"),
}
LANGUAGE_ORDER = ("javascript", "typescript", "java", "cpp", "python", "c", "dockerfile", "compose",
                  "kubernetes", "terraform", "github-actions", "sql", "openapi")
DOMAIN_CONCEPTS = {"code": ("function", "module", "call"), "infra": ("resource", "infrastructure-as-code"),
                   "ci": ("pipeline", "job", "step"), "api": ("api", "endpoint"),
                   "data": ("schema", "migration"), "deps": ("dependency",),
                   "config": ("configuration",), "ai": ("configuration",)}


def _number(value):
    return value if type(value) is int and 0 <= value <= 1_000_000_000 else None


def _member(value, choices):
    return isinstance(value, str) and value in choices


def _path(value):
    if (not isinstance(value, str) or not value or len(value) > 240 or "\\" in value or ":" in value
            or value.startswith("/") or any(ord(c) < 32 for c in value)
            or any(part in ("", ".", "..", ".codemap", ".git") for part in value.split("/"))):
        return None
    return value


def _identifier(value):
    return "change-" + hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:12]


def _revision(pack):
    return hashlib.sha256(json.dumps(pack, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def build_learning(ir, *, clean_text):
    """Build five finite lessons from the captured IR using a caller's redactor."""
    def clean(value, limit=600):
        if not isinstance(value, str):
            return ""
        result = clean_text(value, limit=limit)
        return result[:limit] if isinstance(result, str) else ""

    empty = {"version": 1, "available": False, "reason": "A saved source map is needed before a lesson can be prepared.",
             "truncated": False, "steps": [], "concepts": []}
    if not isinstance(ir, dict) or not isinstance(ir.get("nodes"), dict):
        return {**empty, "revision": _revision(empty)}
    raw_nodes = ir["nodes"]
    nodes, truncated = {}, len(raw_nodes) > MAX_NODES
    for key in sorted(key for key in raw_nodes if isinstance(key, str))[:MAX_NODES]:
        node = raw_nodes[key]
        if not isinstance(node, dict) or not _member(node.get("kind"), KINDS) or not _path(node.get("file")):
            continue
        nodes[key] = node
    if not nodes:
        return {**empty, "revision": _revision(empty)}

    stats = ir.get("stats") if isinstance(ir.get("stats"), dict) else {}
    scope = ir.get("scope") if isinstance(ir.get("scope"), dict) else {}
    review = ir.get("review") if isinstance(ir.get("review"), dict) else {}
    reviewed = review.get("status") == "reviewed"
    review_files = review.get("reviewed_files")
    reviewed_count = len({_path(item) for item in review_files if _path(item)}) if isinstance(review_files, list) else None
    languages = {node.get("lang") for node in nodes.values() if _member(node.get("lang"), LANGUAGES)}
    language_stats = stats.get("languages")
    if isinstance(language_stats, dict):
        languages.update(key for key, value in language_stats.items() if key in LANGUAGES and _number(value))
    domains = {node.get("domain") for node in nodes.values() if _member(node.get("domain"), DOMAINS)}

    def status(node):
        return node.get("own_status", node.get("status")) if node["kind"] in ("class", "module") else node.get("status")

    changed = [key for key, node in nodes.items() if _member(status(node), CHANGED)]
    # Prefer a useful callable/resource per file before adding more details from
    # one file. A module roll-up must not displace its changed child functions.
    candidates = changed or list(nodes)
    ordered = sorted(candidates, key=lambda key: (nodes[key]["file"], nodes[key]["kind"] == "module", key))
    by_file, leftovers = {}, []
    for key in ordered:
        path = nodes[key]["file"]
        if path not in by_file:
            by_file[path] = key
        else:
            leftovers.append(key)
    selected = (list(by_file.values()) + leftovers)[:MAX_CHANGES]
    truncated |= len(candidates) > len(selected)
    excerpt_budget = MAX_EXCERPT_CHARS
    changes = []
    for key in selected:
        node = nodes[key]
        title = clean(node.get("title") or node.get("name"), 160) or "Mapped item"
        item_status = status(node) if _member(status(node), CHANGED) else "context"
        summary = clean(node.get("sum"), 480)
        if not summary:
            summary = f"This {node['kind']} is {'shown for context' if item_status == 'context' else item_status} in the saved map. Inspect the excerpt and its callers before inferring the wider effect."
        lines = []
        raw_lines = node.get("diff") if isinstance(node.get("diff"), list) else []
        raw_lines = [line[:4000] for line in raw_lines[:40] if isinstance(line, str)]
        # Redact multiline material together before clipping individual lines.
        safe_lines = clean("\n".join(raw_lines), 8000).splitlines()
        for raw in safe_lines[:MAX_LINES]:
            if excerpt_budget <= 0:
                break
            kind = "added" if raw.startswith("+") else "removed" if raw.startswith("-") else "context"
            value = raw[1:] if raw[:1] in ("+", "-", " ") else raw
            value = value[:min(MAX_LINE_CHARS, excerpt_budget)]
            excerpt_budget -= len(value)
            # Diff excerpts do not establish original line numbers after cap,
            # context trimming and removed-line interleaving.
            lines.append({"kind": kind, "text": value, "line": None})
        truncated |= len(safe_lines) > len(lines) or any(len(raw) > MAX_LINE_CHARS + 1 for raw in safe_lines)
        changes.append({"id": _identifier(key), "title": title, "summary": summary,
                        "file": clean(node["file"], 240), "language": node.get("lang") if _member(node.get("lang"), LANGUAGES) else "text",
                        "domain": node.get("domain") if _member(node.get("domain"), DOMAINS) else "other",
                        "status": item_status, "lines": lines})

    concept_ids = []
    def add(*ids):
        for ident in ids:
            if ident in GLOSSARY and ident not in concept_ids:
                concept_ids.append(ident)
    add("static-analysis", "source-review", *( ("diff",) if changed else () ))
    code_excerpt = "\n".join(line["text"] for change in changes if change["domain"] == "code" for line in change["lines"])
    if re.search(r"\breturn\b", code_excerpt):
        add("return")
    if re.search(r"\b\d+(?:\.\d+)?\b|[\"'][^\n\"']*[\"']", code_excerpt):
        add("literal")
    ordered_languages = sorted(languages, key=lambda lang: (LANGUAGE_ORDER.index(lang) if lang in LANGUAGE_ORDER else 99, lang))
    for lang in ordered_languages:
        relevant = LANGUAGE_CONCEPTS.get(lang, ("dependency",) if lang.startswith("deps-") else ())
        if relevant:
            add(relevant[0])
    for domain in sorted(domains):
        add(*DOMAIN_CONCEPTS.get(domain, ())[:1])
    for kind in sorted({nodes[key]["kind"] for key in selected}):
        add(kind)
    for lang in ordered_languages:
        add(*LANGUAGE_CONCEPTS.get(lang, ()))
    for domain in sorted(domains):
        add(*DOMAIN_CONCEPTS.get(domain, ()))
    risk_stats = stats.get("risks") if isinstance(stats.get("risks"), dict) else {}
    risks = {severity: _number(risk_stats.get(severity)) for severity in ("high", "medium", "low")}
    if any(risks.values()):
        add("risk-flag")
    if reviewed:
        add("model-inference")
    truncated |= len(concept_ids) > MAX_CONCEPTS
    concept_ids = concept_ids[:MAX_CONCEPTS]
    concepts = [{"id": ident, "term": GLOSSARY[ident][0], "definition": GLOSSARY[ident][1]} for ident in concept_ids]

    connections, seen = [], set()
    views = ir.get("views") if isinstance(ir.get("views"), list) else []
    selected_files = {nodes[key]["file"] for key in selected}
    for view in views[:80]:
        if not isinstance(view, dict):
            continue
        raw_reviewed = view.get("review_edges") if isinstance(view.get("review_edges"), list) else []
        model_pairs = {(edge[0], edge[1]) for edge in raw_reviewed[:1000]
                       if isinstance(edge, list) and len(edge) >= 2 and isinstance(edge[0], str) and isinstance(edge[1], str)}
        edges = view.get("edges") if isinstance(view.get("edges"), list) else []
        for edge in edges[:1000]:
            if (not isinstance(edge, list) or len(edge) < 5 or not isinstance(edge[0], str)
                    or not isinstance(edge[1], str) or edge[0] not in nodes or edge[1] not in nodes
                    or edge[4] not in ("calls", "refs", "inherits")):
                continue
            a, b = edge[:2]
            if nodes[a]["file"] not in selected_files and nodes[b]["file"] not in selected_files:
                continue
            pair = (a, b, edge[4])
            if pair in seen:
                continue
            seen.add(pair)
            if len(connections) >= MAX_CONNECTIONS:
                truncated = True
                continue
            connections.append({"from": clean(nodes[a].get("title") or nodes[a].get("name"), 160) or "Mapped item",
                                "to": clean(nodes[b].get("title") or nodes[b].get("name"), 160) or "Mapped item",
                                "fromId": _identifier(a), "toId": _identifier(b), "kind": edge[4],
                                "confidence": "model" if (a, b) in model_pairs else "static"})

    goals = ir.get("why") if isinstance(ir.get("why"), list) else []
    goal = next((clean(item.get("text"), 600) for item in goals[:3]
                 if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip()), "")
    goal = goal or "No authored change goal is recorded. Use the selected source excerpts and map to establish the purpose before assuming behavior."
    review_note = (f"A source-backed model review is recorded{f' for {reviewed_count} file(s)' if reviewed_count is not None else ''}. Matching source evidence does not establish semantic correctness."
                   if reviewed else "Source-backed model review has not completed. Treat these parser-derived lessons and relationships as provisional.")
    files = _number(scope.get("files"))
    changed_note = f" The captured diff records {files} changed file(s)." if changed and files is not None else ""
    overview = goal + changed_note + " " + review_note
    shown = len(changes)
    change_body = (f"Inspect {shown} selected changed item(s). Added and removed lines are highlighted separately; context lines explain the local surroundings. These are capped excerpts, not the complete diff."
                   if changed else f"Explore {shown} mapped item(s). This saved analysis has no recorded added, modified or deleted item, so these items are context rather than manufactured changes.")
    connection_body = ("Follow recorded calls, references and inheritance to see how the selected files connect. Static links come from source analysis; links marked model are source-backed model inferences. Neither is an observed execution trace."
                       if connections else "No connection between these selected items is recorded in the available map views. This does not prove that the application has no dependencies or runtime interactions.")
    counts = ", ".join(f"{name}: {value}" for name, value in risks.items() if value is not None)
    review_body = review_note + (" Recorded risk flags — " + counts + ". Rule flags need inspection; they are not confirmed defects." if counts else " No risk-count summary is available in this map.")
    review_body += " Complete lessons only after your own inspection. Saved learning progress is self-reported and does not measure verified knowledge or test execution."
    common = {"conceptIds": [], "changes": [], "connections": [], "checks": []}
    steps = [
        {**common, "id": "overview", "title": "Understand the goal", "kind": "overview", "body": overview,
         "conceptIds": [value for value in ("diff", "static-analysis", "source-review") if value in concept_ids],
         "checks": [{"question": "What is the recorded goal of this change?", "answer": goal}]},
        {**common, "id": "concepts", "title": "Learn the vocabulary", "kind": "concepts",
         "body": "Use these definitions to read the languages and engineering areas present in this saved analysis. Definitions explain general concepts; they do not assert that every feature executes in this task.",
         "conceptIds": list(concept_ids), "checks": [{"question": "Does a static map prove runtime behavior?", "answer": "No. It inspects source and configuration without running the application, so dynamic behavior and environmental effects can remain unresolved."}]},
        {**common, "id": "changes", "title": "Read the highlighted changes" if changed else "Explore the mapped context", "kind": "changes", "body": change_body,
         "changes": changes, "checks": [{"question": "Which version do added and removed lines belong to?", "answer": "Added lines belong to the later captured version. Removed lines belong to the earlier version. Context lines help locate those edits."}]},
        {**common, "id": "connections", "title": "Follow the connections", "kind": "connections", "body": connection_body,
         "conceptIds": [value for value in ("call", "dependency", "model-inference") if value in concept_ids], "connections": connections,
         "checks": [{"question": "Do recorded arrows establish that a call actually executed?", "answer": "No. They are static relationships or source-backed model inferences. Confirm behavior with source inspection and appropriate independent runtime evidence."}]},
        {**common, "id": "review", "title": "Check your understanding", "kind": "review", "body": review_body,
         "conceptIds": [value for value in ("source-review", "risk-flag", "model-inference") if value in concept_ids],
         "checks": [{"question": "Has source-backed review completed for this saved map?", "answer": review_note}]},
    ]
    pack = {"version": 1, "available": True, "reason": "This lesson explains selected captured evidence. Learning completion is self-reported.",
            "reviewStatus": "reviewed" if reviewed else "pending", "truncated": bool(truncated), "steps": steps, "concepts": concepts}
    # Keep the full public pack bounded, including JSON escaping expansion.
    def size():
        return len(json.dumps(pack, ensure_ascii=True, separators=(",", ":")).encode())
    while size() > MAX_PACK_BYTES - 320 and any(change["lines"] for change in changes):
        max(changes, key=lambda change: len(change["lines"]))["lines"].pop()
        pack["truncated"] = True
    while size() > MAX_PACK_BYTES - 320 and len(changes) > 1:
        changes.pop()
        pack["truncated"] = True
    if pack["truncated"]:
        pack["reason"] = "This is a limited selection of captured evidence. Open the map and full collected diff for wider context. Learning completion is self-reported."
    actual = len(changes)
    if actual != shown:
        steps[2]["body"] = change_body.replace(f"{shown} selected changed item(s)", f"{actual} selected changed item(s)").replace(f"{shown} mapped item(s)", f"{actual} mapped item(s)")
    pack["revision"] = _revision(pack)
    return pack
