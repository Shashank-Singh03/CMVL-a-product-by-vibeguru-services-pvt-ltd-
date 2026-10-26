"""Generic code engine for ~20 languages (zero dependencies).

One scanner, configured per language by a `Spec`:
1. blank comments and string contents (offsets and line numbers survive),
2. find containers (class, interface, struct, trait, impl, object, ...) and callables
   (functions, methods, constructors) with brace matching, or with `end` keywords for
   Ruby / Lua / Elixir,
3. extract imports, call chains, field types (for `this.repo.find()` resolution),
   annotations (for HTTP routes) and doc comments.

It is approximate by design: good enough to map structure and change, never invents
edges, and every downstream stage is the same as for Python/JS.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

from .model import FileInfo, Imp, Sym, dedupe_ids, sym_id
from .util import first_sentence, sha1


@dataclass
class Spec:
    name: str
    family: str
    engine: str = "brace"                 # "brace" | "end"
    line_comments: tuple = ("//",)
    block_comments: tuple = (("/*", "*/"),)
    nested_comments: bool = False
    hash_needs_space: bool = False        # shell: '#' starts a comment only at a word boundary
    preprocessor: bool = False            # C/C++/C#: '#' lines are directives
    dq_multiline: bool = False
    sq: str = "char"                      # "char" | "string" | "string-ml" | "none"
    sq_escape: bool = True
    triple: tuple = ()
    backtick_raw: bool = False
    special: tuple = ()                   # regexes matching whole raw-string literals
    heredoc: bool = False
    semicolons: bool = True               # declarations end at ';' (else at end of line)
    containers: tuple = ()                # (regex with (?P<name>) [(?P<sub>)] [(?P<bases>)], default sub)
    namespaces: tuple = ()                # transparent blocks: namespace X {, companion object {, mod x {
    func_re: str = ""                     # keyword-introduced callables, (?P<name>) etc.
    typed_funcs: bool = False             # C-style "<type> name(" definitions
    paren_optional: bool = False
    expr_body: tuple = ()                 # "=>" (C#, Dart), "=" (Kotlin, Scala)
    self_names: tuple = ("this",)
    implicit_self: bool = True
    new_kw: bool = True
    imports: tuple = ()                   # (regex, kind) applied to code with strings kept
    package_re: str = ""
    field_res: tuple = ()                 # (?P<type>) (?P<name>) at class-body depth
    ctor_names: tuple = ()                # constructor names besides the class name
    ctor_param_re: str = ""               # params that become fields (Kotlin val, PHP promotion, TS private)
    member_ops: tuple = ("->", "::")
    keywords: frozenset = frozenset()
    global_ns: bool = False               # one global namespace for functions (C, shell, R, Lua)
    commands: bool = False                # shell-like: calls are command words
    interface_subs: tuple = ("interface", "trait", "protocol", "@interface")


MERGE_SUBS = ("impl", "extension", "methods")

# Words that look like `name(` at the start of a statement but never name a C-style definition.
TYPED_EXCLUDE = frozenset("""if for foreach while switch catch return sizeof typeof else do try throw new using lock
synchronized assert case await yield nameof checked unchecked fixed defined alignof decltype static_assert elif
when super this base until unless""".split())

COMMON_KW = frozenset("""if else for foreach while do switch case default break continue return throw throws try catch
finally new delete sizeof typeof instanceof in is as await yield async function fn func def class struct enum
interface trait impl where match when using lock synchronized assert super this self static void var let val
const import package namespace operator goto unsafe defer go select chan map range type print println echo
not and or elif elsif unless until then begin end module rescue ensure lambda proc loop sizeof alignof""".split())

_TYPE_ARGS = re.compile(r"<[^<>]*(?:<[^<>]*>[^<>]*)*>")


def _containers(*names: str, mods: str = "", tail: str = r"(?P<bases>[^{;]*)") -> str:
    m = rf"(?:(?:{mods})\s+)*" if mods else ""
    return rf"(?:^|(?<=[\s;{{}}]))(?:@\w+(?:\([^()\n]*\))?\s+)*{m}(?P<sub>{'|'.join(names)})\s+(?P<name>[A-Za-z_]\w*){tail}"


SPECS: dict = {}


def _spec(*langs: str, **kw) -> Spec:
    spec = Spec(name=langs[0], **kw)
    for lang in langs:
        SPECS[lang] = spec
    return spec


JAVA_MODS = "public|protected|private|abstract|static|final|sealed|non-sealed|strictfp"
_spec("java", "groovy", family="jvm", sq="char", triple=('"""',),
      containers=((_containers("class", "interface", "enum", "record", "@interface", mods=JAVA_MODS), "class"),),
      typed_funcs=True,
      func_re=r"(?:^|(?<=[\s;{}]))def\s+(?P<name>\w+)\s*\(",
      imports=((r"^\s*import\s+(?:static\s+)?(?P<module>[\w.]+?)(?P<star>\.\*)?\s*;?\s*$", "type"),),
      package_re=r"^\s*package\s+([\w.]+)",
      field_res=(r"^[ \t]*(?:(?:private|protected|public|static|final|transient|volatile)\s+)*(?P<type>[A-Z]\w*)(?:<[^;=\n]*?>)?\s+(?P<name>\w+)\s*[;=]",),
      keywords=COMMON_KW)

_spec("kotlin", family="jvm", sq="char", triple=('"""',), nested_comments=True, semicolons=False,
      containers=((_containers("class", "interface", "object",
                               mods="public|private|protected|internal|abstract|open|final|sealed|data|enum|annotation|inner|value|inline|expect|actual|fun",
                               tail=r"(?P<bases>[^{\n]*)"), "class"),),
      namespaces=(r"(?:^|(?<=[\s;{}]))companion\s+object(?:\s+\w+)?\s*(?::[^{\n]*)?(?=\{)",),
      func_re=r"(?:^|(?<=[\s;{}]))(?:(?:public|private|protected|internal|override|open|final|abstract|suspend|inline|operator|infix|tailrec|external|actual|expect)\s+)*fun\s+(?:<[^>]*>\s*)?(?:[\w.<>?]+\.)?(?P<name>\w+)\s*\(",
      expr_body=("=",), new_kw=False,
      imports=((r"^\s*import\s+(?P<module>[\w.]+?)(?P<star>\.\*)?(?:\s+as\s+(?P<alias>\w+))?\s*$", "type"),),
      package_re=r"^\s*package\s+([\w.]+)",
      field_res=(r"^[ \t]*(?:(?:private|protected|public|internal|override|lateinit|open|final)\s+)*va[lr]\s+(?P<name>\w+)\s*:\s*(?P<type>[A-Z]\w*)",),
      ctor_param_re=r"(?:val|var)\s+(?P<name>\w+)\s*:\s*(?P<type>[A-Z]\w*)",
      keywords=COMMON_KW)

_spec("scala", family="jvm", sq="char", triple=('"""',), nested_comments=True, semicolons=False,
      containers=((_containers("class", "object", "trait", "enum",
                               mods="abstract|final|sealed|case|implicit|private|protected|open",
                               tail=r"(?P<bases>[^{\n]*)"), "class"),),
      func_re=r"(?:^|(?<=[\s;{}]))(?:(?:override|private|protected|final|implicit|inline|lazy)\s+)*def\s+(?P<name>[\w$]+)",
      paren_optional=True, expr_body=("=",), new_kw=True,
      imports=((r"^\s*import\s+(?P<module>[\w.]+?)(?:\.(?P<star>_|\*))?\s*$", "type"),),
      package_re=r"^\s*package\s+([\w.]+)",
      ctor_param_re=r"(?:val|var)?\s*(?P<name>\w+)\s*:\s*(?P<type>[A-Z]\w*)",
      keywords=COMMON_KW)

