"""Python parser built on the standard library's `ast` module: exact and dependency-free."""
from __future__ import annotations

import ast
import symtable

from .model import FileInfo, Imp, Sym, dedupe_ids, sym_id
from .util import first_sentence, sha1


def _chain(expr) -> tuple:
    """`self.store.save` -> ("self", "store", "save"); unknown receivers become "?"."""
    if isinstance(expr, ast.Subscript):
        return _chain(expr.value) + ("[]",)
    parts = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if isinstance(expr, ast.Subscript):
        return _chain(expr.value) + ("[]",) + tuple(reversed(parts))
    if isinstance(expr, ast.Name):
        parts.append(expr.id)
    elif isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "super":
        parts.append("super()")
    else:
        parts.append("?")
    return tuple(reversed(parts))


def _child_scope(table, node, name=None):
    if table is None:
        return None
    name = name or node.name
    found = next((s for s in table.get_children()
                  if s.get_name() == name and s.get_lineno() == node.lineno), None)
    # Python 3.12+ wraps generic declarations in a type-parameter scope.
    while found is not None and found.get_type() not in ("function", "class"):
        found = next((s for s in found.get_children()
                      if s.get_name() == name and s.get_lineno() == node.lineno), None)
    return found


def _scope_nodes(body):
    """Nodes executed in this lexical scope, excluding nested declaration bodies."""
    for node in body:
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield from _scope_nodes(ast.iter_child_nodes(node))


def _definitions(body, conditional=False):
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield node, conditional
        elif not isinstance(node, ast.Lambda):
            yield from _definitions(ast.iter_child_nodes(node), True)


def _imports(body) -> list:
    out = []
    for node in _scope_nodes(body):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(Imp(alias.asname or alias.name, alias.name))
                if not alias.asname and "." in alias.name:
                    out.append(Imp(alias.name.split(".")[0], alias.name.split(".")[0]))
        elif isinstance(node, ast.ImportFrom):
            out.extend(Imp(a.asname or a.name, node.module or "", a.name, node.level or 0) for a in node.names)
    return out


def _assignment_names(body) -> set:
    names = {n.id for n in _scope_nodes(body) if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))}
    names.update(n.name for n in _scope_nodes(body) if isinstance(n, ast.ExceptHandler) and n.name)
    return names


def _stable_definitions(body) -> set:
    counts = {}
    for node, _ in _definitions(body):
        counts[node.name] = counts.get(node.name, 0) + 1
    rebound = _assignment_names(body) | {i.alias.split(".")[0] for i in _imports(body)}
    return {name for name, count in counts.items() if count == 1 and name not in rebound}


def _unknown_locals(body, scope) -> list:
    """Known declarations/imports are resolved separately; other locals block guessing."""
    if scope is None:
        return []
    imports = {}
    for imp in _imports(body):
        if "." in imp.alias:  # the separately recorded root is the actual local binding
            continue
        head = imp.alias.split(".")[0]
        imports.setdefault(head, set()).add((imp.module, imp.name, imp.level))
    declarations = {n.name for n, _ in _definitions(body)}
    stable_imports = {name for name, targets in imports.items() if len(targets) == 1} - _assignment_names(body) - declarations
    known = _stable_definitions(body) | stable_imports
    return sorted(s.get_name() for s in scope.get_symbols() if s.is_local() and s.get_name() not in known)


