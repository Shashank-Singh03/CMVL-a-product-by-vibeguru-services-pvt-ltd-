"""HTTP routes: which code serves `GET /notes/{id}`, and which client code calls it.

Handlers get the key "route:GET /notes/{}". API contract operations (OpenAPI) reference that key,
and client calls (fetch, axios, requests, HttpClient) reference both "api:..." and "route:...", so one
endpoint change shows up as client -> contract -> server in the map.
"""
from __future__ import annotations

import re
import io
import tokenize
import posixpath
from collections import defaultdict

from .model import Sym, sym_id
from .parse_config import norm_path
from .parse_generic import SPECS, blank as blank_generic
from .parse_js import blank as blank_js, _calls_in, _fn_body, _match
from .util import sha1

VERB = r"(get|post|put|patch|delete|head|options)"
DECORATOR_ROUTES = [
    # Python (FastAPI, Flask 2, Starlette), NestJS @Get('x'), Micronaut, Rust actix/rocket #[get("/x")]
    re.compile(r"""[@#]\[?(?:[\w.]+\.)?(?i:""" + VERB + r""")\(\s*(?:path\s*=\s*)?[f]?["'`]([^"'`]*)["'`]"""),
    # Spring @GetMapping("/x"), @GetMapping(value = "/x")
    re.compile(r"""@(Get|Post|Put|Patch|Delete)Mapping\s*(?:\(\s*(?:value\s*=\s*|path\s*=\s*)?\{?\s*"([^"]*)")?"""),
    # ASP.NET [HttpGet("x")] / [HttpGet]
    re.compile(r"""\[Http(Get|Post|Put|Patch|Delete)(?:\(\s*(?:template\s*:\s*)?"([^"]*)"\s*\))?"""),
]
FLASK_ROUTE = re.compile(r"""@(?:[\w.]+\.)?route\(\s*["']([^"']+)["'](?:[^)]*methods\s*=\s*[\[(]([^\])]*)[\])])?""")
SPRING_REQUEST = re.compile(r"""@RequestMapping\s*\(\s*(?:value\s*=\s*|path\s*=\s*)?\{?\s*"([^"]*)"(?:[^)]*method\s*=\s*\{?\s*RequestMethod\.(\w+))?""")
JAXRS_VERB = re.compile(r"@(GET|POST|PUT|PATCH|DELETE)\b")
JAXRS_PATH = re.compile(r"""@Path\(\s*"([^"]*)"\s*\)""")
PHP_ATTR = re.compile(r"""#\[Route\(\s*['"]([^'"]+)['"](?:[^\]]*methods\s*:\s*\[\s*['"](\w+))?""")
CLASS_PREFIX = [
    re.compile(r"""@RequestMapping\s*\(\s*(?:value\s*=\s*|path\s*=\s*)?\{?\s*"([^"]*)\""""),
    re.compile(r"""\[Route\(\s*"([^"]*)"\s*\)\]"""),
    re.compile(r"""@Controller\(\s*['"]([^'"]*)['"]"""),
    re.compile(r"""@Path\(\s*"([^"]*)"\s*\)"""),
    re.compile(r"""APIRouter\([^)]*prefix\s*=\s*["']([^"']+)"""),
]
REGISTRATION = [
    # Express / Koa / Hono / Fastify: app.get("/x", handler)
    re.compile(r"""\b(?:app|router|server|api|routes|r|fastify|hono)\.(get|post|put|patch|delete|all)\(\s*["'`]([^"'`]+)["'`]\s*,\s*(?:[\w.]+\s*,\s*)*([\w.$]+)?"""),
    # Go net/http, chi, gin, echo: r.Get("/x", h) / mux.HandleFunc("GET /x", h)
    re.compile(r"""\b\w+\.(HandleFunc|Handle|GET|POST|PUT|PATCH|DELETE|Get|Post|Put|Patch|Delete)\(\s*"([^"]+)"\s*,\s*([\w.]+)"""),
]
LARAVEL = re.compile(r"""Route::(get|post|put|patch|delete)\(\s*['"]([^'"]+)['"]\s*,\s*\[\s*([\w\\]+)::class\s*,\s*['"](\w+)['"]""")
RAILS = re.compile(r"""^\s*(get|post|put|patch|delete)\s+['"]([^'"]+)['"](?:\s*,\s*to:\s*['"](\w+)#(\w+)['"])?""", re.M)
RAILS_RESOURCES = re.compile(r"""^\s*resources\s+:(\w+)""", re.M)
CLIENT_JS = [
    re.compile(r"""\bfetch\(\s*([`'"])(.+?)\1"""),
    re.compile(r"""\b(?:axios|ky|http|\$http|api|client|this\.http)\.(get|post|put|patch|delete)\s*(?:<[^>()]*>)?\(\s*([`'"])(.+?)\2"""),
]
CLIENT_PY = re.compile(r"""\b(?:requests|httpx|session|client|self\.client)\.(get|post|put|patch|delete)\(\s*f?(["'])(.+?)\2""")
CONST_JS = re.compile(r"""(?m)^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*["'`]([^"'`$]*)["'`]""")
CONST_PY = re.compile(r"""(?m)^([A-Z_][A-Z0-9_]*)\s*=\s*["']([^"']*)["']""")
# Any of the annotation styles above; files without one skip the per-function header checks.
ROUTE_HINT = re.compile(r"""[@#]\[?(?:[\w.]+\.)?(?i:get|post|put|patch|delete|head|options|route)\(|"""
                        r"""@(?:Get|Post|Put|Patch|Delete|Request)Mapping\b|\[Http(?:Get|Post|Put|Patch|Delete)\b|"""
                        r"""@(?:GET|POST|PUT|PATCH|DELETE)\b""")