_spec("csharp", family="dotnet", sq="char", preprocessor=True,
      special=(r'\$?@"(?:[^"]|"")*"', r'\$?"""[\s\S]*?"""'),
      containers=((_containers("class", "interface", "struct", "record", "enum",
                               mods="public|private|protected|internal|abstract|sealed|static|partial|readonly|ref|unsafe|new|file"), "class"),),
      namespaces=(r"(?:^|(?<=[\s;{}]))namespace\s+[\w.]+\s*(?=\{)",),
      typed_funcs=True, expr_body=("=>",),
      imports=((r"^\s*(?:global\s+)?using\s+(?:static\s+)?(?:(?P<alias>\w+)\s*=\s*)?(?P<module>[\w.]+)\s*;", "namespace"),),
      package_re=r"^\s*namespace\s+([\w.]+)",
      field_res=(r"^[ \t]*(?:(?:private|protected|public|internal|static|readonly|const|volatile|required)\s+)*(?P<type>[A-Z]\w*)(?:<[^;=\n]*?>)?\??\s+(?P<name>_?\w+)\s*(?:[;=]|\{\s*get)",),
      keywords=COMMON_KW | {"get", "set", "init", "nameof", "checked", "unchecked", "fixed", "stackalloc"})

_spec("go", family="go", sq="char", backtick_raw=True, semicolons=False,
      containers=((r"(?:^|(?<=\n))[ \t]*(?:type\s+)?(?P<name>[A-Za-z_]\w*)\s+(?:\[[^\]\n]*\]\s*)?(?P<sub>struct|interface)\s*(?=\{)", "struct"),),
      func_re=r"(?m)^func\s+(?:\((?P<recv>[^)]*)\)\s*)?(?P<name>\w+)\s*(?:\[[^\]]*\])?\s*\(",
      self_names=(), implicit_self=False, new_kw=False,
      package_re=r"^\s*package\s+(\w+)",
      field_res=(r"^[ \t]*(?P<name>[a-zA-Z_]\w*)\s+\*?(?:\[\])?(?:\w+\.)?(?P<type>[A-Z]\w*)\b",),
      member_ops=(), keywords=COMMON_KW - {"type"} | {"type"})

_spec("rust", family="rust", sq="char", nested_comments=True, dq_multiline=True,
      special=(r'r(?P<rh>#*)"[\s\S]*?"(?P=rh)', r'b"(?:\\.|[^"\\])*"'),
      containers=((_containers("struct", "enum", "trait", "union", mods=r"pub(?:\([^)]*\))?|unsafe|auto"), "struct"),
                  (r"(?:^|(?<=[\s;{}]))(?:unsafe\s+)?(?P<sub>impl)(?:\s*<[^{]*?>)?\s+(?P<name>[^{]+?)(?=\s*\{)", "impl")),
      namespaces=(r"(?:^|(?<=[\s;{}]))(?:pub(?:\([^)]*\))?\s+)?mod\s+\w+\s*(?=\{)",),
      func_re=r"(?:^|(?<=[\s;{}]))(?:pub(?:\([^)]*\))?\s+)?(?:(?:const|async|unsafe|extern\s+\"[^\"]*\")\s+)*fn\s+(?P<name>\w+)\s*(?:<[^(]*?>)?\s*\(",
      self_names=("self", "Self"), implicit_self=False, new_kw=False,
      imports=((r"^\s*(?:pub\s+)?use\s+(?P<module>[\w:]+?)(?:::\{(?P<names>[^}]*)\})?(?:\s+as\s+(?P<alias>\w+))?\s*;", "rust-use"),
               (r"^\s*(?:pub\s+)?mod\s+(?P<module>\w+)\s*;", "file")),
      field_res=(r"^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(?P<name>[a-z_]\w*)\s*:\s*(?:&(?:'\w+\s+)?(?:mut\s+)?)?(?:(?:Box|Arc|Rc|Option|RefCell|Mutex)<)*(?P<type>[A-Z]\w*)",),
      member_ops=("::",), keywords=COMMON_KW)

_spec("c", "cpp", family="c", sq="char", preprocessor=True,
      special=(r'R"(?P<cd>[^(\s]*)\([\s\S]*?\)(?P=cd)"',),
      containers=((r"(?:^|(?<=[\s;{}]))(?:template\s*<[^>]*>\s*)?(?:typedef\s+)?(?P<sub>class|struct|union)\s+(?:[A-Z_][A-Z0-9_]*\s+)*(?P<name>[A-Za-z_]\w*)\s*(?:final\s*)?(?P<bases>:[^{;]*)?(?=\{)", "struct"),),
      namespaces=(r"(?:^|(?<=[\s;{}]))(?:inline\s+)?namespace(?:\s+[\w:]+)?\s*(?=\{)", r'extern\s+"C(?:\+\+)?"\s*(?=\{)'),
      typed_funcs=True, global_ns=True,
      imports=((r'^\s*#\s*include\s*"(?P<module>[^"]+)"', "file"),),
      field_res=(r"^[ \t]*(?:(?:static|const|mutable)\s+)*(?:std::)?(?:(?:unique_ptr|shared_ptr|weak_ptr)<)?(?P<type>[A-Z]\w*)>?\s*[*&]?\s+(?P<name>m?_?\w+)\s*(?:;|=|\{)",),
      keywords=COMMON_KW | {"sizeof", "alignof", "decltype", "static_assert", "defined"})

_spec("php", family="php", sq="string", dq_multiline=True, heredoc=True, line_comments=("//", "#"),
      containers=((_containers("class", "interface", "trait", "enum", mods="abstract|final|readonly"), "class"),),
      func_re=r"(?:^|(?<=[\s;{}]))(?:(?:public|private|protected|static|abstract|final)\s+)*function\s+&?(?P<name>\w+)\s*\(",
      self_names=("this", "self", "static"), implicit_self=False,
      imports=((r"^\s*use\s+(?P<module>[\w\\]+)(?:\s+as\s+(?P<alias>\w+))?\s*;", "type"),
               (r"(?:require|include)(?:_once)?\s*\(?\s*(?:__DIR__\s*\.\s*)?['\"](?P<module>[^'\"]+)['\"]", "file")),
      package_re=r"^\s*namespace\s+([\w\\]+)",
      field_res=(r"^[ \t]*(?:(?:public|private|protected|readonly|static)\s+)+\??(?P<type>[A-Z]\w*)\s+\$(?P<name>\w+)",),
      ctor_names=("__construct",),
      ctor_param_re=r"(?:public|private|protected)\s+(?:readonly\s+)?\??(?P<type>[A-Z]\w*)\s+\$(?P<name>\w+)",
      keywords=COMMON_KW | {"isset", "unset", "empty", "list", "array", "die", "exit", "eval"})

_spec("swift", family="swift", sq="none", triple=('"""',), nested_comments=True, semicolons=False,
      containers=((_containers("class", "struct", "enum", "protocol", "extension", "actor",
                               mods="public|private|fileprivate|internal|open|final|indirect"), "class"),),
      func_re=r"(?:^|(?<=[\s;{}]))(?:(?:public|private|fileprivate|internal|open|final|static|class|override|mutating|nonmutating|convenience|required|@\w+)\s+)*(?:func\s+(?P<name>\w+)|(?P<ctor>init)[?!]?)\s*(?:<[^(]*?>)?\s*\(",
      self_names=("self",), new_kw=False,
      imports=((r"^\s*import\s+(?:class\s+|struct\s+|func\s+|enum\s+)?(?P<module>[\w.]+)", "module"),),
      field_res=(r"^[ \t]*(?:(?:private|public|internal|fileprivate|weak|lazy|static|unowned|@\w+)\s+)*(?:let|var)\s+(?P<name>\w+)\s*:\s*(?P<type>[A-Z]\w*)",),
      keywords=COMMON_KW | {"guard", "repeat", "defer", "inout", "some", "any"})