def _local_types(node) -> dict:
    """Only a single, unconditional constructor assignment gives a local a type."""
    nodes = list(_scope_nodes(node.body))
    writes: dict = {}
    changed_objects = set()
    for sub in nodes:
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
            writes[sub.id] = writes.get(sub.id, 0) + 1
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            writes[sub.name] = writes.get(sub.name, 0) + 1
        elif isinstance(sub, ast.Attribute) and isinstance(sub.ctx, (ast.Store, ast.Del)):
            changed_objects.add(_chain(sub)[0])
    params = {a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs}
    params.update(a.arg for a in (node.args.vararg, node.args.kwarg) if a)
    changed_objects.update(name for sub in ast.walk(node) if isinstance(sub, ast.Nonlocal) for name in sub.names)
    declarations = _stable_definitions(node.body)
    result = {}
    for sub in node.body:
        targets, value = (sub.targets, sub.value) if isinstance(sub, ast.Assign) else (
            ([sub.target], sub.value) if isinstance(sub, ast.AnnAssign) else ([], None))
        if len(targets) != 1 or not isinstance(targets[0], ast.Name) or not isinstance(value, ast.Call):
            continue
        name = targets[0].id
        chain = _chain(value.func)
        constructor_bound = chain[0] in params or (chain[0] in writes and chain[0] not in declarations)
        if writes.get(name) == 1 and name not in params | changed_objects and "?" not in chain and not constructor_bound:
            result[name] = (chain, sub.lineno)
    return result


