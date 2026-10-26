"""Parsers for everything that is not general-purpose code.

Infrastructure: Dockerfile, Compose, Kubernetes (+ Helm templates), Terraform/HCL, Bicep, CloudFormation
CI/CD:          GitHub Actions, GitLab CI, Azure Pipelines, CircleCI, Jenkinsfile, Makefile, other pipelines
API contracts:  OpenAPI / Swagger, GraphQL SDL, Protocol Buffers
Data:           SQL (tables, columns, migrations), Prisma
Dependencies:   npm, pip, pyproject, Pipfile, go.mod, Cargo, Maven, Gradle, Gemfile, Composer, NuGet
Other:          Jupyter notebooks, .env (values never stored), YAML/JSON/TOML/INI settings, Markdown sections

Each produces the same FileInfo/Sym shape as the code parsers. Items carry `keys` (how others can refer to
them) and `refs` (what they refer to), so the graph links a Service to its Deployment, a job to the job it
`needs`, an API operation to its schema and to the code that implements the route.
"""
from __future__ import annotations

import ast
import bisect
import posixpath
import re

from .model import FileInfo, Sym, dedupe_ids, sym_id
from .parse_generic import SPECS, Spec, blank, script_refs
from .util import first_sentence, sha1
from .yamlite import Node, parse_any, parse_json, parse_yaml

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")


def norm_path(path: str) -> str:
    """'/notes/{id}/', '/notes/:id', '/notes/<int:id>' -> '/notes/{}'."""
    p = "/" + path.strip().strip("/")
    p = re.sub(r"\{[^}]*\}|:[A-Za-z_]\w*|<[^>]*>|\[[^\]]*\]|\$\{[^}]*\}", "{}", p)
    return p if p != "/" else "/"


class Builder:
    """Collects symbols for one file with line spans, keys and references."""

    def __init__(self, path: str, lang: str, text: str):
        self.fi = FileInfo(path=path, lang=lang)
        self.lines = text.splitlines()
        self.fi.loc = sum(1 for ln in self.lines if ln.strip())
        self.dir = posixpath.dirname(path)

    def add(self, kind: str, name: str, line: int, end: int, sub: str, parent: Sym | None = None,
            doc: str = "", sig: str = "", keys=(), refs=()) -> Sym:
        qual = f"{parent.qual}.{name}" if parent else name
        end = max(line, min(end, len(self.lines) or end))
        segment = "\n".join(ln.rstrip() for ln in self.lines[line - 1:end])
        sym = Sym(id=sym_id(kind, self.fi.path, qual), kind=kind, name=name, qual=qual, file=self.fi.path,
                  line=line, end=end, sub=sub, doc=first_sentence(doc) if doc else "",
                  signature=(sig or (self.lines[line - 1].strip() if 0 < line <= len(self.lines) else name))[:160],
                  parent=parent.id if parent else None, keys=list(keys), refs=list(refs),
                  hash=sha1(segment), exported=True)
        self.fi.symbols.append(sym)
        return sym

    def done(self) -> FileInfo:
        self.fi.symbols.sort(key=lambda s: (s.line, s.kind != "class"))
        ordered = []
        for s in self.fi.symbols:
            if s.kind == "class":
                ordered.append(s)
                ordered.extend(m for m in self.fi.symbols if m.parent == s.id)
        ordered.extend(s for s in self.fi.symbols if s.kind == "function")
        self.fi.symbols = ordered
        return dedupe_ids(self.fi)

    def file_ref(self, rel: str, conf: float = 0.8) -> tuple:
        """A reference to another repo file, relative to this file's folder (or the repo root)."""
        rel = rel.strip().strip("'\"")
        target = posixpath.normpath(posixpath.join(self.dir, rel)) if not rel.startswith("/") else rel.lstrip("/")
        return (f"file:{target}", conf)


def _subtree_refs(node: Node, pattern: str = "") -> list:
    return [n for n in node.walk() if n.kind == "scalar" and isinstance(n.value, str) and (not pattern or re.search(pattern, n.value))]


# ================================================================ infrastructure

def parse_dockerfile(path: str, text: str) -> FileInfo:
    b = Builder(path, "dockerfile", text)
    stages = []
    joined = []
    for no, raw in enumerate(b.lines, 1):
        line = raw.strip()
        if joined and joined[-1][1].endswith("\\"):
            joined[-1] = (joined[-1][0], joined[-1][1][:-1] + " " + line, no)
        elif line and not line.startswith("#"):
            joined.append((no, line, no))
    for no, line, last in joined:
        m = re.match(r"(?i)^FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", line)
        if m:
            stages.append({"line": no, "end": last, "image": m.group(1), "name": m.group(2) or "", "ins": []})
        elif stages:
            stages[-1]["ins"].append(line)
            stages[-1]["end"] = last
    names = [s["name"] for s in stages if s["name"]]
    for i, st in enumerate(stages):
        name = st["name"] or (f"stage {i + 1}" if len(stages) > 1 else "container image")
        end = stages[i + 1]["line"] - 1 if i + 1 < len(stages) else len(b.lines)
        refs = []
        if st["image"] in names:
            refs.append((f"docker:{path}:{st['image']}", 0.95))
        facts = [f"from {st['image']}"]
        for ins in st["ins"]:
            cm = re.search(r"(?i)--from=(\S+)", ins)
            if cm and cm.group(1) in names:
                refs.append((f"docker:{path}:{cm.group(1)}", 0.95))
            if re.match(r"(?i)^EXPOSE\s", ins):
                facts.append("exposes " + ins.split(None, 1)[1])
            if re.match(r"(?i)^USER\s", ins):
                facts.append("runs as " + ins.split(None, 1)[1])
            if re.match(r"(?i)^(CMD|ENTRYPOINT)\s", ins):
                facts.append(ins.split(None, 1)[0].lower() + " " + ins.split(None, 1)[1][:60])
            for script in script_refs(ins):
                refs.append(b.file_ref(script if "/" in script else "./" + script, 0.7))
        b.add("function", name, st["line"], end, "stage", doc="; ".join(facts) + ".",
              sig=f"FROM {st['image']}" + (f" AS {st['name']}" if st["name"] else ""),
              keys=[f"docker:{path}:{name}", f"dockerfile:{b.dir or '.'}"], refs=refs)
    return b.done()


