"""Symbol graph: resolves imports, call sites and explicit references into edges.

Resolution is heuristic but conservative. Every edge carries a confidence, and the
LLM never invents edges. Order of trust: self/this members, same-file names, imported
names, same package/namespace/directory, module-qualified names, then a unique global
name. Infra/API/data files contribute explicit references (Terraform addresses,
Kubernetes selectors, OpenAPI $refs, CI `needs`, routes) that resolve through keys.
"""
from __future__ import annotations

import posixpath
from collections import Counter, defaultdict

from .algo import pagerank
from .parse_generic import SPECS
from .tsconfig import TSConfigResolver

PY_BUILTINS = frozenset("""abs all any ascii bin bool breakpoint bytearray bytes callable chr classmethod compile
complex delattr dict dir divmod enumerate eval exec filter float format frozenset getattr globals hasattr hash
help hex id input int isinstance issubclass iter len list locals map max memoryview min next object oct open ord
pow print property range repr reversed round set setattr slice sorted staticmethod str sum super tuple type vars
zip __import__ NotImplementedError ValueError TypeError KeyError IndexError RuntimeError Exception""".split())
JS_GLOBALS = frozenset("""console Math JSON Object Array Promise String Number Boolean Date RegExp Error Map Set
WeakMap WeakSet Symbol Reflect Proxy Intl fetch setTimeout clearTimeout setInterval clearInterval
requestAnimationFrame document window globalThis process require module exports Buffer parseInt parseFloat
isNaN encodeURIComponent decodeURIComponent structuredClone queueMicrotask alert localStorage sessionStorage
navigator location history URL URLSearchParams FormData Headers Request Response AbortController
TextEncoder TextDecoder crypto performance describe it test expect beforeEach afterEach jest vi""".split())
# Library / runtime names that must never be guessed onto repo symbols.
PLATFORM = frozenset("""System String Integer Long Double Boolean Object List ArrayList Map HashMap Set HashSet Optional
Collections Arrays Stream Collectors Math Thread Files Paths Path Duration Instant LocalDate UUID Objects Console
Task Enumerable Convert Guid DateTime TimeSpan Environment fmt log os io strings strconv errors time json http
context sync filepath bytes Vec Box Some None Ok Err Rc Arc std println print printf malloc free strlen memcpy
NSString Array Dictionary DispatchQueue Kernel Enum Map Logger IO Integer Float File Dir Rails ActiveRecord""".split())
# Method names too common to guess a target for when the receiver is unknown.
COMMON_METHODS = frozenset("""get set add put pop append extend update keys values items join split strip format
read write close open run start stop send emit on off map filter reduce forEach push then catch finally find
replace lower upper encode decode copy clear remove insert index count sort reverse call apply bind toString
render init setup load dump dumps loads parse log info debug warn error exception json text match search test
exec next has delete length size slice splice concat includes indexOf trim fetch query execute commit
rollback cursor connect listen use handle process create save list new build of from equals hashCode""".split())
ENTRY_FILES = frozenset("__main__ main cli app server manage index wsgi asgi run program startup application".split())
ENTRY_DECORATORS = ("route", "get", "post", "put", "patch", "delete", "command", "group", "task", "websocket",
                    "api_view", "action", "callback", "listener", "handler", "on_event", "subscribe")
JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte")
FILE_EXTS = {
    "c": (".h", ".hpp", ".hh", ".c", ".cpp", ".cc"), "ruby": (".rb",), "script": (".sh", ".bash", ".ps1", ".psm1", ".R", ".r", ".lua"),
    "dart": (".dart",), "php": (".php",), "sol": (".sol",), "rust": (".rs",), "elixir": (".ex", ".exs"),
}


def _family(lang: str) -> str:
    if lang == "python" or lang == "notebook":
        return "py"
    if lang in ("javascript", "typescript", "vue", "svelte", "astro"):
        return "js"
    spec = SPECS.get(lang)
    return spec.family if spec else "data"