_JS_LANGS = frozenset(("javascript", "typescript", "vue", "svelte", "astro"))
_JS_ID = r"[A-Za-z_$][\w$]*"
_JS_OWNER = frozenset("app router server api routes r fastify hono".split())
_JS_CREATE = re.compile(r"\b(?:export\s+)?(?:const|let|var)\s+(" + _JS_ID + r")\s*=\s*(" + _JS_ID + r"(?:\.Router)?)\s*\(")
_JS_USE = re.compile(r"\b(" + _JS_ID + r")\.use\s*\(")
_JS_ROUTE = re.compile(r"\b(?P<owner>" + _JS_ID + r")\.(?P<verb>get|post|put|patch|delete|head|options|all)\s*\(\s*(?P<quote>['\"`])(?P<route>[^'\"`]+)(?P=quote)\s*,")
_JS_HANDLER = re.compile(_JS_ID + r"(?:\." + _JS_ID + r")*")


def _source_views(text: str, lang: str) -> tuple:
    """(comments removed, comments and strings masked), with original offsets."""
    if lang == "python":
        clean, code = list(text), list(text)
        starts = [0]
        for line in text.splitlines(keepends=True):
            starts.append(starts[-1] + len(line))
        try:
            for token in tokenize.generate_tokens(io.StringIO(text).readline):
                if token.type not in (tokenize.COMMENT, tokenize.STRING) and not tokenize.tok_name[token.type].startswith("FSTRING"):
                    continue
                a, b = starts[token.start[0] - 1] + token.start[1], starts[token.end[0] - 1] + token.end[1]
                code[a:b] = ["\n" if c == "\n" else " " for c in text[a:b]]
                if token.type == tokenize.COMMENT:
                    clean[a:b] = code[a:b]
        except (tokenize.TokenError, IndentationError, SyntaxError):
            # Never synthesize routes from an unterminated literal or invalid file.
            return text, re.sub(r"[^\n]", " ", text)
        return "".join(clean), "".join(code)
    if lang in ("vue", "svelte", "astro"):
        from .scan import _script_only
        text = _script_only(text)
    if lang in ("javascript", "typescript", "vue", "svelte", "astro"):
        return blank_js(text, True), blank_js(text, False)
    if lang in SPECS:
        # PHP 8 attributes begin with '#['; the generic scanner otherwise treats
        # '#' as a comment. The replacement keeps offsets while protecting them.
        masked = text.replace("#[", "@[") if lang == "php" else text
        clean = blank_generic(masked, SPECS[lang], True)
        code = blank_generic(masked, SPECS[lang], False)
        if lang == "php":
            clean = "".join(original if original == "#" and c == "@" else c for original, c in zip(text, clean))
        return clean, code
    return text, re.sub(r"[^\n]", " ", text)