def parse_compose(path: str, text: str) -> FileInfo:
    b = Builder(path, "compose", text)
    docs = parse_yaml(text)
    if not docs or docs[0].kind != "map":
        return b.done()
    services = docs[0].get("services")
    for name, node in (services.items() if services is not None else []):
        refs = []
        dep = node.get("depends_on")
        if dep is not None:
            targets = [c.text() for c in dep.children] if dep.kind == "seq" else [k for k, _ in dep.items()]
            refs += [(f"compose:{path}:{t}", 0.95) for t in targets if t]
        for link in (node.get("links").children if node.get("links") is not None else []):
            refs.append((f"compose:{path}:{link.text().split(':')[0]}", 0.9))
        build = node.get("build")
        facts = []
        if build is not None:
            ctx = build.text() if build.kind == "scalar" else (build.at("context").text(".") if build.get("context") else ".")
            dockerfile = build.at("dockerfile").text("Dockerfile") if build.kind == "map" and build.get("dockerfile") else "Dockerfile"
            target_dir = posixpath.normpath(posixpath.join(b.dir, ctx))
            refs.append((f"file:{posixpath.normpath(posixpath.join(target_dir, dockerfile)).lstrip('./')}", 0.9))
            refs.append((f"dockerfile:{target_dir}", 0.8))
            facts.append(f"built from {ctx}")
        if node.get("image") is not None:
            facts.append(f"image {node.at('image').text()}")
        if node.get("ports") is not None:
            facts.append("ports " + ", ".join(str(c.value) for c in node.at("ports").children[:3]))
        b.add("function", name, node.line, node.end, "service", doc="; ".join(facts) + "." if facts else "",
              keys=[f"compose:{path}:{name}"], refs=refs)
    return b.done()


WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod", "ReplicaSet", "ReplicationController"}


def _pod_spec(doc: Node):
    kind = doc.at("kind").text()
    if kind == "Pod":
        return doc.at("spec")
    if kind == "CronJob":
        return doc.at("spec", "jobTemplate", "spec", "template", "spec")
    return doc.at("spec", "template", "spec")


def _helm_clean(text: str) -> str:
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.fullmatch(r"\{\{-?.*-?\}\}", stripped) and not re.search(r":\s", stripped):
            out.append("")                                  # pure template control line
        else:
            out.append(re.sub(r"\{\{-?.*?-?\}\}", "X", line))
    return "\n".join(out)


def parse_kubernetes(path: str, text: str) -> FileInfo:
    b = Builder(path, "kubernetes", text)
    for doc in parse_yaml(_helm_clean(text) if "{{" in text else text):
        if doc.kind != "map" or doc.get("kind") is None:
            continue
        kind = doc.at("kind").text()
        name = doc.at("metadata", "name").text("unnamed") if doc.get("metadata") is not None else "unnamed"
        ns = doc.at("metadata", "namespace").text("default") if doc.at("metadata", "namespace") is not None else "default"
        keys = [f"k8s:{ns}:{kind}/{name}"]
        refs = []
        facts = []
        pod = _pod_spec(doc) if kind in WORKLOADS else None
        if pod is not None:
            labels = doc.at("spec", "template", "metadata", "labels") or doc.at("metadata", "labels")
            for k, v in (labels.items() if labels is not None else []):
                keys.append(f"k8s:{ns}:label:{k}={v.text()}")
            for container in (pod.at("containers").children if pod.get("containers") is not None else []):
                if container.get("image") is not None:
                    facts.append(f"runs {container.at('image').text()}")
                for env_from in (container.at("envFrom").children if container.get("envFrom") is not None else []):
                    for ref_kind, ref_type in (("configMapRef", "ConfigMap"), ("secretRef", "Secret")):
                        if env_from.at(ref_kind, "name") is not None:
                            refs.append((f"k8s:{ns}:{ref_type}/{env_from.at(ref_kind, 'name').text()}", 0.95))
                for node in (container.at("env").walk() if container.get("env") is not None else []):
                    for ref_kind, ref_type in (("configMapKeyRef", "ConfigMap"), ("secretKeyRef", "Secret")):
                        if node.key == ref_kind and node.at("name") is not None:
                            refs.append((f"k8s:{ns}:{ref_type}/{node.at('name').text()}", 0.95))
            for vol in (pod.at("volumes").children if pod.get("volumes") is not None else []):
                if vol.at("configMap", "name") is not None:
                    refs.append((f"k8s:{ns}:ConfigMap/{vol.at('configMap', 'name').text()}", 0.95))
                if vol.at("secret", "secretName") is not None:
                    refs.append((f"k8s:{ns}:Secret/{vol.at('secret', 'secretName').text()}", 0.95))
                if vol.at("persistentVolumeClaim", "claimName") is not None:
                    refs.append((f"k8s:{ns}:PersistentVolumeClaim/{vol.at('persistentVolumeClaim', 'claimName').text()}", 0.95))
            if pod.get("serviceAccountName") is not None:
                refs.append((f"k8s:{ns}:ServiceAccount/{pod.at('serviceAccountName').text()}", 0.95))
        if kind == "Service":
            selector = doc.at("spec", "selector")
            for k, v in (selector.items() if selector is not None else []):
                refs.append((f"k8s:{ns}:label:{k}={v.text()}", 0.85))
            ports = doc.at("spec", "ports")
            if ports is not None:
                facts.append("ports " + ", ".join(str(p.at("port").value) for p in ports.children if p.get("port") is not None))
        if kind == "Ingress":
            for node in doc.walk():
                if node.key == "service" and node.at("name") is not None:
                    refs.append((f"k8s:{ns}:Service/{node.at('name').text()}", 0.95))
                if node.key == "serviceName":
                    refs.append((f"k8s:{ns}:Service/{node.text()}", 0.95))
                if node.key == "host":
                    facts.append(f"host {node.text()}")
        if kind == "HorizontalPodAutoscaler" and doc.at("spec", "scaleTargetRef") is not None:
            target = doc.at("spec", "scaleTargetRef")
            refs.append((f"k8s:{ns}:{target.at('kind').text()}/{target.at('name').text()}", 0.95))
        if kind in ("RoleBinding", "ClusterRoleBinding"):
            role = doc.at("roleRef")
            if role is not None:
                refs.append((f"k8s:{ns}:{role.at('kind').text()}/{role.at('name').text()}", 0.95))
            for subject in (doc.at("subjects").children if doc.get("subjects") is not None else []):
                refs.append((f"k8s:{ns}:{subject.at('kind').text()}/{subject.at('name').text()}", 0.9))
        b.add("function", f"{kind}/{name}", doc.line, doc.end, kind.lower(), doc="; ".join(facts) + "." if facts else "",
              keys=keys, refs=refs)
    return b.done()


