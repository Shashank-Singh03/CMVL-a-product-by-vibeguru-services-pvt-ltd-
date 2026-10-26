"""Parser output: one FileInfo per file, holding its imports, symbols and references.

Every parser (Python, JS/TS, the generic engine for ~20 languages, and the infra/API/data
parsers) produces the same shape, so everything downstream is language-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Imp:
    """One imported binding: `alias` is the local name the file uses."""
    alias: str
    module: str               # as written: "a.b", "./x", "react", "com.acme.Store", "github.com/x/store"
    name: str | None = None   # imported symbol; None = the whole module; "default" (JS); "*"
    level: int = 0            # Python relative-import level
    kind: str = ""            # generic engine: "type" | "namespace" | "file" | "package" | "module"


@dataclass
class Sym:
    """A unit of structure: a class-like container ("class") or a leaf ("function"/"method").

    `sub` says what it really is, e.g. interface, struct, endpoint, resource, job, table, field.
    """
    id: str                   # "c:path#Cls", "f:path#func", "f:path#Cls.meth"
    kind: str                 # "class" | "function" | "method"
    name: str
    qual: str                 # name qualified inside the file: "Cls.meth"
    file: str                 # repo-relative POSIX path
    line: int
    end: int
    signature: str = ""
    doc: str = ""
    parent: str | None = None          # class id for methods
    sub: str = ""                      # display kind ("interface", "resource", "job", "endpoint", ...)
    bases: list = field(default_factory=list)       # raw base-class expressions
    calls: list = field(default_factory=list)       # [(chain tuple, line)]
    local_names: list = field(default_factory=list) # Python lexical locals/free variables: never guess as globals
    decorators: list = field(default_factory=list)
    attrs: dict = field(default_factory=dict)       # classes: attribute -> type chain ("store" -> ("JsonStore",))
    keys: list = field(default_factory=list)        # global names others can reference ("tf:infra:aws_s3_bucket.logs")
    refs: list = field(default_factory=list)        # [(key, confidence)] explicit references to other symbols' keys
    routes: list = field(default_factory=list)      # ["GET /notes/{}"] for HTTP handlers / API operations
    exported: bool = False
    hash: str = ""
    lexical_parent: str | None = None     # enclosing named declaration; distinct from class membership
    local_types: dict = field(default_factory=dict)  # local -> (constructor chain, assignment line)
    local_imports: list = field(default_factory=list)  # imports bound only inside this callable
    declared_globals: list = field(default_factory=list)  # Python global declarations bypass enclosing locals
    provenance: dict = field(default_factory=dict)   # source-backed model review, when applicable
    # filled in later
    status: str = "same"      # same | added | modified | deleted
    add: int = 0
    dele: int = 0
    diff: list = field(default_factory=list)        # ["+line", "-line", " line"]
    flags: list = field(default_factory=list)       # risk flags (risks.py)


@dataclass
class FileInfo:
    path: str
    lang: str                 # format id: "python", "java", "terraform", "kubernetes", "openapi", "text", ...
    domain: str = "code"      # code | api | data | infra | ci | deps | ai | config | docs | other
    loc: int = 0
    doc: str = ""
    source_hash: str = ""        # hash of the complete file, including changes outside parsed symbols
    assigned_names: list = field(default_factory=list) # Python module assignments without a mapped definition
    imports: list = field(default_factory=list)
    reexports: list = field(default_factory=list)   # JS: [(exported name | "*", spec, original name)]
    default_export: str | None = None
    package: str = ""         # package / namespace declared by the file (Java, C#, Go dir, PHP, Kotlin, ...)
    symbols: list = field(default_factory=list)
    main_calls: list = field(default_factory=list)  # call chains run at import time (entry hints)
    error: str | None = None
    # filled in later
    status: str = "same"
    add: int = 0
    dele: int = 0
    module_add: int = 0       # changed lines outside every symbol (imports, constants)
    module_del: int = 0
    module_diff: list = field(default_factory=list)
    flags: list = field(default_factory=list)       # risk flags on lines outside every symbol


def sym_id(kind: str, path: str, qual: str) -> str:
    return f"{'c' if kind == 'class' else 'f'}:{path}#{qual}"


def dedupe_ids(fi: FileInfo) -> FileInfo:
    """Getter/setter pairs and redefinitions share a name; give the later ones `name~2`, `name~3`."""
    seen: dict = {}
    renamed: dict = {}
    for s in fi.symbols:
        if s.parent in renamed:
            s.parent = renamed[s.parent]
            s.qual = s.parent.split("#", 1)[1] + "." + s.qual.rsplit(".", 1)[-1]
            s.id = sym_id(s.kind, s.file, s.qual)
        count = seen.get(s.id, 0) + 1
        seen[s.id] = count
        if count > 1:
            old = s.id
            s.qual = f"{s.qual}~{count}"
            s.id = sym_id(s.kind, s.file, s.qual)
            if s.kind == "class":
                renamed[old] = s.id
    return fi