def _calls(node, scope=None, receiver="", enclosing=None, known_names=(), local_types=None, local_imports=()) -> list:
    """Calls belong to their named scope; local bindings require explicit evidence."""
    seen: dict = {}
    local_types = local_types or {}
    imported = {imp.alias.split(".")[0] for imp in local_imports} - _assignment_names(node.body if hasattr(node, "body") and isinstance(node.body, list) else [])

    def remember(chain, line, table, bound, current_receiver):
        head = chain[1] if chain[0] == "&" else chain[0]
        if head in bound:
            return
        if head != current_receiver and table is not None:
            try:
                symbol = table.lookup(head)
                typed = head in local_types and line > local_types[head][1] and len(chain) > (2 if chain[0] == "&" else 1)
                declared = head in known_names and (symbol.is_namespace() or symbol.is_free())
                if not symbol.is_global() and not typed and not declared and not (symbol.is_imported() and head in imported):
                    return
            except KeyError:
                pass
        if chain not in seen:
            seen[chain] = line

    def visit(sub, table, bound=frozenset(), current_receiver=""):
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            if sub is not node:
                # Definition-time expressions execute here, but its body belongs
                # to the separately extracted declaration (or an unnamed lambda).
                defaults = list(sub.args.defaults) + [d for d in sub.args.kw_defaults if d is not None]
                for item in list(getattr(sub, "decorator_list", [])) + defaults:
                    visit(item, table, bound, current_receiver)
                return
            name = sub.name if not isinstance(sub, ast.Lambda) else "lambda"
            inner = scope if sub is node else _child_scope(table, sub, name)
            inherited = receiver if sub is node else current_receiver
            if inherited and sub is not node and inner is not None:
                try:
                    symbol = inner.lookup(inherited)
                    if symbol.is_local() or symbol.is_assigned() or symbol.is_imported():
                        inherited = ""
                except KeyError:
                    inherited = ""
            body = sub.body if not isinstance(sub, ast.Lambda) else [sub.body]
            for item in body:
                visit(item, inner, frozenset(), inherited)
            return
        if isinstance(sub, ast.ClassDef):
            if sub is node:
                for item in sub.body:
                    visit(item, scope)
            else:
                for item in sub.decorator_list + sub.bases + [k.value for k in sub.keywords]:
                    visit(item, table, bound, current_receiver)
            return
        if isinstance(sub, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            name = {ast.ListComp: "listcomp", ast.SetComp: "setcomp", ast.DictComp: "dictcomp",
                    ast.GeneratorExp: "genexpr"}[type(sub)]
            inner = _child_scope(table, sub, name) or table
            local = set(bound)
            for i, gen in enumerate(sub.generators):
                visit(gen.iter, table if i == 0 else inner, local, current_receiver)
                local.update(n.id for n in ast.walk(gen.target) if isinstance(n, ast.Name))
                for condition in gen.ifs:
                    visit(condition, inner, local, current_receiver)
            for item in ([sub.key, sub.value] if isinstance(sub, ast.DictComp) else [sub.elt]):
                visit(item, inner, local, current_receiver)
            return
        if isinstance(sub, ast.Call):
            chain = _chain(sub.func)
            remember(chain, sub.lineno, table, bound, current_receiver)
            for arg in list(sub.args) + [k.value for k in sub.keywords]:
                if isinstance(arg, (ast.Name, ast.Attribute)):
                    ref = ("&",) + _chain(arg)
                    if "?" not in ref:
                        remember(ref, sub.lineno, table, bound, current_receiver)
        for item in ast.iter_child_nodes(sub):
            visit(item, table, bound, current_receiver)

    visit(node, scope, current_receiver=receiver)
    return list(seen.items())


def _unparse(node, fallback: str = "...") -> str:
    try:
        return ast.unparse(node)
    except Exception:  # very old or exotic syntax
        return fallback


def _signature(node) -> str:
    if isinstance(node, ast.ClassDef):
        bases = [_unparse(b) for b in node.bases] + [_unparse(k) for k in node.keywords]
        sig = f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
    else:
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        sig = f"{prefix} {node.name}({_unparse(node.args)})"
        if node.returns is not None:
            sig += f" -> {_unparse(node.returns)}"
    return sig if len(sig) <= 160 else sig[:157] + "..."


def _doc(node) -> str:
    try:
        doc = ast.get_docstring(node)
    except Exception:
        doc = None
    return first_sentence(doc.split("\n\n")[0]) if doc else ""


def _decorators(node) -> list:
    out = []
    for d in node.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        out.append(".".join(_chain(target)))
    return out


def _start(node) -> int:
    return min([node.lineno] + [d.lineno for d in node.decorator_list])


def _span_hash(lines: list, start: int, end: int) -> str:
    return sha1("\n".join(ln.rstrip() for ln in lines[start - 1:end]))


_WRAPPERS = frozenset("Optional Union List list Dict dict Set set Tuple tuple Type type Sequence Iterable "
                      "Mapping ClassVar Final Annotated None".split())


def _type_chain(annotation):
    """`JsonStore`, `Optional[JsonStore]`, `JsonStore | None`, `"JsonStore"` -> ("JsonStore",)."""
    if annotation is None:
        return None
    if isinstance(annotation, ast.Subscript) and _chain(annotation.value)[-1] in (
            "list", "List", "dict", "Dict", "set", "Set", "tuple", "Tuple", "Sequence", "Iterable", "Mapping"):
        return None                         # a collection is not an instance of its element type
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        name = annotation.value.split("[")[0].split("|")[0].strip()
        return tuple(name.split(".")) if name.replace(".", "").isidentifier() else None
    for sub in ast.walk(annotation):
        if isinstance(sub, (ast.Name, ast.Attribute)):
            chain = _chain(sub)
            if "?" not in chain and chain[-1] not in _WRAPPERS:
                return chain
    return None


def _attr_types(cls_node) -> dict:
    """Infer `self.x` types from `self.x = Foo(...)`, `self.x = param: Foo` and `x: Foo` fields."""
    attrs: dict = {}
    for item in cls_node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            chain = _type_chain(item.annotation)
            if chain:
                attrs[item.target.id] = chain
    for meth, _conditional in _definitions(cls_node.body):
        if not isinstance(meth, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = {a.arg: _type_chain(a.annotation) for a in meth.args.args + meth.args.kwonlyargs}
        for node in _scope_nodes(meth.body):
            targets, value, annotation = [], None, None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign):
                targets, value, annotation = [node.target], node.value, node.annotation
            for t in targets:
                if not (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"):
                    continue
                chain = _type_chain(annotation)
                if not chain and isinstance(value, ast.Call):
                    chain = _chain(value.func)
                if not chain and isinstance(value, ast.Name):
                    chain = params.get(value.id)
                if chain and "?" not in chain:
                    attrs.setdefault(t.attr, chain)
    return attrs


def _container_types(cls_node) -> dict:
    """Homogeneous list fields: explicit literals/constructor insertions only.

    Unknown writes, mutators or whole-list escapes disable the inference. An
    annotation alone is insufficient evidence for an indexed receiver type.
    """
    states = {}
    methods = [n for n, _ in _definitions(cls_node.body) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def field(node):
        return node.attr if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self" else None

    for method in methods:
        nodes = list(_scope_nodes(method.body))
        parents = {id(c): n for n in nodes for c in ast.iter_child_nodes(n)}
        locals_ = {a.arg for a in method.args.posonlyargs + method.args.args + method.args.kwonlyargs}
        locals_.update(n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)))
        locals_.update(imp.alias.split(".")[0] for imp in _imports(method.body))

        def add(state, value):
            if not isinstance(value, ast.Call):
                state["invalid"] = True
                return
            chain = _chain(value.func)
            if "?" in chain or chain[0] in locals_:
                state["invalid"] = True
            else:
                state["types"].add(chain)

        for node in nodes:
            name = field(node)
            if not name:
                continue
            state = states.setdefault(name, {"initial": False, "invalid": False, "types": set()})
            parent = parents.get(id(node))
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                if isinstance(node.ctx, ast.Del):
                    state["invalid"] = True
                    continue
                value = parent.value if isinstance(parent, (ast.Assign, ast.AnnAssign)) else None
                if isinstance(value, (ast.List, ast.Tuple)):
                    state["initial"] = True
                    for item in value.elts:
                        add(state, item)
                else:
                    state["invalid"] = True
            elif isinstance(parent, ast.Subscript) and parent.value is node:
                if isinstance(parent.ctx, ast.Store):
                    assignment = parents.get(id(parent))
                    add(state, assignment.value if isinstance(assignment, (ast.Assign, ast.AnnAssign)) else None)
            elif isinstance(parent, ast.Attribute) and parent.value is node:
                call = parents.get(id(parent))
                if not isinstance(call, ast.Call) or call.func is not parent:
                    state["invalid"] = True
                elif parent.attr == "append" and len(call.args) == 1:
                    add(state, call.args[0])
                elif parent.attr == "insert" and len(call.args) == 2:
                    add(state, call.args[1])
                elif parent.attr == "extend" and len(call.args) == 1 and isinstance(call.args[0], (ast.List, ast.Tuple)):
                    for item in call.args[0].elts:
                        add(state, item)
                elif parent.attr not in ("clear", "pop", "remove", "reverse", "sort", "copy", "count", "index"):
                    state["invalid"] = True
            elif not (isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) and parent.func.id in ("len", "bool")):
                state["invalid"] = True
    return {name + "[]": next(iter(s["types"])) for name, s in states.items()
            if s["initial"] and not s["invalid"] and len(s["types"]) == 1}