TF_BLOCK = re.compile(r'(?m)^(resource|data|module|variable|output|provider|locals|terraform|moved|import|check)\b\s*((?:"[^"]*"\s*)*)\{')


def parse_terraform(path: str, text: str) -> FileInfo:
    b = Builder(path, "terraform", text)
    if path.endswith(".tfvars"):
        for no, line in enumerate(b.lines, 1):
            m = re.match(r"^([A-Za-z_][\w-]*)\s*=", line)
            if m:
                b.add("function", m.group(1), no, no, "tfvar", keys=[f"tf:{b.dir}:var.{m.group(1)}"])
        return b.done()
    spec = Spec(name="hcl", family="data", line_comments=("#", "//"), heredoc=True, sq="none")
    code = blank(text, spec, keep_strings=True)
    newlines = [m.start() for m in re.finditer("\n", text)]

    def line_of(pos: int) -> int:
        return bisect.bisect_left(newlines, pos) + 1
    stack, bmatch = [], {}
    for m in re.finditer(r"[{}]", blank(text, spec, keep_strings=False)):
        if m.group() == "{":
            stack.append(m.start())
        elif stack:
            bmatch[stack.pop()] = m.start()
    for m in TF_BLOCK.finditer(code):
        kind = m.group(1)
        labels = re.findall(r'"([^"]*)"', m.group(2))
        open_at = m.end() - 1
        close = bmatch.get(open_at, len(text) - 1)
        if kind == "resource" and len(labels) >= 2:
            name, sub = f"{labels[0]}.{labels[1]}", "resource"
        elif kind == "data" and len(labels) >= 2:
            name, sub = f"data.{labels[0]}.{labels[1]}", "data source"
        elif kind == "module" and labels:
            name, sub = f"module.{labels[0]}", "module"
        elif kind == "variable" and labels:
            name, sub = f"var.{labels[0]}", "variable"
        elif kind == "output" and labels:
            name, sub = f"output.{labels[0]}", "output"
        elif kind == "provider" and labels:
            name, sub = f"provider.{labels[0]}", "provider"
        else:
            name, sub = kind, kind
        body = code[open_at:close + 1]
        refs = []
        for rm in re.finditer(r"\b(data\.[a-z]\w*\.[\w-]+|module\.[\w-]+|var\.[\w-]+|local\.[\w-]+|[a-z][a-z0-9]*_[a-z0-9_]+\.[A-Za-z_][\w-]*)", body):
            target = rm.group(1)
            if not target.startswith(("data.", "module.", "var.", "local.")):
                target = ".".join(target.split(".")[:2])
            refs.append((f"tf:{b.dir}:{target}", 0.95))
        src = re.search(r'(?m)^\s*source\s*=\s*"(\.{1,2}/[^"]+)"', body)
        if src:
            refs.append((f"tfdir:{posixpath.normpath(posixpath.join(b.dir, src.group(1)))}", 0.9))
        keys = [f"tf:{b.dir}:{name}"]
        if kind == "locals":
            keys += [f"tf:{b.dir}:local.{lm.group(1)}" for lm in re.finditer(r"(?m)^\s{1,4}([A-Za-z_][\w-]*)\s*=", body)]
        desc = re.search(r'(?m)^\s*description\s*=\s*"([^"]*)"', body)
        b.add("function", name, line_of(m.start()), line_of(close), sub, doc=desc.group(1) if desc else "",
              keys=keys, refs=[r for r in refs if r[0] != f"tf:{b.dir}:{name}"])
    if b.fi.symbols:
        b.fi.symbols[0].keys.append(f"tfdir:{b.dir}")
    return b.done()


def parse_bicep(path: str, text: str) -> FileInfo:
    b = Builder(path, "bicep", text)
    decl = re.compile(r"(?m)^(resource|module|param|var|output)\s+(\w+)\s*('[^']*')?")
    matches = list(decl.finditer(text))
    names = {m.group(2) for m in matches}
    for i, m in enumerate(matches):
        start = text.count("\n", 0, m.start()) + 1
        end_pos = matches[i + 1].start() - 1 if i + 1 < len(matches) else len(text)
        end = text.count("\n", 0, end_pos) + 1
        body = text[m.end():end_pos]
        refs = [(f"bicep:{b.dir}:{w}", 0.9) for w in sorted(set(re.findall(r"\b([A-Za-z_]\w*)\b", body))) if w in names and w != m.group(2)]
        sub = {"resource": "resource", "module": "module", "param": "parameter", "var": "variable", "output": "output"}[m.group(1)]
        b.add("function", m.group(2), start, end, sub, sig=text[m.start():m.end()].strip(),
              doc=(m.group(3) or "").strip("'"), keys=[f"bicep:{b.dir}:{m.group(2)}"], refs=refs)
    return b.done()


def parse_cloudformation(path: str, text: str) -> FileInfo:
    b = Builder(path, "cloudformation", text)
    docs = parse_any(text, path.endswith(".json"))
    if not docs or docs[0].kind != "map":
        return b.done()
    root = docs[0]
    for section, sub in (("Parameters", "parameter"), ("Resources", "resource"), ("Outputs", "output")):
        sec = root.get(section)
        for logical, node in (sec.items() if sec is not None else []):
            refs = []
            for n in node.walk():
                val = n.value if n.kind == "scalar" and isinstance(n.value, str) else ""
                for rm in re.finditer(r"!Ref\s+([A-Za-z0-9]+)|!GetAtt\s+([A-Za-z0-9]+)|\$\{([A-Za-z0-9]+)(?:\.[A-Za-z]+)?\}", val):
                    refs.append(next(g for g in rm.groups() if g))
                if n.key == "Ref" and n.kind == "scalar":
                    refs.append(n.text())
                if n.key in ("Fn::GetAtt",) and n.kind == "seq" and n.children:
                    refs.append(n.children[0].text())
                if n.key == "DependsOn":
                    refs += [c.text() for c in n.children] if n.kind == "seq" else [n.text()]
            typ = node.at("Type").text() if node.kind == "map" and node.get("Type") is not None else sub
            label = sub if sub != "resource" else typ.replace("AWS::", "").replace("::", " ").lower()
            b.add("function", logical, node.line, node.end, label,
                  doc=node.at("Description").text() if node.kind == "map" and node.get("Description") is not None else "",
                  sig=f"{logical}: {typ}", keys=[f"cfn:{path}:{logical}"],
                  refs=[(f"cfn:{path}:{r}", 0.95) for r in dict.fromkeys(refs) if r and r != logical and not r.startswith("AWS::")])
    return b.done()