def _matches(pattern, text: str, code: str):
    """Keep literal arguments, but require the construct itself to be code."""
    for match in pattern.finditer(text):
        start = match.start()
        while start < match.end() and text[start].isspace():
            start += 1
        if start < len(code) and not code[start].isspace():
            yield match


def _norm(method: str, path: str) -> str:
    return f"{method.upper()} {norm_path(path)}"


def _innermost(fi, line: int):
    best = None
    for s in fi.symbols:
        if s.line <= line <= s.end and (best is None or (s.end - s.line) < (best.end - best.line)):
            best = s
    return best


def _header_start(lines: list, sym) -> int:
    start = sym.line - 1
    # Some scanners report the declaration line rather than its decorators.
    while start > 0 and sym.line - start <= 12:
        previous = lines[start - 1].strip()
        if previous and not previous.startswith(("@", "#[", "[")):
            break
        start -= 1
    return start


def _header(lines: list, sym, start=None) -> str:
    """Decorator/annotation lines plus the signature line of a symbol."""
    out = []
    start = sym.line - 1 if start is None else start
    for no in range(start + 1, min(sym.end, sym.line + 12) + 1):
        if no - 1 >= len(lines):
            break
        out.append(lines[no - 1])
        if re.search(rf"\b{re.escape(sym.name.split('.')[-1])}\b\s*[(<:=]", lines[no - 1]) or "def " in lines[no - 1]:
            break
    return "\n".join(out)


def _add_route(sym, route: str) -> None:
    if route not in sym.routes:
        sym.routes.append(route)
        sym.keys.append(f"route:{route}")


def _client_url(raw: str, consts: dict) -> str | None:
    url = re.sub(r"\$\{\s*([A-Za-z_$][\w$.]*)\s*\}", lambda m: consts.get(m.group(1), "{x}"), raw)
    url = re.sub(r"\{\s*([A-Za-z_]\w*)\s*\}", lambda m: consts.get(m.group(1), "{" + m.group(1) + "}"), url)
    url = re.sub(r"^https?://[^/]+", "", url).split("?")[0].split("#")[0]
    if not url.startswith("/") or len(url) > 200:
        return None
    return url


def _js_module(path: str, spec: str, files: dict) -> str | None:
    """Resolve only an explicit relative module with one available candidate."""
    if not spec.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(path), spec))
    if base in files:
        return base
    suffixes = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts")
    if base.endswith((".js", ".jsx", ".mjs", ".cjs")):
        base = posixpath.splitext(base)[0]
    candidates = {name for ext in suffixes for name in (base + ext, base + "/index" + ext) if name in files}
    return next(iter(candidates)) if len(candidates) == 1 else None