_spec("dart", family="dart", sq="string", triple=('"""', "'''"),
      special=(r"r'[^'\n]*'", r'r"[^"\n]*"'),
      containers=((_containers("class", "mixin", "extension", "enum", mods="abstract|base|final|interface|sealed|mixin"), "class"),),
      typed_funcs=True, expr_body=("=>",),
      imports=((r"^\s*(?:import|export)\s+['\"](?P<module>[^'\"]+)['\"](?:\s+as\s+(?P<alias>\w+))?", "file"),),
      field_res=(r"^[ \t]*(?:(?:final|late|static|const)\s+)*(?P<type>[A-Z]\w*)(?:<[^;=\n]*?>)?\??\s+(?P<name>_?\w+)\s*[;=]",),
      keywords=COMMON_KW | {"required", "factory", "get", "set", "covariant"})

_spec("solidity", family="sol", sq="string",
      containers=((_containers("contract", "interface", "library", mods="abstract"), "contract"),
                  (r"(?:^|(?<=[\s;{}]))(?P<sub>struct|enum)\s+(?P<name>\w+)\s*(?=\{)", "struct")),
      func_re=r"(?:^|(?<=[\s;{}]))(?:function\s+(?P<name>\w+)|(?P<ctor>constructor|receive|fallback)|modifier\s+(?P<mod>\w+))\s*\(",
      imports=((r'^\s*import\s+(?:[^"\';]*from\s+)?["\'](?P<module>[^"\']+)["\']', "file"),),
      keywords=COMMON_KW | {"require", "revert", "emit", "assert"})

_spec("shell", family="script", line_comments=("#",), block_comments=(), hash_needs_space=True,
      sq="string-ml", sq_escape=False, dq_multiline=True, heredoc=True, semicolons=False,
      func_re=r"(?m)^[ \t]*(?:function\s+(?P<name>[\w.:-]+)(?:\s*\(\s*\))?|(?P<name2>[\w.:-]+)\s*\(\s*\))\s*(?=\{|\n\s*\{|\()",
      self_names=(), implicit_self=False, new_kw=False, global_ns=True, commands=True, member_ops=(),
      imports=((r"^\s*(?:source|\.)\s+[\"']?(?P<module>[\w./${}-]+)[\"']?", "file"),),
      keywords=frozenset("""if then else elif fi for while until do done case esac in function return local export
      declare readonly set unset shift exit echo printf test true false read cd eval exec trap source alias""".split()))

_spec("powershell", family="script", line_comments=("#",), block_comments=(("<#", "#>"),),
      sq="string-ml", sq_escape=False, dq_multiline=True, semicolons=False,
      special=(r"@'\r?\n[\s\S]*?\r?\n'@", r'@"\r?\n[\s\S]*?\r?\n"@'),
      containers=((r"(?im)^[ \t]*(?P<sub>class|enum)\s+(?P<name>\w+)(?P<bases>[^{]*)", "class"),),
      func_re=r"(?im)^[ \t]*(?:function|filter|workflow)\s+(?P<name>[\w-]+(?::[\w-]+)?)",
      self_names=("$this",), implicit_self=False, new_kw=False, global_ns=True, commands=True, member_ops=("::",),
      imports=((r"^\s*\.\s+[\"']?(?P<module>[^\"'\s]+\.ps1)", "file"),
               (r"(?i)^\s*Import-Module\s+[\"']?(?P<module>[^\"'\s]+)", "file")),
      keywords=frozenset("if else elseif foreach for while do switch return param begin process end try catch finally throw".split()))

_spec("r", family="script", line_comments=("#",), block_comments=(), sq="string-ml", dq_multiline=True, semicolons=False,
      func_re=r"(?m)^[ \t]*(?P<name>[\w.]+)\s*(?:<-|=)\s*function\s*\(",
      self_names=(), implicit_self=False, new_kw=False, global_ns=True, member_ops=("::", "$"),
      imports=((r"^\s*source\(\s*[\"'](?P<module>[^\"']+)[\"']", "file"),
               (r"^\s*(?:library|require)\(\s*[\"']?(?P<module>[\w.]+)", "module")),
      keywords=COMMON_KW | {"function", "repeat", "library", "require", "c", "list"})

_spec("ruby", family="ruby", engine="end", line_comments=("#",), block_comments=(("=begin", "=end"),),
      sq="string-ml", dq_multiline=True, heredoc=True, semicolons=False,
      self_names=("self",), new_kw=False, member_ops=("::",),
      imports=((r"^\s*require_relative\s*\(?\s*['\"](?P<module>[^'\"]+)['\"]", "file"),
               (r"^\s*require\s*\(?\s*['\"](?P<module>[^'\"]+)['\"]", "module")),
      keywords=COMMON_KW | {"puts", "raise", "attr_accessor", "attr_reader", "include", "extend", "private", "protected", "public", "require", "yield"})

_spec("lua", family="script", engine="end", line_comments=("--",), block_comments=(), sq="string", semicolons=False,
      special=(r"--\[(?P<lq>=*)\[[\s\S]*?\](?P=lq)\]", r"\[(?P<lq2>=*)\[[\s\S]*?\](?P=lq2)\]"),
      self_names=("self",), implicit_self=False, new_kw=False, global_ns=True, member_ops=(":",),
      imports=((r"(?:local\s+(?P<alias>\w+)\s*=\s*)?require\s*\(?\s*['\"](?P<module>[^'\"]+)['\"]", "module"),),
      keywords=COMMON_KW | {"local", "nil", "repeat", "elseif", "pairs", "ipairs"})

_spec("elixir", family="elixir", engine="end", line_comments=("#",), block_comments=(), sq="string",
      triple=('"""', "'''"), dq_multiline=True, semicolons=False,
      self_names=("__MODULE__",), implicit_self=True, new_kw=False, member_ops=(),
      imports=((r"^\s*alias\s+(?P<module>[\w.]+)(?:,\s*as:\s*(?P<alias>\w+))?", "type"),
               (r"^\s*(?:import|use|require)\s+(?P<module>[\w.]+)", "module")),
      keywords=COMMON_KW | {"defmodule", "defp", "defmacro", "fn", "cond", "with", "receive", "quote", "unquote", "pipe"})


# ---------------------------------------------------------------- blanking

def _starter_regex(spec: Spec):
    parts = []
    for i, pat in enumerate(spec.special):
        parts.append((f"sp{i}", pat))
    for i, t in enumerate(spec.triple):
        parts.append((f"tq{i}", re.escape(t)))
    for i, (a, _b) in enumerate(spec.block_comments):
        parts.append((f"bc{i}", ("^" if a in ("=begin",) else "") + re.escape(a)))
    for i, t in enumerate(spec.line_comments):
        parts.append((f"lc{i}", re.escape(t)))
    if spec.heredoc:
        parts.append(("hd", r"<<[-~]?\s*(?P<hq>['\"]?)(?P<hid>[A-Za-z_]\w*)(?P=hq)"))
    parts.append(("dq", '"'))
    if spec.sq != "none":
        parts.append(("sq", "'"))
    if spec.backtick_raw:
        parts.append(("bq", "`"))
    if spec.preprocessor:
        parts.append(("pp", r"^[ \t]*#"))
    return re.compile("|".join(f"(?P<{name}>{pat})" for name, pat in parts), re.M), parts


_STARTERS: dict = {}
_NOT_NL = re.compile(r"[^\n]")