# ================================================================ CI/CD

def _job_scripts(b: Builder, node: Node) -> list:
    refs = []
    for n in node.walk():
        if n.kind == "scalar" and isinstance(n.value, str) and n.key in ("run", "script", "before_script", "after_script", "command", "inline", None):
            for script in script_refs(n.value):
                refs.append((f"file:{posixpath.normpath(script).lstrip('./')}", 0.8))
        if n.key == "uses" and n.kind == "scalar" and str(n.value).startswith("./"):
            local = str(n.value)[2:].split("@")[0]
            refs.append((f"file:{local}" if local.endswith((".yml", ".yaml")) else f"file:{local}/action.yml", 0.85))
    return refs


def parse_github_actions(path: str, text: str) -> FileInfo:
    b = Builder(path, "github-actions", text)
    docs = parse_yaml(text)
    if not docs or docs[0].kind != "map":
        return b.done()
    root = docs[0]
    for key, sub in (("on", "trigger"), (True, "trigger"), ("permissions", "permissions"), ("env", "environment")):
        node = root.get(key)
        if node is not None:
            b.add("function", "on" if key is True else key, node.line, node.end, sub,
                  doc="Triggered by " + ", ".join(str(k) for k, _ in node.items()) + "." if sub == "trigger" and node.kind == "map" else "")
    jobs = root.get("jobs")
    for job_id, job in (jobs.items() if jobs is not None else []):
        refs = []
        needs = job.get("needs")
        if needs is not None:
            refs += [(f"gha:{path}:{c.text()}", 0.95) for c in (needs.children if needs.kind == "seq" else [needs])]
        refs += _job_scripts(b, job)
        steps = job.get("steps")
        step_names = [(s.at("name") or s.at("uses")).text() for s in (steps.children if steps is not None else [])
                      if s.kind == "map" and (s.get("name") is not None or s.get("uses") is not None)]
        title = job.at("name").text() if job.get("name") is not None else ""
        doc = (f"{title}: " if title else "") + ("steps: " + ", ".join(step_names[:5]) if step_names else "")
        b.add("function", str(job_id), job.line, job.end, "job", doc=doc + ("." if doc else ""),
              sig=f"job {job_id}" + (f" (runs on {job.at('runs-on').text()})" if job.get("runs-on") is not None else ""),
              keys=[f"gha:{path}:{job_id}"], refs=refs)
    runs = root.get("runs")
    if runs is not None:
        b.add("function", "action", runs.line, runs.end, "action", refs=_job_scripts(b, runs))
    return b.done()


GITLAB_RESERVED = {"stages", "variables", "include", "default", "workflow", "image", "services", "before_script",
                   "after_script", "cache", "pages"}


def parse_ci_yaml(path: str, text: str, lang: str) -> FileInfo:
    """GitLab CI, Azure Pipelines, CircleCI, Bitbucket, Cloud Build, CodeBuild."""
    b = Builder(path, lang, text)
    docs = parse_yaml(text)
    if not docs or docs[0].kind != "map":
        return b.done()
    root = docs[0]
    jobs: list = []
    if lang == "gitlab-ci":
        jobs = [(k, n) for k, n in root.items() if k not in GITLAB_RESERVED and n.kind == "map"]
    elif lang == "circleci":
        jobs = root.get("jobs").items() if root.get("jobs") is not None else []
    elif lang == "azure-pipelines":
        for n in root.walk():
            if n.kind == "map" and (n.get("job") is not None or n.get("deployment") is not None or n.get("stage") is not None):
                label = n.get("job") or n.get("deployment") or n.get("stage")
                jobs.append((label.text(), n))
    else:
        jobs = [(k, n) for k, n in root.items() if n.kind in ("map", "seq")]
    made = {}
    for name, node in jobs:
        refs = _job_scripts(b, node)
        for dep_key in ("needs", "dependencies", "extends", "dependsOn", "requires"):
            dep = node.get(dep_key) if node.kind == "map" else None
            if dep is not None:
                vals = [c.at("job").text() if c.kind == "map" and c.get("job") is not None else c.text() for c in dep.children] \
                    if dep.kind == "seq" else [dep.text()]
                refs += [(f"ci:{path}:{v}", 0.95) for v in vals if v]
        sub = "template" if str(name).startswith(".") else "job"
        stage = node.at("stage").text() if node.kind == "map" and node.get("stage") is not None else ""
        made[str(name)] = b.add("function", str(name), node.line, node.end, sub, doc=f"Stage {stage}." if stage else "",
                                keys=[f"ci:{path}:{name}"], refs=refs)
    if lang == "circleci" and root.get("workflows") is not None:      # workflows: jobs: - test: {requires: [build]}
        for n in root.at("workflows").walk():
            if n.kind == "map" and n.get("requires") is not None and n.key in made:
                made[n.key].refs += [(f"ci:{path}:{r.text()}", 0.95) for r in n.at("requires").children]
    return b.done()


def parse_jenkinsfile(path: str, text: str) -> FileInfo:
    b = Builder(path, "jenkins", text)
    spec = SPECS["groovy"]
    code = blank(text, spec, keep_strings=True)
    stack, bmatch = [], {}
    for m in re.finditer(r"[{}]", blank(text, spec, keep_strings=False)):
        if m.group() == "{":
            stack.append(m.start())
        elif stack:
            bmatch[stack.pop()] = m.start()
    previous = None
    for m in re.finditer(r"""\bstage\s*\(\s*['"]([^'"]+)['"]\s*\)\s*\{""", code):
        close = bmatch.get(m.end() - 1, len(text) - 1)
        body = text[m.end():close]
        refs = [(f"file:{posixpath.normpath(s).lstrip('./')}", 0.8) for s in script_refs(body)]
        if previous:
            refs.append((f"jenkins:{path}:{previous}", 0.5))
        b.add("function", m.group(1), text.count("\n", 0, m.start()) + 1, text.count("\n", 0, close) + 1, "stage",
              keys=[f"jenkins:{path}:{m.group(1)}"], refs=refs)
        previous = m.group(1)
    return b.done()