class CodeGraph:
    def __init__(self, files: dict, root=None):
        self.files = files
        self.syms: dict = {}
        self.top: dict = defaultdict(dict)          # file -> name -> top-level sym id
        self.members: dict = defaultdict(dict)      # class id -> member name -> sym id
        self.global_names: dict = defaultdict(list)
        self.method_names: dict = defaultdict(list)
        self.keys: dict = defaultdict(list)         # key -> sym ids
        self.classes_by_name: dict = defaultdict(list)
        self.bind: dict = {}                        # file -> alias -> ("module", path) | ("symbol", id) | ("gopkg", dir)
        self.visible_files: dict = defaultdict(list)
        self.visible_ns: dict = defaultdict(list)
        self.external: dict = defaultdict(set)      # file -> aliases bound to third-party modules
        self.ext_usage: Counter = Counter()
        self.edges: dict = {}                       # (src, dst) -> [kind, weight, confidence]
        self.file_edges: dict = defaultdict(float)  # (file a, file b) -> weight
        self.bases: dict = defaultdict(list)        # class id -> resolved base class ids
        self.entry: dict = defaultdict(int)
        self.rank: dict = {}
        self.callers: dict = defaultdict(set)
        self.callees: dict = defaultdict(set)
        self.paths = set(files)
        self.py_modules: dict = defaultdict(list)
        self.family = {p: _family(fi.lang) for p, fi in files.items()}
        self.dirs = {posixpath.dirname(p) for p in files}
        self.tsconfig = TSConfigResolver(root) if root is not None else None
        self.lexical: dict = defaultdict(list)       # (enclosing declaration, name) -> Python declarations
        self.py_top: dict = defaultdict(list)
        self.local_bind: dict = {}

    # ------------------------------------------------------------------ build
    def build(self) -> "CodeGraph":
        self._index()
        self._bind_imports()
        self._bind_local_imports()
        self._resolve_bases()
        self._resolve_calls()
        self._resolve_refs()
        self._entries()
        nodes = list(self.syms)
        self.rank = pagerank(nodes, [(a, b, e[1]) for (a, b), e in self.edges.items()])
        return self

    def _index(self) -> None:
        for path, fi in self.files.items():
            first_top = None
            for s in fi.symbols:
                self.syms[s.id] = s
                for key in s.keys:
                    self.keys[key].append(s.id)
                python = self.family[path] == "py"
                if python and s.lexical_parent:
                    self.lexical[(s.lexical_parent, s.name)].append(s.id)
                if s.kind == "class" and not (python and s.lexical_parent):
                    self.classes_by_name[s.name].append(s.id)
                if s.parent is None and not (python and s.lexical_parent):
                    self.top[path].setdefault(s.name, s.id)
                    self.global_names[s.name].append(s.id)
                    if python:
                        self.py_top[(path, s.name)].append(s.id)
                    first_top = first_top or s.id
                elif s.parent is not None:
                    self.members[s.parent].setdefault(s.name, s.id)
                    self.method_names[s.name].append(s.id)
            if first_top and f"file:{path}" not in self.keys:
                self.keys[f"file:{path}"].append(first_top)
            if fi.lang in ("python", "notebook"):
                parts = path[: path.rfind(".")].split("/")
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                for k in range(len(parts)):
                    self.py_modules[".".join(parts[k:])].append(path)

    # ---------------------------------------------------------------- imports
    def _py_module(self, importer: str, module: str, level: int) -> str | None:
        if level:
            base = importer.split("/")[:-1]
            if level > 1:
                base = base[: max(0, len(base) - (level - 1))]
            stem = "/".join(base + (module.split(".") if module else []))
            for cand in (stem + ".py", stem + "/__init__.py"):
                if cand.lstrip("/") in self.paths:
                    return cand.lstrip("/")
            return None
        cands = self.py_modules.get(module)
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]
        return max(cands, key=lambda c: (self._shared(c, importer), -len(c)))

    @staticmethod
    def _shared(a: str, b: str) -> int:
        x, y = a.split("/"), b.split("/")
        k = 0
        while k < min(len(x), len(y)) and x[k] == y[k]:
            k += 1
        return k

    def _js_module(self, importer: str, spec: str) -> str | None:
        if spec.startswith("."):
            bases = [posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))]
        else:
            if self.tsconfig is not None:
                handled, target = self.tsconfig.resolve(importer, spec, self._js_config_module)
                if handled:
                    return target
            if spec.startswith(("@/", "~/")):
                bases = ["src/" + spec[2:], spec[2:]]
            else:
                return None
        for base in bases:
            cands = [base] + [base + e for e in JS_EXTS] + [base + "/index" + e for e in JS_EXTS]
            if base.endswith((".js", ".jsx", ".mjs", ".cjs")):
                stem = base[: base.rfind(".")]
                cands += [stem + e for e in JS_EXTS]
            for c in cands:
                if c in self.paths:
                    return c
        return None

    def _js_config_module(self, base: str) -> tuple:
        """Resolve configured aliases only when a source target is unambiguous."""
        if base.endswith(JS_EXTS):
            substitutions = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}
            ext = posixpath.splitext(base)[1]
            candidates = [base] + [base[:-len(ext)] + e for e in substitutions.get(ext, ())]
        else:
            candidates = [base] + [base + e for e in JS_EXTS]
        matches = sorted({c for c in candidates if c in self.paths})
        if matches:
            return True, matches[0] if len(matches) == 1 else None
        matches = sorted({base + "/index" + e for e in JS_EXTS if base + "/index" + e in self.paths})
        return (True, matches[0] if len(matches) == 1 else None) if matches else (False, None)

    def _file_module(self, importer: str, spec: str, family: str) -> str | None:
        """Resolve an include / require / source / mod path to a repo file."""
        spec = spec.strip().replace("\\", "/")
        if not spec or "$" in spec and "{" in spec:
            return None
        here = posixpath.dirname(importer)
        exts = FILE_EXTS.get(family, ()) + ("",)
        names = [spec]
        if family in ("script", "ruby") and "." not in posixpath.basename(spec):
            names.append(spec.replace(".", "/"))
        cands = []
        for name in names:
            for base in (posixpath.normpath(posixpath.join(here, name)), posixpath.normpath(name.lstrip("./"))):
                cands += [base + e for e in exts]
                if family == "rust":
                    cands.append(base + "/mod.rs")
                if family == "script":
                    cands.append(base + "/init.lua")
        for c in cands:
            c = c.lstrip("/")
            if c in self.paths:
                return c
        tail = "/" + spec.lstrip("./")
        matches = sorted(p for p in self.paths
                         if p.endswith(tail) or any(p.endswith(tail + e) for e in exts if e))
        return matches[0] if len(matches) == 1 else None

    def _export(self, path: str, name: str, depth: int = 0):
        """What does module `path` export under `name`? Follows re-exports a few hops."""
        if depth > 4:
            return None
        if self.family.get(path) == "py":
            if len(self.py_top.get((path, name), ())) > 1 or name in self.files[path].assigned_names:
                return None
        if name in self.top.get(path, {}):
            return ("symbol", self.top[path][name])
        fi = self.files[path]
        if self.family.get(path) == "js":
            if name == "default" and fi.default_export:
                return self._export(path, fi.default_export, depth + 1) or ("module", path)
            for exported, spec, original in fi.reexports:
                if exported == name or exported == "*":
                    target = self._js_module(path, spec)
                    if target:
                        found = self._export(target, name if exported == "*" else original, depth + 1)
                        if found:
                            return found
        bound = self.bind.get(path, {}).get(name)
        if bound and bound[0] in ("symbol", "module"):
            return bound
        if bound and bound[0] == "pending":
            return self._export(bound[1], bound[2], depth + 1)
        return None

    def _bind_py(self, path: str, imp, b: dict, pending: list) -> None:
        if imp.name is None:
            target = self._py_module(path, imp.module, imp.level)
            if target:
                b[imp.alias] = ("module", target)
            else:
                self.external[path].add(imp.alias)
                self.ext_usage[imp.module.split(".")[0]] += 1
            return
        mod = self._py_module(path, imp.module, imp.level)
        if not mod:
            sub = self._py_module(path, (imp.module + "." if imp.module else "") + imp.name, imp.level)
            if sub:
                b[imp.alias] = ("module", sub)
            else:
                self.external[path].add(imp.alias)
                self.ext_usage[(imp.module or ".").split(".")[0]] += 1
            return
        if imp.name == "*":
            for name, sid in self.top.get(mod, {}).items():
                b.setdefault(name, self._export(mod, name) or ("unknown", None))
        elif imp.name in self.top.get(mod, {}):
            b[imp.alias] = self._export(mod, imp.name) or ("unknown", None)
        else:
            sub = self._py_module(path, (imp.module + "." if imp.module else "") + imp.name, imp.level)
            b[imp.alias] = ("module", sub) if sub else ("pending", mod, imp.name)
            if not sub:
                pending.append((path, imp.alias))
        self.file_edges[(path, mod)] += 0.5

    def _bind_js(self, path: str, imp, b: dict, pending: list) -> None:
        if not imp.alias:
            return
        target = self._js_module(path, imp.module)
        if not target:
            self.external[path].add(imp.alias)
            self.ext_usage[imp.module.split("/")[0]] += 1
            return
        self.file_edges[(path, target)] += 0.5
        if imp.name is None:
            b[imp.alias] = ("module", target)
        else:
            b[imp.alias] = ("pending", target, imp.name)
            pending.append((path, imp.alias))

    def _bind_generic(self, path: str, imp, b: dict, family: str) -> None:
        kind = imp.kind
        if kind == "namespace":
            self.visible_ns[path].append(imp.module)
            return
        if kind == "type":
            module = imp.module.replace("::", ".").replace("\\", ".")
            for key in (f"fqn:{module}", f"ex:{module}"):
                if self.keys.get(key):
                    target = self.keys[key][0]
                    b[imp.alias] = ("symbol", target)
                    self.file_edges[(path, self.syms[target].file)] += 0.5
                    return
            last = module.rsplit(".", 1)[-1]
            cands = [c for c in self.classes_by_name.get(last, []) + self.top_ids(last)
                     if module.split(".")[-2:-1] == [] or module.split(".")[-2] in self.syms[c].file]
            if family == "rust" and module.startswith(("crate.", "super.", "self.")) and cands:
                b[imp.alias] = ("symbol", cands[0])
                return
            self.external[path].add(imp.alias)
            self.ext_usage[module.split(".")[0]] += 1
            return
        if kind == "package":                        # Go: import "github.com/acme/notes/internal/cache"
            parts = imp.module.split("/")
            for k in range(len(parts)):
                suffix = "/".join(parts[k:])
                match = [d for d in self.dirs if d == suffix or d.endswith("/" + suffix)]
                if len(match) == 1:
                    b[imp.alias] = ("gopkg", match[0])
                    return
            self.external[path].add(imp.alias)
            self.ext_usage[parts[0]] += 1
            return
        target = self._file_module(path, imp.module, family)
        if target:
            self.visible_files[path].append(target)
            self.file_edges[(path, target)] += 0.5
            if imp.alias:
                b[imp.alias] = ("module", target)
        elif imp.alias:
            self.external[path].add(imp.alias)
            self.ext_usage[imp.module.split("/")[0]] += 1

    def top_ids(self, name: str) -> list:
        return self.global_names.get(name, [])

    def _bind_imports(self) -> None:
        pending: list = []
        for path, fi in self.files.items():
            b: dict = {}
            fam = self.family[path]
            for imp in fi.imports:
                if fam == "py":
                    self._bind_py(path, imp, b, pending)
                elif fam == "js":
                    self._bind_js(path, imp, b, pending)
                elif fam != "data":
                    self._bind_generic(path, imp, b, fam)
            self.bind[path] = b
        for path, alias in pending:
            bound = self.bind[path].get(alias)
            if not bound or bound[0] != "pending":   # re-bound later in the same file
                continue
            found = self._export(bound[1], bound[2])
            self.bind[path][alias] = found if found else ("module", bound[1])

    # ------------------------------------------------------------------ calls
    def _method(self, cls_id: str, name: str, depth: int = 0) -> str | None:
        if name in self.members.get(cls_id, {}):
            return self.members[cls_id][name]
        cls = self.syms.get(cls_id)
        if cls is not None and depth == 0 and self.family.get(cls.file) != "py":
            # partial classes: Go methods in other files, Swift extensions, C++ header/source split
            here = posixpath.dirname(cls.file)
            for other in self.classes_by_name.get(cls.name, []):
                if other != cls_id and (posixpath.dirname(self.syms[other].file) == here
                                        or len(self.classes_by_name[cls.name]) <= 3):
                    if name in self.members.get(other, {}):
                        return self.members[other][name]
        if depth < 5:
            for base in self.bases.get(cls_id, ()):
                found = self._method(base, name, depth + 1)
                if found:
                    return found
        return None

    def _type(self, file: str, name: str):
        """Resolve a bare type/class name as seen from `file`."""
        if name in PLATFORM:
            return None
        top = self.top.get(file, {}).get(name)
        if top and self.syms[top].kind == "class":
            return top
        bound = self.bind.get(file, {}).get(name)
        if bound and bound[0] == "symbol":
            return bound[1]
        fi = self.files.get(file)
        if fi is not None:
            pkg = fi.package
            fam = self.family.get(file)
            keys = []
            if pkg and fam in ("jvm", "dotnet", "php"):
                keys.append(f"fqn:{pkg}.{name}")
            if fam == "go":
                keys.append(f"go:{posixpath.dirname(file)}.{name}")
            keys += [f"fqn:{ns}.{name}" for ns in self.visible_ns.get(file, [])]
            for key in keys:
                if self.keys.get(key):
                    return self.keys[key][0]
            for vf in self.visible_files.get(file, []):
                hit = self.top.get(vf, {}).get(name)
                if hit:
                    return hit
        cands = self.classes_by_name.get(name, [])
        if len(cands) == 1 and name not in self.external.get(file, ()):
            return cands[0]
        return None

    def _attr_class(self, cls_id: str, attr: str, depth: int = 0) -> str | None:
        """Class of `self.<attr>` as inferred by the parser, if it is one of ours."""
        cls = self.syms.get(cls_id)
        if cls is None or depth > 4:
            return None
        chain = cls.attrs.get(attr)
        if chain:
            if self.family.get(cls.file) in ("py", "js"):
                found = self.resolve(cls.file, None, tuple(chain), context=cls.id)
                if found and self.syms[found[0]].kind == "class":
                    return found[0]
            else:
                found = self._type(cls.file, chain[-1])
                if found and self.syms[found].kind == "class":
                    return found
        for base in self.bases.get(cls_id, ()):
            found = self._attr_class(base, attr, depth + 1)
            if found:
                return found
        return None

    def _unique_method(self, name: str):
        if name in COMMON_METHODS or len(name) < 4:
            return None
        ids = self.method_names.get(name, [])
        return (ids[0], 0.4) if len(ids) == 1 else None

    def _bind_local_imports(self) -> None:
        for symbol in self.syms.values():
            if self.family.get(symbol.file) != "py":
                continue
            bindings = {}
            for imp in symbol.local_imports:
                module = self._py_module(symbol.file, imp.module, imp.level)
                bound = None
                if imp.name is None:
                    bound = ("module", module) if module else None
                elif module:
                    bound = self._export(module, imp.name)
                    if not bound:
                        sub = self._py_module(symbol.file, (imp.module + "." if imp.module else "") + imp.name, imp.level)
                        bound = ("module", sub) if sub else None
                else:
                    sub = self._py_module(symbol.file, (imp.module + "." if imp.module else "") + (imp.name or ""), imp.level)
                    bound = ("module", sub) if sub else None
                bindings[imp.alias] = bound or ("unknown", None)
            self.local_bind[symbol.id] = bindings

    def _scope_binding(self, context: str, name: str, line=None):
        first = True
        while context in self.syms:
            symbol = self.syms[context]
            if symbol.kind != "class" or first:
                if name in symbol.declared_globals:
                    return None
                if name in symbol.local_names:
                    return "unknown", None
                candidates = self.lexical.get((context, name), ())
                if candidates:
                    if len(candidates) != 1 or (line is not None and symbol.kind != "class" and
                                                self.syms[candidates[0]].line > line):
                        return "unknown", None
                    return "symbol", candidates[0]
                if name in self.local_bind.get(context, {}):
                    return self.local_bind[context][name]
            first = False
            context = symbol.lexical_parent
        return None

    def _python_member(self, target: str, names: tuple):
        for name in names:
            if self.syms[target].kind != "class":
                return None
            candidates = self.lexical.get((target, name), ())
            if len(candidates) > 1:
                return None
            target = candidates[0] if candidates else self._method(target, name)
            if not target:
                return None
        return target

    def _enclosing_class(self, symbol):
        context = symbol.parent or symbol.lexical_parent
        while context in self.syms:
            owner = self.syms[context]
            if owner.kind == "class":
                return owner.id
            context = owner.lexical_parent
        return None

    def resolve(self, file: str, cls_id: str | None, chain: tuple, context=None, line=None):
        """Return (target sym id, confidence) for a call chain made inside `file`, or None."""
        head = chain[0]
        bind = self.bind.get(file, {})
        fam = self.family.get(file, "py")
        spec = SPECS.get(self.files[file].lang) if file in self.files else None
        if head in ("self", "cls", "this"):
            if not cls_id:
                return None
            if len(chain) == 2:
                found = self._method(cls_id, chain[1])
                return (found, 0.95) if found else None
            if len(chain) == 3:
                owner = self._attr_class(cls_id, chain[1])
                if owner:
                    found = self._method(owner, chain[2])
                    return (found, 0.8) if found else None
                return self._unique_method(chain[2])
            if len(chain) == 4 and chain[2] == "[]":
                owner = self._attr_class(cls_id, chain[1] + "[]")
                found = self._method(owner, chain[3]) if owner else None
                return (found, 0.8) if found else None
            return None
        if head == "super()":
            if cls_id and len(chain) == 2:
                for base in self.bases.get(cls_id, ()):
                    found = self._method(base, chain[1])
                    if found:
                        return found, 0.9
            return None
        if head == "?":
            return self._unique_method(chain[-1])
        scoped = None
        if fam == "py":
            caller = self.syms.get(context)
            local_type = caller.local_types.get(head) if caller else None
            if local_type and len(chain) > 1:
                constructor, assigned = local_type
                if line is not None and line <= assigned:
                    return None
                found = self.resolve(file, cls_id, tuple(constructor), context=context, line=assigned)
                if not found or self.syms[found[0]].kind != "class":
                    return None
                target = self._python_member(found[0], chain[1:])
                return (target, 0.85) if target else None
            scoped = self._scope_binding(context, head, line) if context else None
            if scoped:
                if scoped[0] == "unknown":
                    return None
                if scoped[0] == "symbol":
                    target = scoped[1] if len(chain) == 1 else self._python_member(scoped[1], chain[1:])
                    return (target, 0.95) if target else None
                bind = dict(bind, **{head: scoped})
            elif head in self.files[file].assigned_names:
                return None
            if not scoped and len(self.py_top.get((file, head), ())) > 1:
                return None
        if (not scoped and head in self.external.get(file, ())) or (head in JS_GLOBALS and fam == "js"):
            return None
        if fam == "py" and len(chain) > 1 and not scoped:
            target = self.top.get(file, {}).get(head)
            if target and self.syms[target].kind == "class" and (
                    self.syms[target].status != "deleted" or head not in bind):
                member = self._python_member(target, chain[1:])
                return (member, 0.9) if member else None
        if len(chain) == 1:
            if spec is not None and spec.implicit_self and cls_id:
                found = self._method(cls_id, head)
                if found:
                    return found, 0.9
            top = self.top.get(file, {}).get(head)
            if top and self.syms[top].status != "deleted":
                return top, 0.95
            bound = bind.get(head)
            if bound and bound[0] == "symbol":
                return bound[1], 0.9
            if bound or (head in PY_BUILTINS and fam == "py") or head in PLATFORM:
                return None
            if top:
                return top, 0.95
            if fam not in ("py", "js"):
                typ = self._type(file, head)
                if typ:
                    return typ, 0.85
                for vf in self.visible_files.get(file, []):
                    hit = self.top.get(vf, {}).get(head)
                    if hit:
                        return hit, 0.85
                if fam == "go":
                    hits = self.keys.get(f"go:{posixpath.dirname(file)}.{head}")
                    if hits:
                        return hits[0], 0.9
            ids = self.global_names.get(head, [])
            if len(ids) == 1:
                return ids[0], (0.75 if spec is not None and spec.global_ns else 0.5)
            return None
        for k in range(len(chain) - 1, 0, -1):
            bound = bind.get(".".join(chain[:k]))
            if not bound:
                continue
            rest = chain[k:]
            if bound[0] == "gopkg" and len(rest) == 1:
                hits = self.keys.get(f"go:{bound[1]}.{rest[0]}")
                return (hits[0], 0.9) if hits else None
            if bound[0] == "module":
                found = self._export(bound[1], rest[0])
                if found and found[0] == "symbol":
                    target = found[1]
                    if len(rest) == 2 and self.syms[target].kind == "class":
                        meth = self._method(target, rest[1])
                        return (meth, 0.85) if meth else (target, 0.6)
                    if len(rest) == 1:
                        return target, 0.9
                return None
            if bound[0] == "symbol" and len(rest) == 1 and self.syms[bound[1]].kind == "class":
                meth = self._method(bound[1], rest[0])
                return (meth, 0.85) if meth else None
            return None
        if len(chain) == 2:
            if head in self.top.get(file, {}):
                target = self.top[file][head]
                if self.syms[target].kind == "class":
                    meth = self._method(target, chain[1])
                    return (meth, 0.9) if meth else None
            if cls_id and fam not in ("py", "js"):
                owner = self._attr_class(cls_id, head)      # Java/Kotlin/C#: repo.findAll() on a field
                if owner:
                    meth = self._method(owner, chain[1])
                    return (meth, 0.8) if meth else None
            if fam not in ("py", "js"):
                typ = self._type(file, head)                # static call / Rust Type::new / Elixir Mod.fun
                if typ:
                    meth = self._method(typ, chain[1])
                    return (meth, 0.85) if meth else None
        return self._unique_method(chain[-1])

    def _add_edge(self, src: str, dst: str, kind: str, conf: float) -> None:
        if src == dst:
            return
        e = self.edges.get((src, dst))
        if e:
            e[1] += 1
            e[2] = max(e[2], conf)
        else:
            self.edges[(src, dst)] = [kind, 1, conf]
        self.callees[src].add(dst)
        self.callers[dst].add(src)
        fa, fb = self.syms[src].file, self.syms[dst].file
        if fa != fb:
            self.file_edges[(fa, fb)] += 1

    def _resolve_bases(self) -> None:
        for s in self.syms.values():
            if s.kind != "class":
                continue
            for raw in s.bases:
                if self.family.get(s.file) in ("py", "js"):
                    found = self.resolve(s.file, None, tuple(raw.split(".")), context=s.lexical_parent, line=s.line)
                    target = found[0] if found else None
                    conf = found[1] if found else 0
                else:
                    target, conf = self._type(s.file, raw.split(".")[-1]), 0.85
                if target and target != s.id and self.syms[target].kind == "class":
                    self.bases[s.id].append(target)
                    self._add_edge(s.id, target, "inherits", conf)

    def _resolve_calls(self) -> None:
        for s in list(self.syms.values()):
            cls_id = self._enclosing_class(s) if self.family.get(s.file) == "py" else s.parent
            for chain, _line in s.calls:
                chain = tuple(chain)
                if chain[0] == "&":     # a function passed as a callback: weaker evidence, strong resolution only
                    found = self.resolve(s.file, cls_id, chain[1:], context=s.id, line=_line) if len(chain) > 1 else None
                    if found and found[1] >= 0.85 and self.syms[found[0]].kind != "class":
                        self._add_edge(s.id, found[0], "calls", 0.6)
                    continue
                found = self.resolve(s.file, cls_id, chain, context=s.id, line=_line)
                if found:
                    self._add_edge(s.id, found[0], "calls", found[1])

    # ------------------------------------------------------------------ explicit references
    def _route_lookup(self, key: str) -> list:
        hits = self.keys.get(key)
        if hits:
            return hits
        if not key.startswith(("route:", "api:")):
            return []
        prefix, _, rest = key.partition(":")
        method, _, path = rest.partition(" ")
        segs = [p for p in path.split("/") if p]
        found = []
        for other, ids in self.keys.items():
            if not other.startswith(prefix + ":" + method + " "):
                continue
            osegs = [p for p in other.split(" ", 1)[1].split("/") if p]
            short, long_ = (segs, osegs) if len(segs) <= len(osegs) else (osegs, segs)
            if short and long_[len(long_) - len(short):] == short and len(short) >= 1:
                found.extend(ids)
        return found

    def _resolve_refs(self) -> None:
        for s in list(self.syms.values()):
            for key, conf in s.refs:
                for target in self._route_lookup(key) if key.startswith(("route:", "api:")) else self.keys.get(key, []):
                    if target != s.id:
                        self._add_edge(s.id, target, "refs", conf)

    def _entries(self) -> None:
        for path, fi in self.files.items():
            stem = path.rsplit("/", 1)[-1].split(".")[0].lower()
            for chain in fi.main_calls:
                if not chain or chain[0] == "&":
                    continue
                found = self.resolve(path, None, tuple(chain))
                if found:
                    self.entry[found[0]] += 3
            for s in fi.symbols:
                if s.parent is None and stem in ENTRY_FILES and self.family.get(path) != "data":
                    self.entry[s.id] += 1
                if s.name == "main" or s.sub == "script":
                    self.entry[s.id] += 2
                if s.routes:
                    self.entry[s.id] += 2
                if any(d.rsplit(".", 1)[-1] in ENTRY_DECORATORS for d in s.decorators if not d.startswith(("@", "["))):
                    self.entry[s.id] += 2
                if fi.default_export and s.name == fi.default_export and s.parent is None:
                    self.entry[s.id] += 1