def _express_contexts(files: dict, texts: dict) -> tuple:
    """Prove router identities and compose only one explicit mount per router.

    Dynamic factories, aliases and multiple mount sites retain their local route
    path. We do not choose a prefix by the spelling of a filename or a variable.
    """
    contexts = {}
    for path, fi in files.items():
        if fi.lang not in _JS_LANGS or fi.domain != "code" or path not in texts:
            continue
        text, code = _source_views(texts[path], fi.lang)
        factories = {imp.alias: "router" if imp.name == "Router" else "app"
                     for imp in fi.imports if imp.module == "express" and imp.name in (None, "default", "Router")}
        created = defaultdict(list)
        exported = set()
        for m in _matches(_JS_CREATE, text, code):
            owner, factory = m.group(1), m.group(2)
            if factory in factories:
                created[owner].append(factories[factory])
            elif factory.endswith(".Router") and factory[:-7] in factories:
                created[owner].append("router")
            if m.group().startswith("export "):
                exported.add(owner)
        for m in re.finditer(r"\bexport\s*\{([^}]*)\}", code):
            exported.update(part.strip().split(" as ")[0].strip() for part in m.group(1).split(","))
        routers = {owner for owner, kinds in created.items() if kinds == ["router"]}
        contexts[path] = {"text": text, "code": code, "routers": routers, "exported": exported,
                          "receivers": {owner: (path, owner) for owner in _JS_OWNER | set(created)}}

    def router_node(path, alias):
        context = contexts[path]
        if alias in context["routers"]:
            return path, alias
        imports = [imp for imp in files[path].imports if imp.alias == alias]
        if len(imports) != 1:
            return None
        imp = imports[0]
        target = _js_module(path, imp.module, files)
        if target not in contexts:
            return None
        owner = files[target].default_export if imp.name in (None, "default") else imp.name
        if owner in contexts[target]["routers"] and (imp.name in (None, "default") or owner in contexts[target]["exported"]):
            return target, owner
        return None

    for path, context in contexts.items():
        for imp in files[path].imports:
            node = router_node(path, imp.alias)
            if node:
                context["receivers"][imp.alias] = node
    parents, unknown_mounts = defaultdict(set), set()
    for path, context in contexts.items():
        for m in _matches(_JS_USE, context["text"], context["code"]):
            parent = context["receivers"].get(m.group(1))
            end = _match(context["code"], m.end() - 1, "(", ")")
            if not parent or end < 0:
                continue
            args = _js_arguments(context["code"], m.end(), end)
            if not args:
                continue
            a, b = args[-1]
            child = router_node(path, context["code"][a:b].strip())
            if not child:
                continue
            first, last = args[0]
            literal = re.fullmatch(r"(['\"`])(/[^'\"`]*)\1", context["text"][first:last].strip())
            if len(args) == 1:
                parents[child].add((parent, ""))
            elif literal and "${" not in literal.group(2):
                parents[child].add((parent, literal.group(2)))
            else:
                unknown_mounts.add(child)

    def prefix_for(node, seen):
        if node in seen or node in unknown_mounts or len(parents[node]) > 1:
            return None
        if not parents[node]:
            return ""
        parent, prefix = next(iter(parents[node]))
        inherited = prefix_for(parent, seen | {node})
        return inherited.rstrip("/") + "/" + prefix.lstrip("/") if inherited is not None else None

    prefixes = {node: prefix_for(node, set()) for context in contexts.values() for node in context["receivers"].values()}
    return contexts, prefixes


def _js_arguments(code: str, start: int, end: int) -> list:
    """Argument spans in string-masked code; nested commas remain inside a span."""
    spans, stack, begin = [], [], start
    for i in range(start, end):
        ch = code[i]
        if ch in "({[":
            stack.append(ch)
        elif ch in ")}]" and stack:
            stack.pop()
        elif ch == "," and not stack:
            if code[begin:i].strip():
                spans.append((begin, i))
            begin = i + 1
    if code[begin:end].strip():
        spans.append((begin, end))
    return spans


def _apply_js_registrations(path, fi, text, code, contexts, prefixes) -> None:
    context = contexts[path]
    braces, stack = {}, []
    for m in re.finditer(r"[{}]", code):
        if m.group() == "{":
            stack.append(m.start())
        elif stack:
            braces[stack.pop()] = m.start()
    top = defaultdict(list)
    for sym in fi.symbols:
        if sym.parent is None and sym.sub != "route handler":
            top[sym.name].append(sym)
    for m in _matches(_JS_ROUTE, text, code):
        receiver = context["receivers"].get(m.group("owner"))
        route = m.group("route")
        if not receiver or not route.startswith("/") or "${" in route:
            continue
        call_open = code.find("(", m.start(), m.end())
        call_end = _match(code, call_open, "(", ")")
        if call_end < 0:
            continue
        args = _js_arguments(code, m.end(), call_end)
        if not args:
            continue
        start, end = args[-1]
        while start < end and code[start].isspace():
            start += 1
        value = code[start:end].strip()
        handler = value if _JS_HANDLER.fullmatch(value) else None
        callback_start = start + 6 if code.startswith("async ", start) else start
        while callback_start < end and code[callback_start].isspace():
            callback_start += 1
        body = _fn_body(code, callback_start, braces)
        if not handler and not body:
            continue
        prefix = prefixes.get(receiver)
        if prefix:
            route = prefix.rstrip("/") + "/" + route.lstrip("/")
        label = _norm("GET" if m.group("verb") == "all" else m.group("verb"), route)
        line = text.count("\n", 0, m.start()) + 1
        candidates = top.get(handler, []) if handler else []
        target = candidates[0] if len(candidates) == 1 else None
        if target is None:
            target = next((s for s in fi.symbols if s.sub == "route handler" and s.line == line and s.name == label), None)
            if target is None:
                qual = label
                while any(s.id == sym_id("function", path, qual) for s in fi.symbols):
                    qual += "~2"
                target = Sym(id=sym_id("function", path, qual), kind="function", name=label, qual=qual,
                             file=path, line=line, end=text.count("\n", 0, call_end) + 1, sub="route handler",
                             signature=text[m.start():m.end()].strip()[:160], hash=sha1(text[m.start():call_end + 1]))
                fi.symbols.append(target)
            if body:
                target.calls = [(chain, text.count("\n", 0, pos) + 1)
                                for chain, pos in _calls_in(code, body[0], min(body[1] + 1, end), True).items()]
            elif handler:
                target.calls = [(("&",) + tuple(handler.split(".")), line)]
        _add_route(target, label)