def parse_make(path: str, text: str) -> FileInfo:
    b = Builder(path, "make", text)
    targets = []
    for no, line in enumerate(b.lines, 1):
        m = re.match(r"^([A-Za-z0-9_./%-][\w./%-]*)\s*:(?!=)\s*([^=]*)$", line)
        if m and not m.group(1).startswith("."):
            targets.append((no, m.group(1), m.group(2).split()))
    for i, (no, name, deps) in enumerate(targets):
        end = targets[i + 1][0] - 1 if i + 1 < len(targets) else len(b.lines)
        while end > no and not b.lines[end - 1].strip():
            end -= 1
        recipe = "\n".join(b.lines[no:end])
        refs = [(f"make:{path}:{d}", 0.95) for d in deps]
        refs += [(f"file:{posixpath.normpath(posixpath.join(b.dir, s)).lstrip('./')}", 0.8) for s in script_refs(recipe)]
        b.add("function", name, no, end, "target", keys=[f"make:{path}:{name}"], refs=refs)
    return b.done()


# ================================================================ API contracts

def parse_openapi(path: str, text: str) -> FileInfo:
    b = Builder(path, "openapi", text)
    docs = parse_any(text, path.endswith(".json"))
    if not docs or docs[0].kind != "map":
        return b.done()
    root = docs[0]
    paths = root.get("paths")
    schemas = root.at("components", "schemas") or root.get("definitions")
    if paths is not None:
        ops = b.add("class", "Endpoints", paths.line, paths.end, "API endpoints", doc="HTTP operations in this contract.")
        for route, item in paths.items():
            for method, op in item.items():
                if str(method).lower() not in HTTP_METHODS:
                    continue
                verb = str(method).upper()
                norm = norm_path(str(route))
                refs = [(f"oas:{path}:schema:{n.value.rsplit('/', 1)[-1]}", 0.95)
                        for n in op.walk() if n.key == "$ref" and isinstance(n.value, str)]
                refs.append((f"route:{verb} {norm}", 0.9))
                summary = op.at("summary").text() if op.kind == "map" and op.get("summary") is not None else ""
                if not summary and op.kind == "map" and op.get("description") is not None:
                    summary = op.at("description").text()
                op_id = op.at("operationId").text() if op.kind == "map" and op.get("operationId") is not None else ""
                sym = b.add("method", f"{verb} {route}", op.line, op.end, "endpoint", parent=ops, doc=summary,
                            sig=f"{verb} {route}" + (f" ({op_id})" if op_id else ""),
                            keys=[f"api:{verb} {norm}"], refs=refs)
                sym.routes = [f"{verb} {norm}"]
    if schemas is not None:
        holder = b.add("class", "Schemas", schemas.line, schemas.end, "API schemas", doc="Data shapes the endpoints exchange.")
        for name, schema in schemas.items():
            props = schema.at("properties") if schema.kind == "map" else None
            fields = [k for k, _ in props.items()] if props is not None else []
            refs = [(f"oas:{path}:schema:{n.value.rsplit('/', 1)[-1]}", 0.95)
                    for n in schema.walk() if n.key == "$ref" and isinstance(n.value, str)]
            sym = b.add("method", str(name), schema.line, schema.end, "schema", parent=holder,
                        doc=("Fields: " + ", ".join(fields[:8]) + ".") if fields else "",
                        keys=[f"oas:{path}:schema:{name}"], refs=[r for r in refs if not r[0].endswith(f":{name}")])
            for k, prop in (props.items() if props is not None else []):
                typ = prop.at("type").text() if prop.kind == "map" and prop.get("type") is not None else (
                    prop.at("$ref").text().rsplit("/", 1)[-1] if prop.kind == "map" and prop.get("$ref") is not None else "")
                sym.attrs[str(k)] = (typ,)
    return b.done()