def _is_main_guard(node) -> bool:
    """`if __name__ == "__main__":`"""
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    parts = [node.test.left] + list(node.test.comparators)
    names = {p.id for p in parts if isinstance(p, ast.Name)}
    consts = {p.value for p in parts if isinstance(p, ast.Constant)}
    return "__name__" in names and "__main__" in consts


def _receiver(node, scope, cls, inherited="") -> str:
    receiver = ""
    positional = node.args.posonlyargs + node.args.args
    if cls and scope and positional and "staticmethod" not in _decorators(node):
        name = positional[0].arg
        symbol = scope.lookup(name)
        if name in ("self", "cls") and not symbol.is_assigned() and not symbol.is_imported():
            receiver = name
    elif inherited and scope:
        try:
            symbol = scope.lookup(inherited)
            if symbol.is_free() and not symbol.is_assigned():
                receiver = inherited
        except KeyError:
            pass
    return receiver


def _function(path: str, node, lines: list, cls, scope=None, enclosing=None, qual=None) -> Sym:
    qual = qual or (f"{cls.qual}.{node.name}" if cls else node.name)
    start, end = _start(node), node.end_lineno or node.lineno
    return Sym(
        id=sym_id("function", path, qual),
        kind="method" if cls else "function",
        name=node.name,
        qual=qual,
        file=path,
        line=start,
        end=end,
        signature=_signature(node),
        doc=_doc(node),
        parent=cls.id if cls else None,
        local_types=_local_types(node),
        local_imports=_imports(node.body),
        local_names=_unknown_locals(node.body, scope),
        decorators=_decorators(node),
        exported=not node.name.startswith("_"),
        hash=_span_hash(lines, start, end),
    )