def blank(text: str, spec: Spec, keep_strings: bool) -> str:
    """Comments (and, unless keep_strings, string contents) become spaces; offsets survive."""
    if spec.name not in _STARTERS:
        _STARTERS[spec.name] = _starter_regex(spec)
    master, _ = _STARTERS[spec.name]
    n = len(text)
    wipes: list = []
    i = 0
    while True:
        m = master.search(text, i)
        if not m:
            break
        kind = m.lastgroup
        j = m.start()
        if kind.startswith("sp"):                    # whole raw-string literal (or Lua long comment)
            if not keep_strings or text.startswith("--", j):
                wipes.append((j + 1, m.end() - 1))
            i = m.end()
            continue
        if kind.startswith("tq"):
            delim = spec.triple[int(kind[2:])]
            end = text.find(delim, m.end())
            end = n if end < 0 else end
            if not keep_strings:
                wipes.append((m.end(), end))
            i = end + len(delim)
            continue
        if kind.startswith("bc"):
            a, b = spec.block_comments[int(kind[2:])]
            k, depth = m.end(), 1
            while depth:
                close = text.find(b, k)
                if close < 0:
                    k = n
                    break
                if spec.nested_comments:
                    nxt = text.find(a, k)
                    if 0 <= nxt < close:
                        depth += 1
                        k = nxt + len(a)
                        continue
                depth -= 1
                k = close + len(b)
            wipes.append((j, k))
            i = k
            continue
        if kind.startswith("lc") or kind == "pp":
            if kind != "pp" and spec.hash_needs_space and j > 0 and text[j - 1] not in " \t\n;":
                i = j + 1
                continue
            end = text.find("\n", j)
            end = n if end < 0 else end
            if kind == "pp":
                while end > 0 and text[end - 1] == "\\" and end < n:   # continued directive
                    nxt = text.find("\n", end + 1)
                    end = n if nxt < 0 else nxt
            wipes.append((j, end))
            i = end
            continue
        if kind == "hd":
            ident = m.group("hid")
            nl = text.find("\n", m.end())
            if nl < 0:
                i = m.end()
                continue
            term = re.compile(r"(?m)^[ \t]*" + re.escape(ident) + r"[ \t]*;?\s*$")
            t = term.search(text, nl + 1)
            end = t.start() if t else n
            if not keep_strings:
                wipes.append((nl + 1, end))
            i = t.end() if t else n
            continue
        if kind == "bq":
            end = text.find("`", j + 1)
            end = n if end < 0 else end
            if not keep_strings:
                wipes.append((j + 1, end))
            i = end + 1
            continue
        quote = text[j]
        if kind == "sq" and spec.sq == "char":
            k = j + 1
            if k < n and text[k] == "\\":
                k += 2
            else:
                k += 1
            close = text.find("'", k, k + 10)
            if close < 0 or "\n" in text[j:close]:
                i = j + 1                            # a Rust lifetime or Kotlin label, not a literal
                continue
            if not keep_strings:
                wipes.append((j + 1, close))
            i = close + 1
            continue
        multiline = spec.dq_multiline if quote == '"' else spec.sq == "string-ml"
        escapes = spec.sq_escape or quote == '"'
        k = j + 1
        while k < n:
            ch = text[k]
            if ch == "\\" and escapes:
                k += 2
                continue
            if ch == quote:
                if spec.name == "powershell" and text[k + 1:k + 2] == quote:   # '' escape
                    k += 2
                    continue
                break
            if ch == "\n" and not multiline:
                break
            k += 1
        if not keep_strings:
            wipes.append((j + 1, min(k, n)))
        i = k + 1
    if not wipes:
        return text
    pieces, prev = [], 0
    for a, b in wipes:
        if b <= prev:
            continue
        a = max(a, prev)
        pieces.append(text[prev:a])
        pieces.append(_NOT_NL.sub(" ", text[a:b]))
        prev = b
    pieces.append(text[prev:])
    return "".join(pieces)


# ---------------------------------------------------------------- helpers