def apply_routes(files: dict, texts: dict) -> int:
    """Attach routes to handlers and client references to callers. Returns the number of routes found."""
    found = 0
    classes = defaultdict(list)
    for fi in files.values():
        for s in fi.symbols:
            if s.kind == "class":
                classes[s.name].append(s)
    members = {s.id: {m.name: m for m in fi.symbols if m.parent == s.id} for fi in files.values() for s in fi.symbols if s.kind == "class"}
    js_contexts, js_prefixes = _express_contexts(files, texts)

    for path, fi in files.items():
        text = texts.get(path)
        if not text or fi.domain != "code":
            continue
        text, code = _source_views(text, fi.lang)
        lines = text.splitlines()
        code_lines = code.splitlines()
        syms = {s.id: s for s in fi.symbols}
        # ---- decorators / annotations / attributes
        for s in (fi.symbols if ROUTE_HINT.search(text) else ()):
            if s.kind == "class":
                continue
            start = _header_start(code_lines, s)
            head = _header(lines, s, start)
            code_head = _header(code_lines, s, start)
            prefix = ""
            if s.parent and s.parent in syms:
                cls = syms[s.parent]
                start = _header_start(code_lines, cls)
                cls_head = "\n".join(lines[start:min(cls.line + 8, len(lines))])
                cls_code = "\n".join(code_lines[start:min(cls.line + 8, len(code_lines))])
                for pat in CLASS_PREFIX:
                    pm = next(_matches(pat, cls_head, cls_code), None)
                    if pm:
                        prefix = pm.group(1).replace("[controller]", re.sub(r"Controller$", "", cls.name).lower())
                        break
            for pat in DECORATOR_ROUTES:
                for m in _matches(pat, head, code_head):
                    _add_route(s, _norm(m.group(1), prefix.rstrip("/") + "/" + (m.group(2) or "").lstrip("/")))
            for m in _matches(FLASK_ROUTE, head, code_head):
                methods = re.findall(r"\w+", m.group(2) or "GET")
                for method in methods:
                    _add_route(s, _norm(method, prefix.rstrip("/") + "/" + m.group(1).lstrip("/")))
            for m in _matches(SPRING_REQUEST, head, code_head):
                if s.parent or m.group(2):           # on a method; a class-level mapping is only a prefix
                    _add_route(s, _norm(m.group(2) or "GET", prefix.rstrip("/") + "/" + m.group(1).lstrip("/")))
            verbs = [m.group(1) for m in _matches(JAXRS_VERB, head, code_head)]
            if verbs:
                pm = next(_matches(JAXRS_PATH, head, code_head), None)
                for v in verbs:
                    _add_route(s, _norm(v, prefix.rstrip("/") + "/" + (pm.group(1) if pm else "").lstrip("/")))
            for m in _matches(PHP_ATTR, head, code_head):
                _add_route(s, _norm(m.group(2) or "GET", m.group(1)))
        # ---- route registrations by call
        if fi.lang in _JS_LANGS:
            _apply_js_registrations(path, fi, text, code, js_contexts, js_prefixes)
        top = {s.name: s for s in fi.symbols if s.parent is None}
        names = {s.name: s for s in fi.symbols}
        for pat in (() if fi.lang in _JS_LANGS else REGISTRATION):
            for m in _matches(pat, text, code):
                verb, route, handler = m.group(1), m.group(2), (m.group(3) or "")
                if verb.lower() in ("handlefunc", "handle"):
                    parts = route.split(" ", 1)
                    verb, route = (parts[0], parts[1]) if len(parts) == 2 and parts[0].isupper() else ("GET", route)
                if verb.lower() == "all":
                    verb = "GET"
                if not route.startswith("/"):
                    continue
                line = text.count("\n", 0, m.start()) + 1
                target = names.get(handler.split(".")[-1]) if handler else None
                if target is None:
                    holder = _innermost(fi, line)
                    if holder is None or holder.kind == "class":
                        depth, k = 0, m.start()
                        while k < len(text):
                            if code[k] == "(":
                                depth += 1
                            elif code[k] == ")":
                                depth -= 1
                                if depth == 0:
                                    break
                            k += 1
                        end = text.count("\n", 0, k) + 1
                        label = _norm(verb, route)
                        target = Sym(id=sym_id("function", path, label), kind="function", name=label, qual=label,
                                     file=path, line=line, end=max(line, end), sub="route handler",
                                     signature=lines[line - 1].strip()[:160] if line - 1 < len(lines) else label)
                        fi.symbols.append(target)
                    else:
                        target = holder
                _add_route(target, _norm(verb, route))
        if path.endswith("routes.rb"):
            for m in _matches(RAILS, text, code):
                if m.group(3):
                    ctrl = "".join(p.capitalize() for p in m.group(3).split("_")) + "Controller"
                    for cls in classes.get(ctrl, []):
                        meth = members.get(cls.id, {}).get(m.group(4))
                        if meth:
                            _add_route(meth, _norm(m.group(1), m.group(2)))
            for m in _matches(RAILS_RESOURCES, text, code):
                res = m.group(1)
                ctrl = "".join(p.capitalize() for p in res.split("_")) + "Controller"
                for cls in classes.get(ctrl, []):
                    for action, (verb, suffix) in {"index": ("GET", ""), "show": ("GET", "/{id}"), "create": ("POST", ""),
                                                   "update": ("PATCH", "/{id}"), "destroy": ("DELETE", "/{id}")}.items():
                        meth = members.get(cls.id, {}).get(action)
                        if meth:
                            _add_route(meth, _norm(verb, f"/{res}{suffix}"))
        for m in _matches(LARAVEL, text, code):
            cls_name = m.group(3).split("\\")[-1]
            for cls in classes.get(cls_name, []):
                meth = members.get(cls.id, {}).get(m.group(4))
                if meth:
                    _add_route(meth, _norm(m.group(1), m.group(2)))
        # ---- client calls
        const_pattern = CONST_JS if fi.lang in ("javascript", "typescript", "vue", "svelte", "astro") else CONST_PY
        consts = {m.group(1): m.group(2) for m in _matches(const_pattern, text, code)}
        for pat in (CLIENT_JS if fi.lang in ("javascript", "typescript", "vue", "svelte", "astro") else [CLIENT_PY]):
            for m in _matches(pat, text, code):
                if pat is CLIENT_JS[0]:
                    raw = m.group(2)
                    window = text[m.end():m.end() + 240]
                    window_code = code[m.end():m.end() + 240]
                    end = window_code.find(")")
                    if end >= 0:
                        window, window_code = window[:end], window_code[:end]
                    vm = next(_matches(re.compile(r"""method\s*:\s*["'](\w+)["']"""), window, window_code), None)
                    verb = vm.group(1) if vm else "GET"
                else:
                    verb, raw = m.group(1), m.group(3)
                url = _client_url(raw, consts)
                if not url:
                    continue
                line = text.count("\n", 0, m.start()) + 1
                holder = _innermost(fi, line)
                if holder is None:
                    continue
                route = _norm(verb, url)
                for key, conf in ((f"api:{route}", 0.8), (f"route:{route}", 0.7)):
                    if (key, conf) not in holder.refs:
                        holder.refs.append((key, conf))
        found += sum(len(s.routes) for s in fi.symbols)
    return found