def parse_graphql(path: str, text: str) -> FileInfo:
    b = Builder(path, "graphql", text)
    spec = Spec(name="graphql", family="data", line_comments=("#",), block_comments=(), triple=('"""',), sq="none")
    code = blank(text, spec, keep_strings=False)
    code_lines = code.splitlines()
    defs = re.compile(r"(?m)^(?:extend\s+)?(type|input|interface|enum|union|scalar|schema)\b\s*(\w*)([^{\n]*)(\{)?")
    builtin = {"String", "Int", "Float", "Boolean", "ID"}
    for m in defs.finditer(code):
        kind, name = m.group(1), m.group(2) or m.group(1)
        start = text.count("\n", 0, m.start()) + 1
        if not m.group(4):
            refs = [(f"gql:{t}", 0.9) for t in re.findall(r"\b([A-Z]\w*)\b", m.group(3)) if t not in builtin]
            b.add("function" if kind in ("scalar", "union") else "class", name, start, start, kind, keys=[f"gql:{name}"], refs=refs)
            continue
        open_at = m.end() - 1
        depth, k = 0, open_at
        while k < len(code):
            if code[k] == "{":
                depth += 1
            elif code[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        end = text.count("\n", 0, k) + 1
        root_op = name in ("Query", "Mutation", "Subscription")
        cls = b.add("class", name, start, end, "operations" if root_op else kind, keys=[f"gql:{name}"],
                    doc=_gql_desc(b.lines, start))
        body_start = text.count("\n", 0, open_at) + 1
        for no in range(body_start + 1, end):
            line = code_lines[no - 1] if no - 1 < len(code_lines) else ""
            fm = re.match(r"^\s*(\w+)\s*(\([^)]*\))?\s*:\s*([\[\]\w!]+)", line)
            if not fm and kind == "enum":
                fm2 = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*$", line)
                if fm2:
                    b.add("method", fm2.group(1), no, no, "value", parent=cls)
                continue
            if not fm:
                continue
            types = re.findall(r"\b([A-Z]\w*)\b", (fm.group(2) or "") + " " + fm.group(3))
            sub = name.lower() if root_op else "field"
            field = b.add("method", fm.group(1), no, no, sub, parent=cls, doc=_gql_desc(b.lines, no),
                          refs=[(f"gql:{t}", 0.9) for t in types if t not in builtin and t != name],
                          keys=[f"api:{name.upper()} {fm.group(1)}"] if root_op else [])
            cls.attrs[fm.group(1)] = (fm.group(3),)
            if root_op:
                field.routes = [f"{name.upper()} {fm.group(1)}"]
    return b.done()


def _gql_desc(lines: list, line_no: int) -> str:
    k = line_no - 2
    while k >= 0 and lines[k].strip().startswith(("#", '"')):
        txt = lines[k].strip().strip('#"').strip()
        if txt:
            return txt
        k -= 1
    return ""


def parse_proto(path: str, text: str) -> FileInfo:
    b = Builder(path, "proto", text)
    spec = SPECS["java"]
    code = blank(text, spec, keep_strings=False)
    pkg = re.search(r"(?m)^\s*package\s+([\w.]+)\s*;", text)
    pkg = pkg.group(1) + "." if pkg else ""
    scalars = {"double", "float", "int32", "int64", "uint32", "uint64", "sint32", "sint64", "fixed32", "fixed64",
               "sfixed32", "sfixed64", "bool", "string", "bytes", "map", "repeated", "optional"}
    for m in re.finditer(r"(?m)^\s*(service|message|enum)\s+(\w+)\s*\{", code):
        kind, name = m.group(1), m.group(2)
        open_at = m.end() - 1
        depth, k = 0, open_at
        while k < len(code):
            if code[k] == "{":
                depth += 1
            elif code[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        start, end = text.count("\n", 0, m.start()) + 1, text.count("\n", 0, k) + 1
        cls = b.add("class", name, start, end, kind, keys=[f"proto:{pkg}{name}", f"proto:{name}"])
        body_lines = code.splitlines()[start:end - 1]
        for offset, line in enumerate(body_lines):
            no = start + 1 + offset
            rpc = re.match(r"^\s*rpc\s+(\w+)\s*\(\s*(?:stream\s+)?([\w.]+)\s*\)\s*returns\s*\(\s*(?:stream\s+)?([\w.]+)\s*\)", line)
            if rpc and kind == "service":
                field = b.add("method", rpc.group(1), no, no, "rpc", parent=cls,
                              refs=[(f"proto:{rpc.group(2).rsplit('.', 1)[-1]}", 0.95), (f"proto:{rpc.group(3).rsplit('.', 1)[-1]}", 0.95)],
                              keys=[f"api:RPC {name}.{rpc.group(1)}"])
                field.routes = [f"RPC {name}.{rpc.group(1)}"]
                continue
            fld = re.match(r"^\s*(?:repeated\s+|optional\s+)?(map\s*<[^>]+>|[\w.]+)\s+(\w+)\s*=\s*\d+", line)
            if fld and kind == "message":
                types = [t for t in re.findall(r"[\w.]+", fld.group(1)) if t not in scalars]
                b.add("method", fld.group(2), no, no, "field", parent=cls,
                      refs=[(f"proto:{t.rsplit('.', 1)[-1]}", 0.9) for t in types])
                cls.attrs[fld.group(2)] = (fld.group(1),)
    return b.done()


# ================================================================ data

def _sql_statements(text: str):
    """Yield (start offset, end offset, statement text) split on ';' outside strings and comments."""
    spec = Spec(name="sql", family="data", line_comments=("--",), sq="string", dq_multiline=True)
    code = blank(text, spec, keep_strings=False)
    start = 0
    for m in re.finditer(r";", code):
        yield start, m.end(), code[start:m.end()]
        start = m.end()
    if code[start:].strip():
        yield start, len(code), code[start:]


def parse_sql(path: str, text: str) -> FileInfo:
    b = Builder(path, "sql", text)
    for s0, s1, stmt in _sql_statements(text):
        body = stmt.strip()
        if not body:
            continue
        lead = len(stmt) - len(stmt.lstrip())
        start = text.count("\n", 0, s0 + lead) + 1
        end = text.count("\n", 0, max(s0 + lead, s1 - 1)) + 1
        refs = [(f"sql:table:{t.lower()}", 0.9) for t in re.findall(r"(?i)\bREFERENCES\s+[`\"\[]?([\w.]+)", body)]
        m = re.match(r"(?is)^CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?([\w.]+)", body)
        if m:
            table = m.group(1).split(".")[-1]
            cls = b.add("class", table, start, end, "table", keys=[f"sql:table:{table.lower()}"], refs=refs)
            for offset, line in enumerate(text.splitlines()[start:end - 1]):
                cm = re.match(r"^\s*[`\"\[]?(\w+)[`\"\]]?\s+([A-Za-z]\w*(?:\s*\([^)]*\))?)", line)
                if cm and cm.group(1).upper() not in ("PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT", "CHECK", "KEY", "INDEX"):
                    col_refs = [(f"sql:table:{t.lower()}", 0.9) for t in re.findall(r"(?i)\bREFERENCES\s+[`\"\[]?([\w.]+)", line)]
                    b.add("method", cm.group(1), start + 1 + offset, start + 1 + offset, "column", parent=cls,
                          sig=line.strip().rstrip(","), refs=col_refs)
                    cls.attrs[cm.group(1)] = (cm.group(2),)
            continue
        verb = re.match(r"(?is)^(CREATE\s+(?:OR\s+REPLACE\s+)?(?:UNIQUE\s+)?(?:INDEX|VIEW|MATERIALIZED\s+VIEW|FUNCTION|PROCEDURE|TRIGGER|TYPE|SEQUENCE|EXTENSION|SCHEMA)|ALTER\s+TABLE|DROP\s+\w+|INSERT\s+INTO|UPDATE|DELETE\s+FROM|GRANT|REVOKE|TRUNCATE(?:\s+TABLE)?)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?(?:ONLY\s+)?[`\"\[]?([\w.]+)", body)
        if not verb:
            continue
        kind = " ".join(verb.group(1).split()).upper()
        target = verb.group(2).split(".")[-1]
        tables = re.findall(r"(?i)\b(?:FROM|JOIN|INTO|UPDATE|TABLE|ON)\s+[`\"\[]?([\w.]+)", body)
        refs += [(f"sql:table:{t.split('.')[-1].lower()}", 0.85) for t in tables if t.split(".")[-1].lower() != target.lower() or kind.startswith(("ALTER", "DROP", "INSERT", "UPDATE", "DELETE", "TRUNCATE"))]
        if kind.startswith("ALTER TABLE"):
            clause = re.search(r"(?is)ALTER\s+TABLE\s+\S+\s+(.{0,60})", body)
            name = f"ALTER {target}: " + " ".join((clause.group(1) if clause else "").split())[:40]
            sub = "migration"
        else:
            name = f"{kind.split()[0]} {target}" if not kind.startswith("CREATE") else target
            sub = kind.replace("CREATE ", "").replace("OR REPLACE ", "").lower()
        b.add("function", name, start, end, sub, sig=" ".join(body.split())[:120],
              keys=[f"sql:{sub}:{target.lower()}"] if kind.startswith("CREATE") else [], refs=refs)
    return b.done()


def parse_prisma(path: str, text: str) -> FileInfo:
    b = Builder(path, "prisma", text)
    models = set(re.findall(r"(?m)^\s*(?:model|enum|type)\s+(\w+)", text))
    lines = b.lines
    i = 0
    while i < len(lines):
        m = re.match(r"^\s*(model|enum|type|datasource|generator)\s+(\w+)\s*\{", lines[i])
        if not m:
            i += 1
            continue
        start = i + 1
        j = i + 1
        while j < len(lines) and not lines[j].strip().startswith("}"):
            j += 1
        cls = b.add("class", m.group(2), start, j + 1, m.group(1), keys=[f"prisma:{m.group(2)}", f"sql:table:{m.group(2).lower()}"])
        for k in range(i + 1, j):
            fm = re.match(r"^\s*(\w+)\s+(\w+)(\[\])?\??", lines[k])
            if fm and not lines[k].strip().startswith("@@"):
                refs = [(f"prisma:{fm.group(2)}", 0.95)] if fm.group(2) in models and fm.group(2) != m.group(2) else []
                b.add("method", fm.group(1), k + 1, k + 1, "field", parent=cls, refs=refs)
                cls.attrs[fm.group(1)] = (fm.group(2),)
        i = j + 1
    return b.done()


# ================================================================ dependencies

def parse_deps(path: str, text: str, lang: str) -> FileInfo:
    b = Builder(path, lang, text)
    kind = lang.split("-", 1)[1]
    found: list = []           # (name, version, line, sub)
    if kind in ("npm", "composer"):
        root = parse_json(text)
        if root is not None and root.kind == "map":
            groups = (("dependencies", "package"), ("devDependencies", "dev package"), ("peerDependencies", "peer package"),
                      ("optionalDependencies", "optional package")) if kind == "npm" else (("require", "package"), ("require-dev", "dev package"))
            for section, sub in groups:
                node = root.get(section)
                for name, v in (node.items() if node is not None else []):
                    found.append((name, v.text(), v.line, sub))
            scripts = root.get("scripts")
            for name, v in (scripts.items() if scripts is not None else []):
                refs = [(f"file:{posixpath.normpath(posixpath.join(b.dir, s)).lstrip('./')}", 0.8) for s in script_refs(v.text())]
                b.add("function", f"npm run {name}", v.line, v.end, "npm script", sig=f"{name}: {v.text()}"[:160],
                      keys=[f"npm-script:{path}:{name}"], refs=refs)
    elif kind == "pip":
        for no, line in enumerate(b.lines, 1):
            s = line.split("#", 1)[0].strip()
            if not s:
                continue
            if s.startswith(("-r", "--requirement", "-c")):
                parts = s.split(None, 1)
                if len(parts) == 2:
                    b.add("function", f"includes {parts[1]}", no, no, "include", refs=[b.file_ref(parts[1])])
                continue
            m = re.match(r"^([A-Za-z0-9_.\-\[\],]+)\s*(.*)$", s)
            if m and not s.startswith("-"):
                found.append((re.sub(r"\[.*\]", "", m.group(1)), m.group(2).strip(), no, "package"))
    elif kind in ("pyproject", "pipfile", "cargo", "pip-setupcfg"):
        section = ""
        in_array = False
        for no, line in enumerate(b.lines, 1):
            s = line.split("#", 1)[0].strip()
            sec = re.match(r"^\[([^\]]+)\]$", s)
            if sec:
                section, in_array = sec.group(1), False
                continue
            dev = "dev" in section or "test" in section or "optional" in section
            sub = "dev package" if dev else "package"
            if in_array:
                if s.startswith("]"):
                    in_array = False
                    continue
                q = re.match(r"""^["']([A-Za-z0-9_.\-\[\]]+)\s*([^"']*)["'],?""", s)
                if q:
                    found.append((re.sub(r"\[.*\]", "", q.group(1)), q.group(2).strip(), no, sub))
                continue
            if re.match(r"^(dependencies|optional-dependencies|dev-dependencies|install_requires|requires)\s*=\s*\[", s) or \
                    (section.startswith(("project.optional-dependencies", "dependency-groups")) and s.endswith("[")):
                in_array = not s.rstrip().endswith("]")
                for q in re.finditer(r"""["']([A-Za-z0-9_.\-\[\]]+)\s*([^"']*)["']""", s.split("[", 1)[1] if "[" in s else ""):
                    found.append((re.sub(r"\[.*\]", "", q.group(1)), q.group(2).strip(), no, sub))
                continue
            if any(x in section for x in ("dependencies", "packages", "dev-packages")):
                kv = re.match(r"""^["']?([A-Za-z0-9_.\-]+)["']?\s*=\s*(.+)$""", s)
                if kv and kv.group(1) not in ("python",):
                    ver = re.search(r"""version\s*=\s*["']([^"']+)""", kv.group(2))
                    found.append((kv.group(1), ver.group(1) if ver else kv.group(2).strip("\"' "), no, sub))
    elif kind == "gomod":
        block = False
        for no, line in enumerate(b.lines, 1):
            s = line.split("//", 1)[0].strip()
            if s.startswith("require ("):
                block = True
                continue
            if block and s == ")":
                block = False
                continue
            m = re.match(r"^(?:require\s+)?([\w./\-~]+)\s+(v[\w.\-+]+)", s) if (block or s.startswith("require ")) else None
            if m:
                found.append((m.group(1), m.group(2), no, "module"))
    elif kind == "maven":
        for m in re.finditer(r"(?s)<dependency>(.*?)</dependency>", text):
            body = m.group(1)
            g = re.search(r"<groupId>([^<]+)", body)
            a = re.search(r"<artifactId>([^<]+)", body)
            v = re.search(r"<version>([^<]+)", body)
            scope = re.search(r"<scope>([^<]+)", body)
            if a:
                line = text.count("\n", 0, m.start()) + 1
                end = text.count("\n", 0, m.end()) + 1
                b.add("function", f"{g.group(1) if g else ''}:{a.group(1)}".strip(":"), line, end,
                      "test package" if scope and scope.group(1) == "test" else "package",
                      sig=f"{a.group(1)} {v.group(1) if v else ''}".strip())
    elif kind == "gradle":
        for no, line in enumerate(b.lines, 1):
            m = re.match(r"""^\s*(implementation|api|compileOnly|runtimeOnly|testImplementation|kapt|ksp|annotationProcessor|classpath)\s*\(?\s*["']([^"']+)["']""", line)
            if m:
                found.append((m.group(2).rsplit(":", 1)[0] if m.group(2).count(":") >= 2 else m.group(2),
                              m.group(2).rsplit(":", 1)[-1] if m.group(2).count(":") >= 2 else "", no,
                              "test package" if m.group(1).startswith("test") else "package"))
    elif kind == "gem":
        for no, line in enumerate(b.lines, 1):
            m = re.match(r"""^\s*gem\s+["']([^"']+)["'](?:\s*,\s*["']([^"']+)["'])?""", line)
            if m:
                found.append((m.group(1), m.group(2) or "", no, "gem"))
    elif kind == "nuget":
        for m in re.finditer(r"""<Package(?:Reference|Version)?\s+(?:Include|id)="([^"]+)"(?:\s+Version|\s+version)?="?([^"/>]*)""", text):
            found.append((m.group(1), m.group(2), text.count("\n", 0, m.start()) + 1, "package"))
    for name, version, line, sub in found:
        b.add("function", name, line, line, sub, sig=f"{name} {version}".strip()[:160], keys=[f"pkg:{name.lower()}"])
    return b.done()


# ================================================================ notebooks, config, docs

def parse_notebook(path: str, text: str) -> FileInfo:
    b = Builder(path, "notebook", text)
    root = parse_json(text)
    cells = root.get("cells") if root is not None and root.kind == "map" else None
    last_md = ""
    for i, cell in enumerate(cells.children if cells is not None else [], 1):
        if cell.kind != "map":
            continue
        ctype = cell.at("cell_type").text()
        src_node = cell.get("source")
        if src_node is None:
            continue
        source = "".join(c.text() for c in src_node.children) if src_node.kind == "seq" else src_node.text()
        if ctype == "markdown":
            heading = re.search(r"(?m)^#+\s*(.+)$", source)
            last_md = heading.group(1) if heading else first_sentence(source)
            continue
        if ctype != "code" or not source.strip():
            continue
        defined, called = [], []
        try:
            tree = ast.parse(source)
            defined = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            called = list(dict.fromkeys(n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)))
        except SyntaxError:
            pass
        first = next((ln.strip() for ln in source.splitlines() if ln.strip() and not ln.strip().startswith("#")), f"cell {i}")
        name = f"cell {i}: " + (defined[0] if defined else first[:40])
        b.add("function", name, src_node.line, max(src_node.end, cell.end), "code cell", doc=last_md,
              sig=first[:120], keys=[f"nbdef:{path}:{d}" for d in defined],
              refs=[(f"nbdef:{path}:{c}", 0.9) for c in called if c not in defined])
        last_md = ""
    return b.done()


def parse_settings(path: str, text: str, lang: str) -> FileInfo:
    """Generic config: top-level keys / sections. `.env` values are never stored."""
    b = Builder(path, lang, text)
    if lang == "env":
        for no, line in enumerate(b.lines, 1):
            m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][\w.]*)\s*=", line)
            if m:
                b.add("function", m.group(1), no, no, "env var", sig=f"{m.group(1)}=•••")
        return b.done()
    if lang in ("yaml", "json", "helm-values"):
        docs = parse_any(text, lang == "json")
        root = docs[0] if docs else None
        for key, node in (root.items() if root is not None and root.kind == "map" else []):
            b.add("function", str(key), node.line, node.end, "setting")
        return b.done()
    sections = []
    for no, line in enumerate(b.lines, 1):
        sec = re.match(r"^\s*\[\[?([^\]]+)\]\]?\s*$", line)
        if sec:
            sections.append((no, sec.group(1)))
    if sections:
        for i, (no, name) in enumerate(sections):
            end = sections[i + 1][0] - 1 if i + 1 < len(sections) else len(b.lines)
            b.add("function", f"[{name}]", no, end, "section")
    else:
        for no, line in enumerate(b.lines, 1):
            m = re.match(r"^([A-Za-z_][\w.\-]*)\s*[=:]", line)
            if m:
                b.add("function", m.group(1), no, no, "setting")
    return b.done()