def parse_python(path: str, text: str) -> FileInfo:
    fi = FileInfo(path=path, lang="python")
    lines = text.splitlines()
    fi.loc = sum(1 for ln in lines if ln.strip())
    try:
        tree = ast.parse(text)
        scopes = symtable.symtable(text, path, "exec")
    except (SyntaxError, ValueError) as exc:
        fi.error = f"syntax error: {getattr(exc, 'msg', exc)} (line {getattr(exc, 'lineno', '?')})"
        return fi
    fi.doc = _doc(tree)
    defined = {n.name for n, _ in _definitions(tree.body)}
    fi.assigned_names = sorted(s.get_name() for s in scopes.get_symbols()
                               if s.is_assigned() and s.get_name() not in defined)
    fi.assigned_names = sorted(set(fi.assigned_names) | _assignment_names(tree.body) |
                               (defined & {i.alias.split(".")[0] for i in _imports(tree.body)}))

    fi.imports = _imports(tree.body)
    records = []
    counts = {}

    def extract(body, table, owner=None, inherited_receiver="", visible=frozenset()):
        definitions = list(_definitions(body))
        local_names = _stable_definitions(body)
        # A class namespace does not act as an enclosing lexical scope for methods.
        known = visible | local_names
        for node, conditional in definitions:
            qual = (owner.qual + "." if owner else "") + node.name
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            identifier = sym_id(kind, path, qual)
            counts[identifier] = counts.get(identifier, 0) + 1
            if counts[identifier] > 1:
                qual += f"~{counts[identifier]}"
            scope = _child_scope(table, node)
            direct_class = owner if owner and owner.kind == "class" else None
            if kind == "class":
                start, end = _start(node), node.end_lineno or node.lineno
                symbol = Sym(id=sym_id(kind, path, qual), kind=kind, name=node.name, qual=qual, file=path,
                             line=start, end=end, signature=_signature(node), doc=_doc(node),
                             bases=[".".join(_chain(b)) for b in node.bases], decorators=_decorators(node),
                             attrs=_attr_types(node), local_imports=_imports(node.body),
                             hash=_span_hash(lines, start, end))
                symbol.attrs.update(_container_types(node))
                symbol.local_names = _unknown_locals(node.body, scope)
                receiver = ""
            else:
                symbol = _function(path, node, lines, direct_class, scope, table, qual)
                receiver = _receiver(node, scope, direct_class, inherited_receiver)
            symbol.lexical_parent = owner.id if owner else None
            symbol.exported = owner is None and not node.name.startswith("_")
            symbol.declared_globals = [s.get_name() for s in scope.get_symbols() if s.is_declared_global()] if scope else []
            fi.symbols.append(symbol)
            scope_known = (known | _stable_definitions(node.body)) - _assignment_names(node.body)
            records.append((symbol, node, scope, table, receiver, scope_known))
            child_visible = visible if kind == "class" else scope_known
            extract(node.body, scope, symbol, receiver, child_visible)

    extract(tree.body, scopes)
    for symbol, node, scope, enclosing, receiver, known in records:
        symbol.calls = _calls(node, scope, receiver, enclosing, known,
                              symbol.local_types, symbol.local_imports)
    for node in tree.body:
        if _is_main_guard(node):
            fi.main_calls.extend(chain for chain, _ in _calls(node))
    return dedupe_ids(fi)