def _match(code: str, pos: int, open_ch: str, close_ch: str, limit: int = 20000) -> int:
    depth = 0
    for i in range(pos, min(len(code), pos + limit)):
        ch = code[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
    return -1


def _end_of_expr(code: str, pos: int, semicolons: bool) -> int:
    depth, n = 0, len(code)
    i = pos
    while i < n:
        ch = code[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                return i - 1
            depth -= 1
        elif depth == 0 and (ch == ";" or (ch == "\n" and not semicolons and code[pos:i].strip())):
            return i
        i += 1
    return n - 1


def _find_body(code: str, pos: int, spec: Spec, bmatch: dict, limit: int = 600):
    """After a parameter list: ("block", start, end) | ("expr", start, end) | ("decl", start, end) | None."""
    n = len(code)
    i, depth = pos, 0
    end = min(n, pos + limit)
    while i < end:
        ch = code[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth <= 0:
            if ch == "{":
                close = bmatch.get(i)
                return ("block", i, close) if close is not None else None
            if ch == ";":
                return ("decl", i, i)
            for tok in spec.expr_body:
                if code.startswith(tok, i) and (tok != "=" or (code[i + 1:i + 2] not in "=>" and code[i - 1:i] not in "=!<>+-*/%&|^:")):
                    return ("expr", i, _end_of_expr(code, i + len(tok), spec.semicolons))
            if ch == "\n" and not spec.semicolons:
                k = i + 1
                while k < n and code[k] in " \t\r\n":
                    k += 1
                nxt = code[k:k + 6]
                if not (nxt.startswith(("{", "=", ":", "->", "where", "throws", "rethro")) or nxt[:1] in ".,"):
                    return ("decl", i, i)
            if ch == "}":
                return None
        i += 1
    return None


def _parse_bases(header: str) -> list:
    header = re.split(r"\bwhere\b|\{", header, maxsplit=1)[0]
    header = re.sub(r"\([^()]*\)", " ", header)
    while True:
        stripped = _TYPE_ARGS.sub(" ", header)
        if stripped == header:
            break
        header = stripped
    words = re.findall(r"[A-Za-z_][\w.:]*", header)
    skip = {"extends", "implements", "with", "public", "private", "protected", "virtual", "is", "final", "open",
            "sealed", "abstract", "class", "struct", "where", "on", "permits", "internal", "override", "data",
            "const", "val", "var", "let"}
    return [w.replace("::", ".") for w in words if w not in skip and not w[0].islower()][:6]


def _doc_above(lines: list, line_no: int, spec: Spec) -> tuple:
    """(doc sentence, first line of the doc/annotation block above `line_no`)."""
    k = line_no - 2
    first = line_no
    body: list = []
    markers = ("///", "//!", "//", "/**", "/*", "*/", "*", "#", "--", "%")
    while k >= 0:
        raw = lines[k].strip()
        if not raw:
            break
        if raw.startswith(("@doc", "@moduledoc")):           # Elixir documentation attribute
            quoted = re.search(r'"(.*?)"|\'(.*?)\'', raw)
            if quoted:
                body.insert(0, quoted.group(1) or quoted.group(2) or "")
            first = k + 1
            k -= 1
            continue
        if raw.startswith("@") or (raw.startswith("[") and raw.endswith("]") and spec.name in ("csharp",)) \
                or raw.startswith("#[") or raw.startswith("#!"):
            first = k + 1
            k -= 1
            continue
        if raw.startswith(markers) and not raw.startswith("#include"):
            text = raw
            for mk in markers:
                if text.startswith(mk):
                    text = text[len(mk):]
                    break
            text = text.rstrip("*/").strip()
            if not text.startswith("@"):
                body.insert(0, text)
            first = k + 1
            k -= 1
            continue
        break
    doc = re.sub(r"</?\w+[^>]*>", "", " ".join(t for t in body if t))    # C# /// <summary> tags
    return first_sentence(doc), first


RE_CALL = re.compile(r"(?<![\w$.#@])((?:[A-Za-z_$][\w$]*)(?:\s*\.\s*[A-Za-z_$][\w$]*)*)\s*(?:<[\w$.,\s\[\]|?]*>)?\s*\(")
RE_NEW = re.compile(r"\bnew\s+([A-Za-z_][\w.]*)")
RE_CMD = re.compile(r"(?:^|[;&|(`=]|\bthen\b|\bdo\b|\belse\b|\$\()\s*([A-Za-z_][\w.:-]*)", re.M)
RE_CALLBACK = re.compile(r"[(,]\s*((?:this\s*\.\s*)?[A-Za-z_]\w*)\s*(?=[,)])")   # map(f, xs), sapply(xs, count_words)
RE_SCRIPT_REF = re.compile(r"(?:^|[\s;&|(\"'=])(?:bash\s+|sh\s+|zsh\s+|source\s+|\.\s+|pwsh\s+|python3?\s+|node\s+)?((?:\.{0,2}/)?(?:[\w.-]+/)*[\w.-]+\.(?:sh|bash|ps1|py|js|mjs|ts|rb|pl))\b")


def _normalize(code: str, spec: Spec) -> str:
    for op in spec.member_ops:
        code = code.replace(op, " " * (len(op) - 1) + ".")
    code = code.replace("?.", " .").replace("!.", " .")
    if spec.name == "php":
        code = code.replace("$this", " this").replace("$", " ")
    if spec.name == "ruby":
        code = re.sub(r"@(?=[A-Za-z_])", "self.", code) if "@" in code else code
    return code


def _calls(code: str, spec: Spec, self_alias: str | None = None) -> dict:
    """Call chains in a body: ("helper",), ("this", "save"), ("store", "load"), ("Note",) for `new Note(`."""
    seen: dict = {}
    selfs = {n.lstrip("$") for n in spec.self_names}
    if self_alias:
        selfs.add(self_alias)

    def norm(parts: tuple):
        if not parts:
            return None
        if parts[0].lstrip("$") in selfs:           # self/this/receiver first: `this` is also a keyword
            parts = ("this",) + parts[1:]
            return parts if len(parts) > 1 else None
        if parts[0] in spec.keywords or (len(parts) == 1 and parts[0] in COMMON_KW):
            return None
        return parts

    for m in RE_CALL.finditer(code):
        parts = norm(tuple(p for p in re.split(r"\s*\.\s*", m.group(1)) if p))
        if parts:
            seen.setdefault(parts, m.start())
    for m in RE_CALLBACK.finditer(code):              # functions passed as arguments
        parts = norm(tuple(p for p in re.split(r"\s*\.\s*", m.group(1)) if p))
        if parts and parts[-1] not in ("true", "false", "null", "nil", "None", "undefined", "self", "this"):
            seen.setdefault(("&",) + parts, m.start())
    if spec.new_kw:
        for m in RE_NEW.finditer(code):
            seen.setdefault(tuple(m.group(1).split(".")), m.start())
    if spec.commands:
        for m in RE_CMD.finditer(code):
            word = m.group(1)
            if word not in spec.keywords:
                seen.setdefault((word,), m.start())
    if spec.name == "ruby":
        for m in re.finditer(r"(?<![\w.:@])((?:[A-Za-z_]\w*)(?:\.[A-Za-z_]\w*[?!]?)+)", code):
            parts = tuple(m.group(1).split("."))
            if parts[-1] == "new":
                parts = parts[:-1]
            parts = norm(parts)
            if parts:
                seen.setdefault(parts, m.start())
    return seen


def script_refs(text: str) -> list:
    """Paths of scripts a file runs: `./scripts/deploy.sh`, `bash ci/test.sh`, `python tools/x.py`."""
    return list(dict.fromkeys(m.group(1) for m in RE_SCRIPT_REF.finditer(text)))


# ---------------------------------------------------------------- brace engine

def _parse_brace(path: str, text: str, spec: Spec, fi: FileInfo) -> None:
    code = _normalize_layout(blank(text, spec, keep_strings=False))
    code_str = blank(text, spec, keep_strings=True)
    lines = text.splitlines()
    newlines = [m.start() for m in re.finditer("\n", text)]

    def line_of(pos: int) -> int:
        return bisect.bisect_left(newlines, pos) + 1

    ev_pos, ev_depth, bmatch, stack, depth = [], [], {}, [], 0
    for m in re.finditer(r"[{}]", code):
        if m.group() == "{":
            depth += 1
            stack.append(m.start())
        else:
            depth = max(0, depth - 1)
            if stack:
                bmatch[stack.pop()] = m.start()
        ev_pos.append(m.start())
        ev_depth.append(depth)

    def depth_at(pos: int) -> int:
        k = bisect.bisect_left(ev_pos, pos)
        return ev_depth[k - 1] if k else 0

    ns_ranges = []
    for pat in spec.namespaces:
        for m in re.finditer(pat, code, re.M):
            open_at = code.find("{", m.end() - 1 if code[m.end() - 1:m.end()] == "{" else m.end())
            if open_at in bmatch:
                ns_ranges.append((open_at, bmatch[open_at]))

    def eff(pos: int) -> int:
        d = depth_at(pos)
        for a, b in ns_ranges:
            if a < pos < b:
                d -= 1
        return d

    if spec.package_re:
        m = re.search(spec.package_re, code_str, re.M)
        if m:
            fi.package = m.group(1).replace("\\", ".")

    # ---- containers
    containers: list = []
    for pat, default_sub in spec.containers:
        for m in re.finditer(pat, code, re.M):
            start = m.start()
            gd = m.groupdict()
            sub = (gd.get("sub") or default_sub or "class").strip()
            name = (gd.get("name") or "").strip()
            bases_text = gd.get("bases") or ""
            if sub == "impl":                       # Rust: impl Trait for Type / impl Type
                target = _TYPE_ARGS.sub("", name)
                if " for " in f" {target} ":
                    trait, typ = re.split(r"\s+for\s+", target, maxsplit=1)
                    name, bases_text = typ.strip(), trait.strip()
                else:
                    name = target.strip()
                name = re.sub(r"[^\w].*$", "", name.split("::")[-1]) or name
            if not name or not (name[0].isalpha() or name[0] == "_") or name in spec.keywords:
                continue
            open_at = -1
            k, pdepth = m.end(), 0
            while k < min(len(code), m.end() + 800):
                ch = code[k]
                if ch in "(<[":
                    pdepth += 1
                elif ch in ")>]":
                    pdepth -= 1
                elif pdepth <= 0 and ch == "{":
                    open_at = k
                    break
                elif pdepth <= 0 and (ch == ";" or (ch == "\n" and not spec.semicolons and code[m.end():k].count("(") == code[m.end():k].count(")")
                                                   and not code[k:k + 80].lstrip().startswith(("{", ":", "where", ",", "extends", "implements", "with")))):
                    break
                k += 1
            body = (open_at, bmatch[open_at]) if open_at in bmatch else None
            header_end = open_at if body else k
            containers.append({
                "name": name, "sub": sub, "start": start, "body": body, "depth": eff(start),
                "bases": _parse_bases(bases_text or code[m.end():header_end]),
                "header": code_str[start:header_end], "end": body[1] if body else header_end,
            })
    containers.sort(key=lambda c: c["start"])
    by_start = []
    for c in containers:
        parent = None
        for p in by_start:
            if p["body"] and p["body"][0] < c["start"] < p["body"][1] and c["depth"] == eff(p["body"][0] + 1):
                parent = p
        if c["depth"] != 0 and parent is None:
            continue                                 # a local class inside a function body
        c["parent"] = parent
        c["qual"] = (parent["qual"] + "." if parent else "") + c["name"]
        by_start.append(c)
    containers = by_start

    def container_at(pos: int):
        best = None
        d = eff(pos)
        for c in containers:
            if c["body"] and c["body"][0] < pos < c["body"][1] and d == eff(c["body"][0] + 1):
                if best is None or c["body"][0] > best["body"][0]:
                    best = c
        return best

    # ---- callables
    found: list = []
    claimed: set = set()

    def add_callable(name: str, start: int, paren: int, owner=None, self_alias=None, is_ctor=False):
        if not name:
            return
        d = eff(start)
        cont = owner if owner is not None else container_at(start)
        if cont is None and d != 0:
            return
        if paren >= 0:
            close = _match(code, paren, "(", ")")
            if close < 0:
                return
            body = _find_body(code, close + 1, spec, bmatch)
        else:
            body = _find_body(code, start, spec, bmatch) if spec.paren_optional else None
        if body is None:
            return
        kind, b0, b1 = body
        if kind == "decl" and not (cont and (cont["sub"] in spec.interface_subs or spec.family == "c")):
            return
        if b0 in claimed:
            return
        claimed.add(b0)
        found.append({"name": name, "start": start, "body": (b0, b1), "decl": kind == "decl", "cont": cont,
                      "self": self_alias, "ctor": is_ctor})

    if spec.func_re:
        for m in re.finditer(spec.func_re, code, re.M):
            gd = m.groupdict()
            name = gd.get("name") or gd.get("name2") or gd.get("ctor") or gd.get("mod")
            paren = code.find("(", m.end() - 1) if code[m.end() - 1:m.end()] == "(" else -1
            if spec.commands and paren < 0:
                paren = -1
            owner, alias = None, None
            if gd.get("recv"):                      # Go: func (s *Server) Handle(...)
                rm = re.match(r"\s*(\w+)?\s*\*?\s*([A-Za-z_]\w*)", gd["recv"])
                if rm:
                    alias, typ = rm.group(1), rm.group(2)
                    owner = next((c for c in containers if c["name"] == typ and c["depth"] == 0), None)
                    if owner is None:
                        owner = {"name": typ, "sub": "methods", "start": m.start(), "body": None, "depth": 0,
                                 "bases": [], "header": "", "end": m.start(), "parent": None, "qual": typ, "pseudo": True}
                        containers.append(owner)
            if spec.commands:
                body_open = code.find("{", m.end())
                if body_open in bmatch and re.fullmatch(r"\s*(\([^)]*\))?\s*", code[m.end():body_open]):
                    if body_open not in claimed and eff(m.start()) == 0:
                        claimed.add(body_open)
                        found.append({"name": name, "start": m.start(), "body": (body_open, bmatch[body_open]),
                                      "decl": False, "cont": None, "self": None, "ctor": False})
                continue
            add_callable(name, m.start(), paren, owner=owner, self_alias=alias,
                         is_ctor=bool(gd.get("ctor")) or name in spec.ctor_names)

    if spec.typed_funcs:
        typed = re.compile(
            r"(?m)^[ \t]*(?:(?:@\w+(?:\([^()\n]*\))?|\[[^\]\n]*\])\s*)*"
            r"(?P<mods>(?:[\w$<>\[\],.?*&:~]+[ \t]+(?:[*&]+[ \t]*)?)*?)"
            r"(?P<name>~?[A-Za-z_]\w*(?:::~?[A-Za-z_]\w*)*|operator\s*\S{1,3})\s*(?:<[^<>()\n]*>)?\s*\(")
        for m in typed.finditer(code):
            name = m.group("name")
            mods = m.group("mods").strip()
            start = m.start("mods") if mods else m.start("name")
            last = name.split("::")[-1]
            if last in TYPED_EXCLUDE:
                continue
            words = mods.split()
            if words and words[-1] in ("return", "new", "else", "case", "throw", "await", "yield", "goto", "delete", "co_return"):
                continue
            owner = None
            if "::" in name:                        # C++: Type Class::method(...) { out-of-line definition
                cls_name = name.split("::")[-2]
                owner = next((c for c in containers if c["name"] == cls_name), None)
                if owner is None:
                    owner = {"name": cls_name, "sub": "class", "start": start, "body": None, "depth": 0, "bases": [],
                             "header": "", "end": start, "parent": None, "qual": cls_name, "pseudo": True}
                    containers.append(owner)
                name = last
            cont = owner or container_at(start)
            is_ctor = bool(cont) and (last == cont["name"] or last == "~" + cont["name"] or last in spec.ctor_names)
            if not mods and not is_ctor and not owner:
                continue
            if mods and not is_ctor and not re.search(r"[A-Za-z_]", mods.replace("const", "")):
                continue
            add_callable(name, start, m.end() - 1, owner=owner, is_ctor=is_ctor)

    # a definition replaces its declaration (C++ `void f();` in the class, `void X::f() {}` later)
    defined = {(id(f["cont"]) if f["cont"] else 0, f["name"]) for f in found if not f["decl"]}
    found = [f for f in found if not (f["decl"] and (id(f["cont"]) if f["cont"] else 0, f["name"]) in defined)]

    # ---- symbols
    syms: list = []
    cont_sym: dict = {}
    by_qual: dict = {}
    for c in sorted(containers, key=lambda c: (c["sub"] in MERGE_SUBS or bool(c.get("pseudo")), c["start"])):
        if c.get("pseudo"):
            members = [f for f in found if f["cont"] is c]
            if not members:
                continue
            c["start"] = min(f["start"] for f in members)
            c["end"] = max(f["body"][1] for f in members)
        doc, first = _doc_above(lines, line_of(c["start"]), spec)
        start_line = min(first, line_of(c["start"]))
        end_line = line_of(c["end"])
        same = by_qual.get(c["qual"])
        if same is not None and (c["sub"] in MERGE_SUBS or c.get("pseudo")):
            # Rust impl blocks, Swift extensions, Go methods and C++ out-of-line bodies join their type
            same.line, same.end = min(same.line, start_line), max(same.end, end_line)
            same.bases.extend(b for b in c["bases"] if b not in same.bases)
            cont_sym[id(c)] = same
            continue
        sym = Sym(id=sym_id("class", path, c["qual"]), kind="class", name=c["name"], qual=c["qual"], file=path,
                  line=start_line, end=end_line, sub=c["sub"], doc=doc,
                  signature=" ".join(c["header"].split())[:160], bases=c["bases"],
                  exported=not c["name"].startswith("_"))
        sym.hash = sha1(f"{sym.signature}|{doc}|" + ",".join(sorted(f["name"] for f in found if f["cont"] is c)))
        sym.decorators = [ln.strip() for ln in lines[start_line - 1:line_of(c["start"])] if ln.strip().startswith(("@", "["))]
        if fi.package and spec.family in ("jvm", "dotnet", "php"):
            sym.keys.append(f"fqn:{fi.package}.{c['qual']}")
        if spec.family == "go":
            sym.keys.append(f"go:{path.rsplit('/', 1)[0] if '/' in path else ''}.{c['name']}")
        cont_sym[id(c)] = sym
        by_qual[c["qual"]] = sym
        syms.append(sym)
        if c["body"] and spec.field_res:
            body_depth = eff(c["body"][0] + 1)
            region = code[c["body"][0] + 1:c["body"][1]]
            base = c["body"][0] + 1
            for pat in spec.field_res:
                for fm in re.finditer(pat, region, re.M):
                    if eff(base + fm.start("name")) == body_depth and fm.group("type") not in spec.keywords:
                        sym.attrs.setdefault(fm.group("name"), (fm.group("type"),))
        if spec.ctor_param_re:
            for fm in re.finditer(spec.ctor_param_re, c["header"]):
                sym.attrs.setdefault(fm.group("name"), (fm.group("type"),))

    for f in sorted(found, key=lambda x: x["start"]):
        c = f["cont"]
        parent = cont_sym.get(id(c)) if c is not None else None
        kind = "method" if parent else "function"
        qual = f"{parent.qual}.{f['name']}" if parent else f["name"]
        doc, first = _doc_above(lines, line_of(f["start"]), spec)
        start_line = min(first, line_of(f["start"]))
        b0, b1 = f["body"]
        sig = " ".join(text[f["start"]:b0].split())
        sig = sig.rstrip("{=").strip()
        if len(sig) > 160:
            sig = sig[:157] + "..."
        sym = Sym(id=sym_id("function", path, qual), kind=kind, name=f["name"], qual=qual, file=path,
                  line=start_line, end=line_of(b1), signature=sig, doc=doc, parent=parent.id if parent else None,
                  sub=("constructor" if f["ctor"] else ("abstract" if f["decl"] else "")),
                  exported=not f["name"].startswith("_"))
        anno = [ln.strip() for ln in lines[start_line - 1:line_of(f["start"])] if ln.strip().startswith(("@", "["))]
        sym.decorators = anno
        segment = text[f["start"]:b1 + 1]
        sym.hash = sha1("\n".join(ln.rstrip() for ln in segment.splitlines()))
        if not f["decl"]:
            body = _normalize(code[b0:b1 + 1], spec)
            sym.calls = [(chain, line_of(b0 + pos)) for chain, pos in _calls(body, spec, f["self"]).items()]
        if spec.family == "go" and not parent and fi.package:
            sym.keys.append(f"go:{path.rsplit('/', 1)[0] if '/' in path else ''}.{f['name']}")
        if parent and f["ctor"] and spec.ctor_param_re:          # PHP promotion: __construct(private Repo $repo)
            for fm in re.finditer(spec.ctor_param_re, text[f["start"]:b0]):
                parent.attrs.setdefault(fm.group("name"), (fm.group("type"),))
        if parent and f["ctor"] and spec.ctor_param_re == "":
            params = text[f["start"]:b0]
            body_txt = text[b0:b1 + 1]
            types = {}
            for pm in re.finditer(r"(?P<type>[A-Z]\w*)(?:<[^>]*>)?\??\s+(?P<name>\w+)\s*[,)]", params):
                types[pm.group("name")] = pm.group("type")
            for pm in re.finditer(r"(?P<name>\w+)\s*:\s*(?P<type>[A-Z]\w*)", params):
                types.setdefault(pm.group("name"), pm.group("type"))
            for am in re.finditer(r"(?:this|self)\s*(?:\.|->)\s*(\w+)\s*=\s*(\w+)\s*;?", body_txt):
                if am.group(2) in types:
                    parent.attrs.setdefault(am.group(1), (types[am.group(2)],))
        syms.append(sym)

    fi.symbols = sorted(syms, key=lambda s: (s.line, s.kind != "class"))
    # parents must precede their members
    ordered, emitted = [], set()
    for s in fi.symbols:
        if s.kind == "class":
            ordered.append(s)
            emitted.add(s.id)
            ordered.extend(m for m in fi.symbols if m.parent == s.id)
    ordered.extend(s for s in fi.symbols if s.kind == "function")
    fi.symbols = ordered

    # ---- top-level code (scripts): entry hints
    if spec.commands or spec.family == "script":
        spans = [(s.line, s.end) for s in fi.symbols]
        top = "\n".join(ln if not any(a <= i + 1 <= b for a, b in spans) else "" for i, ln in enumerate(code.split("\n")))
        fi.main_calls = list(_calls(_normalize(top, spec), spec))


def _normalize_layout(code: str) -> str:
    return code


# ---------------------------------------------------------------- end engine (Ruby, Lua, Elixir)

END_SIGNATURE = {
    "ruby": r"^\s*def\s+(?:(?:self|[A-Z]\w*)\.)?[\w?!=\[\]<>+\-*/%]+(?:\([^)]*\))?",
    "lua": r"(?:local\s+)?function\s*[\w.:]*\s*\([^)]*\)|[\w.]+\s*=\s*function\s*\([^)]*\)",
    "elixir": r"^\s*def\w*\s+[\w?!]+(?:\([^)]*\))?",
}
RUBY_OPEN_LINE = re.compile(r"^\s*(if|unless|while|until|case|for|begin)\b")
RUBY_ASSIGN_OPEN = re.compile(r"=\s*(if|unless|case|begin)\b")
RUBY_DO = re.compile(r"\bdo\b(\s*\|[^|]*\|)?")
END_TOKEN = re.compile(r"(?<![\w.:@$])end(?![\w?!:])")


def _parse_end(path: str, text: str, spec: Spec, fi: FileInfo) -> None:
    code = blank(text, spec, keep_strings=False)
    lines = text.splitlines()
    clines = code.split("\n")
    stack: list = []          # (kind, name, sub, start_line, parent_frame)
    closed: list = []         # (kind, name, sub, start, end, parent_name_chain)

    def chain(frames) -> list:
        return [f[1] for f in frames if f[0] == "class"]

    for no, line in enumerate(clines, 1):
        events = []           # (pos, "open"/"close", kind, name, sub)
        s = line
        if spec.name == "ruby":
            m = re.match(r"^\s*(class|module)\s+(<<\s*self|[A-Z][\w:]*)(?:\s*<\s*([\w:]+))?", s)
            if m and not m.group(2).startswith("<<"):
                events.append((m.start(1), "open", "class", m.group(2).split("::")[-1], m.group(1), m.group(3)))
            elif m:
                events.append((m.start(1), "open", "block", "", "", None))
            dm = re.match(r"^\s*def\s+(?:(self|[A-Z]\w*)\.)?([\w?!=\[\]<>+\-*/%]+)", s)
            if dm and not re.match(r"^\s*def\s+[\w.?!=\[\]]+(\([^)]*\))?\s*=\s*\S", s):
                events.append((dm.start(), "open", "def", dm.group(2), "singleton" if dm.group(1) else "", None))
            om = RUBY_OPEN_LINE.match(s) or RUBY_ASSIGN_OPEN.search(s)
            if om and not (om.group(1) in ("while", "until", "for") and RUBY_DO.search(s)):
                events.append((om.start(1), "open", "block", "", "", None))
            for dmatch in RUBY_DO.finditer(s):
                events.append((dmatch.start(), "open", "block", "", "", None))
        elif spec.name == "lua":
            for fm in re.finditer(r"\bfunction\b(?:\s+([\w.:]+))?", s):
                name = fm.group(1)
                if not name:
                    am = re.search(r"([\w.]+)\s*=\s*$", s[:fm.start()])
                    name = am.group(1) if am else ""
                events.append((fm.start(), "open", "def" if name else "block", name or "", "", None))
            for om in re.finditer(r"(?<![\w.])(do|if|repeat)\b", s):
                if om.group(1) == "if" and re.search(r"\belseif\s*$", s[:om.end()]):
                    continue
                events.append((om.start(), "open", "block", "", "repeat" if om.group(1) == "repeat" else "", None))
            for um in re.finditer(r"\buntil\b", s):
                events.append((um.start(), "close", "until", "", "", None))
        else:  # elixir
            mm = re.match(r"^\s*defmodule\s+([\w.]+)\s+do\b", s)
            dm = re.match(r"^\s*(def|defp|defmacro|defmacrop)\s+([\w?!]+)", s)
            for om in re.finditer(r"\bdo\b(?!:)|\bfn\b", s):
                if mm and om.start() > mm.start() and om.group() == "do" and not any(e[1] == "open" and e[2] == "class" for e in events):
                    events.append((om.start(), "open", "class", mm.group(1).split(".")[-1], "module", None))
                elif dm and om.group() == "do" and not any(e[2] == "def" for e in events):
                    events.append((om.start(), "open", "def", dm.group(2), "private" if dm.group(1).endswith("p") else "", None))
                else:
                    events.append((om.start(), "open", "block", "", "", None))
            if dm and not any(e[2] == "def" for e in events) and re.search(r",\s*do:", s):
                closed.append(("def", dm.group(2), "", no, no, chain([f for f in stack])))
        for em in END_TOKEN.finditer(s):
            events.append((em.start(), "close", "end", "", "", None))
        for ev in sorted(events, key=lambda e: e[0]):
            if ev[1] == "open":
                stack.append((ev[2], ev[3], ev[4], no, ev[5]))
            elif stack:
                if ev[2] == "until" and stack[-1][2] != "repeat":
                    continue
                kind, name, sub, start, bases = stack.pop()
                if kind in ("class", "def") and name:
                    closed.append((kind, name, sub, start, no, chain(stack), bases))
    for kind, name, sub, start, bases in reversed(stack):
        if kind in ("class", "def") and name:
            closed.append((kind, name, sub, start, len(clines), chain([]), bases))

    syms, by_chain = [], {}
    for item in sorted(closed, key=lambda c: c[3]):
        kind, name, sub, start, end, parents = item[:6]
        bases = item[6] if len(item) > 6 else None
        doc, first = _doc_above(lines, start, spec)
        if kind == "class":
            qual = ".".join(parents + [name])
            sym = Sym(id=sym_id("class", path, qual), kind="class", name=name, qual=qual, file=path, line=min(first, start),
                      end=end, sub=sub or "class", doc=doc, signature=lines[start - 1].strip()[:160] if start - 1 < len(lines) else "",
                      bases=[bases.split("::")[-1]] if bases else [])
            if fi.package or spec.name == "elixir":
                mod = re.match(r"^\s*defmodule\s+([\w.]+)", lines[start - 1]) if start - 1 < len(lines) else None
                if mod:
                    sym.keys.append(f"ex:{mod.group(1)}")
            by_chain[qual] = sym
            syms.append(sym)
            continue
        owner_qual = ".".join(parents)
        owner = by_chain.get(owner_qual)
        if spec.name == "lua" and ("." in name or ":" in name):
            table, _, meth = re.split(r"([.:])", name, maxsplit=1)
            owner = by_chain.get(table)
            if owner is None:
                owner = Sym(id=sym_id("class", path, table), kind="class", name=table, qual=table, file=path,
                            line=start, end=end, sub="table")
                by_chain[table] = owner
                syms.append(owner)
            owner.end = max(owner.end, end)
            name = meth
        qual = f"{owner.qual}.{name}" if owner else name
        body_lines = clines[start - 1:end]
        if body_lines:                               # drop the definition itself (`def name(args)`) from the body
            body_lines = [re.sub(END_SIGNATURE[spec.name], " ", body_lines[0], count=1)] + body_lines[1:]
        body = _normalize("\n".join(body_lines), spec)
        sym = Sym(id=sym_id("function", path, qual), kind="method" if owner else "function", name=name, qual=qual,
                  file=path, line=min(first, start), end=end, doc=doc, parent=owner.id if owner else None,
                  signature=lines[start - 1].strip()[:160] if start - 1 < len(lines) else "", sub=sub if sub == "private" else "",
                  exported=sub != "private" and not name.startswith("_"))
        sym.calls = [(c, start) for c in _calls(body, spec)]
        sym.hash = sha1("\n".join(ln.rstrip() for ln in lines[start - 1:end]))
        syms.append(sym)
    for s in syms:
        if s.kind == "class":
            s.hash = sha1(s.signature + "|" + ",".join(sorted(m.name for m in syms if m.parent == s.id)))
    ordered = []
    for s in sorted(syms, key=lambda s: s.line):
        if s.kind == "class":
            ordered.append(s)
            ordered.extend(m for m in sorted(syms, key=lambda x: x.line) if m.parent == s.id)
    ordered.extend(s for s in sorted(syms, key=lambda x: x.line) if s.kind == "function")
    fi.symbols = ordered
    spans = [(s.line, s.end) for s in fi.symbols]
    top = "\n".join(ln if not any(a <= i + 1 <= b for a, b in spans) else "" for i, ln in enumerate(clines))
    fi.main_calls = [c for c in _calls(_normalize(top, spec), spec) if len(c) <= 2 and c[0] != "&"]


# ---------------------------------------------------------------- imports + entry point

def _imports(text_str: str, spec: Spec, fi: FileInfo) -> None:
    for pat, kind in spec.imports:
        for m in re.finditer(pat, text_str, re.M):
            gd = m.groupdict()
            module = (gd.get("module") or "").strip()
            if not module:
                continue
            if kind == "rust-use":
                names = gd.get("names")
                base = module.rstrip(":")
                if names:
                    for part in names.split(","):
                        part = part.strip()
                        if not part or part == "self":
                            continue
                        orig, _, alias = part.partition(" as ")
                        fi.imports.append(Imp(alias.strip() or orig.split("::")[-1], base + "::" + orig.strip(), None, kind="type"))
                else:
                    fi.imports.append(Imp(gd.get("alias") or module.split("::")[-1], module, None, kind="type"))
                continue
            if kind == "type" and gd.get("star"):
                fi.imports.append(Imp("*", module, "*", kind="namespace"))
                continue
            if kind == "type":
                alias = gd.get("alias") or re.split(r"[.\\]", module)[-1]
                fi.imports.append(Imp(alias, module.replace("\\", "."), None, kind="type"))
            elif kind == "namespace":
                if gd.get("alias"):
                    fi.imports.append(Imp(gd["alias"], module, None, kind="type"))
                else:
                    fi.imports.append(Imp("*", module, "*", kind="namespace"))
            else:
                alias = gd.get("alias") or ""
                fi.imports.append(Imp(alias, module, None, kind=kind))
    if spec.family == "go":
        for block in re.finditer(r"(?m)^import\s*\(([^)]*)\)", text_str):
            for m in re.finditer(r'(?m)^\s*(?:(?P<alias>[\w.]+)\s+)?"(?P<module>[^"]+)"', block.group(1)):
                fi.imports.append(Imp(m.group("alias") or m.group("module").rsplit("/", 1)[-1], m.group("module"), None, kind="package"))
        for m in re.finditer(r'(?m)^import\s+(?:(?P<alias>[\w.]+)\s+)?"(?P<module>[^"]+)"', text_str):
            fi.imports.append(Imp(m.group("alias") or m.group("module").rsplit("/", 1)[-1], m.group("module"), None, kind="package"))


def parse_generic(path: str, text: str, lang: str) -> FileInfo:
    spec = SPECS[lang]
    fi = FileInfo(path=path, lang=lang)
    fi.loc = sum(1 for ln in text.splitlines() if ln.strip())
    try:
        if spec.engine == "end":
            _parse_end(path, text, spec, fi)
        else:
            _parse_brace(path, text, spec, fi)
        # directives like #include live on lines the scanner treats as preprocessor noise
        _imports(text if spec.preprocessor else blank(text, spec, keep_strings=True), spec, fi)
    except RecursionError:
        fi.error = "file too deeply nested to scan"
    if spec.family == "go" and fi.package:
        fi.package = path.rsplit("/", 1)[0] if "/" in path else ""
    return dedupe_ids(fi)