def parse_markdown(path: str, text: str) -> FileInfo:
    b = Builder(path, "markdown", text)
    heads = []
    fence = False
    for no, line in enumerate(b.lines, 1):
        if line.strip().startswith(("```", "~~~")):
            fence = not fence
        m = re.match(r"^(#{1,3})\s+(.+?)\s*#*\s*$", line)
        if m and not fence:
            heads.append((no, len(m.group(1)), m.group(2)))
    for i, (no, level, title) in enumerate(heads):
        end = len(b.lines)
        for later_no, later_level, _ in heads[i + 1:]:
            if later_level <= level:
                end = later_no - 1
                break
        else:
            if i + 1 < len(heads):
                end = heads[i + 1][0] - 1 if heads[i + 1][1] <= level else end
        b.add("function", title[:60], no, end, "section")
    if not heads and b.lines:
        b.fi.doc = first_sentence(" ".join(b.lines[:5]))
    return b.done()


PARSERS = {
    "dockerfile": parse_dockerfile, "compose": parse_compose, "kubernetes": parse_kubernetes,
    "terraform": parse_terraform, "bicep": parse_bicep, "cloudformation": parse_cloudformation,
    "github-actions": parse_github_actions, "jenkins": parse_jenkinsfile, "make": parse_make,
    "openapi": parse_openapi, "graphql": parse_graphql, "proto": parse_proto, "sql": parse_sql, "prisma": parse_prisma,
    "notebook": parse_notebook, "markdown": parse_markdown,
}


def parse_config(path: str, text: str, fmt: str) -> FileInfo:
    if fmt in PARSERS:
        return PARSERS[fmt](path, text)
    if fmt in ("gitlab-ci", "azure-pipelines", "circleci", "bitbucket", "cloudbuild", "buildspec"):
        return parse_ci_yaml(path, text, fmt)
    if fmt.startswith("deps-"):
        return parse_deps(path, text, fmt)
    if fmt in ("env", "yaml", "json", "toml", "ini", "helm-values"):
        return parse_settings(path, text, fmt)
    fi = FileInfo(path=path, lang=fmt)                     # unknown text: the agent describes it from the diff
    fi.loc = sum(1 for ln in text.splitlines() if ln.strip())
    return fi
