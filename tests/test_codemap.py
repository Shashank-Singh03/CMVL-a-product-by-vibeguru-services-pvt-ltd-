"""codemap test suite (standard library only).

Run from the project root:   python -m unittest discover -s tests -v
Optional slow test:          CODEMAP_TEST_VIDEO=1 python -m unittest discover -s tests -v
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "codemap" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from codemap_lib import MAX_VIEW, demo, pipeline  # noqa: E402
from codemap_lib.algo import louvain, workflow_order  # noqa: E402
from codemap_lib.changes import diff_texts, parse_diff  # noqa: E402
from codemap_lib.formats import classify  # noqa: E402
from codemap_lib.graph import CodeGraph  # noqa: E402
from codemap_lib.parse_generic import parse_generic  # noqa: E402
from codemap_lib.parse_js import blank, parse_js  # noqa: E402
from codemap_lib.parse_python import parse_python  # noqa: E402
from codemap_lib.risks import RiskScanner  # noqa: E402
from codemap_lib.routes import apply_routes  # noqa: E402
from codemap_lib.scan import parse_source  # noqa: E402
from codemap_lib.tools import find_browser, find_ffmpeg  # noqa: E402
from codemap_lib.util import read_json, rmtree, write_json  # noqa: E402
from codemap_lib.yamlite import parse_json, parse_yaml  # noqa: E402

HAS_GIT = shutil.which("git") is not None


def quiet(fn, *args, **kwargs):
    with redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def silent_log(*_args, **_kwargs):
    pass


def quals(fi) -> list:
    return [s.qual for s in fi.symbols]


def calls_of(fi, qual: str) -> list:
    return [".".join(c) for c, _ in next(s for s in fi.symbols if s.qual == qual).calls]


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="codemap-test-"))

    def tearDown(self):
        rmtree(self.tmp)


# ---------------------------------------------------------------- Python + JS parsers

class PythonParserTest(unittest.TestCase):
    SRC = '''"""Module doc."""
import os, json as j
from .store import JsonStore
from ..pkg import util as u


class Service(Base):
    """Does things. More detail."""
    limit: int = 3

    def __init__(self, store: JsonStore):
        self.store = store
        self.cache = Cache()

    @property
    def size(self):
        return len(self.store.all())

    @size.setter
    def size(self, value):
        pass

    def run(self):
        super().run()
        u.helper(j.dumps(1))
        self.cache.get("k")
        do_it()


def do_it():
    pass


if __name__ == "__main__":
    do_it()
'''

    def test_symbols_imports_and_calls(self):
        fi = parse_python("app/svc.py", self.SRC)
        self.assertIsNone(fi.error)
        self.assertEqual(fi.doc, "Module doc.")
        self.assertEqual(quals(fi), ["Service", "Service.__init__", "Service.size", "Service.size~2", "Service.run", "do_it"])
        self.assertEqual(len({s.id for s in fi.symbols}), len(fi.symbols), "ids must be unique")
        cls = fi.symbols[0]
        self.assertEqual(cls.doc, "Does things.")
        self.assertEqual(cls.bases, ["Base"])
        self.assertEqual(cls.attrs["store"], ("JsonStore",))
        self.assertEqual(cls.attrs["cache"], ("Cache",))
        run = next(s for s in fi.symbols if s.name == "run")
        chains = [c for c, _ in run.calls]
        self.assertIn(("super()", "run"), chains)
        self.assertIn(("u", "helper"), chains)
        self.assertIn(("self", "cache", "get"), chains)
        self.assertEqual(fi.main_calls, [("do_it",)])
        imports = {(i.alias, i.module, i.name, i.level) for i in fi.imports}
        self.assertIn(("JsonStore", "store", "JsonStore", 1), imports)
        self.assertIn(("u", "pkg", "util", 2), imports)
        self.assertIn(("j", "json", None, 0), imports)

    def test_syntax_error_is_reported_not_raised(self):
        fi = parse_python("bad.py", "def broken(:\n")
        self.assertIn("syntax error", fi.error)


class JsParserTest(unittest.TestCase):
    SRC = r'''import { fetchNotes, createNote as mk } from "./api.js";
import * as R from "./render";
const re = /["'`{]/g;
const tpl = `a ${ {x: 1}.x } } b`;
/** The app.
 * @param root element */
export class NotesApp extends Base {
  static count = 0;
  handle = (e) => { this.draw(); };
  constructor(root) { super(root); this.root = root; this.store = new Store(); }
  async start() { this.notes = await fetchNotes(); this.#load(); }
  #load() { return 1; }
  load() { return 2; }
  get size() { return this.notes.length; }
  draw() { this.root.innerHTML = R.renderList(this.notes); this.store.save(); }
}
export const add = (a, b) => a + b;
export async function load(url: string): Promise<Note[]> { return <List items={await mk(url)} />; }
module.exports.thing = function () { add(1, 2); };
main();
'''

    def test_blank_keeps_offsets(self):
        out = blank(self.SRC, keep_strings=False)
        self.assertEqual(len(out), len(self.SRC))
        self.assertEqual(out.count("\n"), self.SRC.count("\n"))
        self.assertNotIn("The app", out)            # comments removed
        self.assertNotIn('"./api.js"'[1:-1], out)   # string contents removed
        kept = blank(self.SRC, keep_strings=True)
        self.assertIn("./api.js", kept)

    def test_structure(self):
        fi = parse_js("web/app.tsx", self.SRC)
        for q in ["NotesApp", "NotesApp.handle", "NotesApp.constructor", "NotesApp.start", "NotesApp.#load",
                  "NotesApp.load", "NotesApp.size", "NotesApp.draw", "add", "load", "thing"]:
            self.assertIn(q, quals(fi))
        self.assertEqual(len({s.id for s in fi.symbols}), len(fi.symbols))
        cls = fi.symbols[0]
        self.assertEqual(cls.doc, "The app.")
        self.assertEqual(cls.line, 5, "JSDoc belongs to the symbol span")
        self.assertEqual(cls.bases, ["Base"])
        self.assertEqual(cls.attrs.get("store"), ("Store",))
        start = next(s for s in fi.symbols if s.qual == "NotesApp.start")
        self.assertIn(("this", "#load"), [c for c, _ in start.calls])
        load_fn = next(s for s in fi.symbols if s.qual == "load")
        self.assertEqual(load_fn.signature, "export async function load(url: string): Promise<Note[]>")
        self.assertIn(("List",), [c for c, _ in load_fn.calls], "JSX tags count as calls")
        thing = next(s for s in fi.symbols if s.qual == "thing")
        self.assertTrue(thing.exported)
        self.assertIn(("main",), fi.main_calls)
        imports = {(i.alias, i.module, i.name) for i in fi.imports}
        self.assertIn(("mk", "./api.js", "createNote"), imports)
        self.assertIn(("R", "./render", None), imports)


# ---------------------------------------------------------------- every other language (generic engine)

class GenericLanguageTest(unittest.TestCase):
    """One engine, configured per language: brace languages and `end` languages."""

    def test_java(self):
        src = r'''package com.notes.stats;

import com.notes.store.NoteRepository;
import java.util.*;

/** Computes statistics about notes. */
@Service
public class StatsService extends BaseService implements Reporter {
    private final NoteRepository repo;
    private static final String SEP = "}{";

    public StatsService(NoteRepository repo) {
        this.repo = repo;
    }

    /** Count words across all notes. */
    public int countWords() {
        int total = 0;
        for (Note n : repo.findAll()) { total += split(n.getBody()).length; }
        return total;
    }

    @GetMapping("/stats/tags")
    public Map<String, Integer> delete(String prefix) throws IOException {
        return new HashMap<>();
    }

    private String[] split(String s) { return s.split(" "); }
}
'''
        fi = parse_generic("src/main/java/com/notes/StatsService.java", src, "java")
        self.assertEqual(fi.package, "com.notes.stats")
        self.assertEqual(quals(fi), ["StatsService", "StatsService.StatsService", "StatsService.countWords",
                                     "StatsService.delete", "StatsService.split"])
        cls = fi.symbols[0]
        self.assertEqual(cls.doc, "Computes statistics about notes.")
        self.assertEqual(cls.bases, ["BaseService", "Reporter"])
        self.assertEqual(cls.attrs["repo"], ("NoteRepository",))
        self.assertIn("@Service", cls.decorators)
        self.assertIn("repo.findAll", calls_of(fi, "StatsService.countWords"))
        self.assertIn("split", calls_of(fi, "StatsService.countWords"))
        self.assertEqual(fi.symbols[0].end, 29, "a brace inside a string must not end the class")
        imports = {(i.alias, i.module, i.kind) for i in fi.imports}
        self.assertIn(("NoteRepository", "com.notes.store.NoteRepository", "type"), imports)
        self.assertIn(("*", "java.util", "namespace"), imports)

    def test_go_receivers_attach_to_their_type(self):
        src = '''package store

import (
    "encoding/json"
    "github.com/acme/notes/internal/cache"
)

// JsonStore keeps notes in a file.
type JsonStore struct {
    path  string
    cache *cache.LRU
}

// New creates a store.
func New(path string) *JsonStore {
    return &JsonStore{path: path, cache: cache.NewLRU(128)}
}

func (s *JsonStore) Save(n Note) error {
    data, _ := json.Marshal(n)
    s.cache.Invalidate()
    return write(data)
}
'''
        fi = parse_generic("internal/store/store.go", src, "go")
        self.assertEqual(quals(fi), ["JsonStore", "JsonStore.Save", "New"])
        self.assertEqual(fi.symbols[0].attrs["cache"], ("LRU",))
        self.assertIn("this.cache.Invalidate", calls_of(fi, "JsonStore.Save"))
        self.assertIn("cache.NewLRU", calls_of(fi, "New"))
        self.assertIn(("cache", "github.com/acme/notes/internal/cache", "package"),
                      {(i.alias, i.module, i.kind) for i in fi.imports})

    def test_rust_impl_blocks_merge_into_the_struct(self):
        src = '''use crate::cache::{Cache, Lru as L};

/// A JSON store.
pub struct JsonStore<'a> {
    path: &'a str,
    cache: Box<Cache>,
}

impl<'a> JsonStore<'a> {
    pub fn save(&mut self, note: &Note) -> Result<(), Error> {
        let s = r#"{"}"}"#;
        self.cache.invalidate();
        helper(self.path)
    }
}

impl Store for JsonStore<'_> {
    fn load(&self) -> Vec<Note> { helper(self.path) }
}

fn helper(p: &str) -> Vec<Note> { vec![] }
'''
        fi = parse_generic("src/store.rs", src, "rust")
        self.assertEqual(quals(fi), ["JsonStore", "JsonStore.save", "JsonStore.load", "helper"])
        self.assertEqual(fi.symbols[0].bases, ["Store"])
        self.assertIn("this.cache.invalidate", calls_of(fi, "JsonStore.save"))
        self.assertIn(("L", "crate::cache::Lru"), {(i.alias, i.module) for i in fi.imports})

    def test_csharp_attributes_and_namespaces(self):
        src = '''using Notes.Services;

namespace Notes.Api
{
    /// <summary>HTTP API for notes.</summary>
    [ApiController]
    public class NotesController : ControllerBase
    {
        private readonly INoteService _service;

        [HttpDelete("{id}")]
        public IActionResult Delete(string id) => _service.Delete(id) ? NoContent() : NotFound();
    }
}
'''
        fi = parse_generic("Api/NotesController.cs", src, "csharp")
        self.assertEqual(fi.package, "Notes.Api")
        self.assertEqual(quals(fi), ["NotesController", "NotesController.Delete"])
        self.assertEqual(fi.symbols[0].doc, "HTTP API for notes.")
        self.assertEqual(fi.symbols[0].attrs["_service"], ("INoteService",))
        self.assertIn('[HttpDelete("{id}")]', fi.symbols[1].decorators)
        self.assertIn("_service.Delete", calls_of(fi, "NotesController.Delete"))

    def test_ruby_end_blocks(self):
        src = '''require_relative "store"

# Business rules for notes.
class NoteService < BaseService
  def initialize(store)
    @store = store
  end

  def delete(id)
    if @store.delete(id)
      notify(id)
    end
  end
end
'''
        fi = parse_generic("app/services/note_service.rb", src, "ruby")
        self.assertEqual(quals(fi), ["NoteService", "NoteService.initialize", "NoteService.delete"])
        self.assertEqual(fi.symbols[0].bases, ["BaseService"])
        self.assertEqual((fi.symbols[2].line, fi.symbols[2].end), (9, 13))
        self.assertIn("this.store.delete", calls_of(fi, "NoteService.delete"))

    def test_shell_functions_and_sourced_files(self):
        src = '''#!/usr/bin/env bash
source ./scripts/lib.sh

# Upload a snapshot.
upload() {
  aws s3 cp "$1" "s3://bucket/"
}

main() { check_env; upload notes.json; }
main "$@"
'''
        fi = parse_generic("scripts/backup.sh", src, "shell")
        self.assertEqual(quals(fi), ["upload", "main"])
        self.assertEqual(fi.symbols[0].doc, "Upload a snapshot.")
        self.assertIn("upload", calls_of(fi, "main"))
        self.assertIn(("main",), fi.main_calls)

    def test_many_languages_parse_without_errors(self):
        samples = {
            "kotlin": ("a.kt", "class A(val repo: Repo) {\n  fun add(x: Int): Int {\n    return repo.save(x)\n  }\n}\n"),
            "scala": ("a.scala", "object A {\n  def add(x: Int): Int = {\n    x + 1\n  }\n}\n"),
            "swift": ("a.swift", "class A {\n  func add(x: Int) -> Int {\n    return x + 1\n  }\n}\n"),
            "php": ("a.php", "<?php\nclass A {\n  public function add($x) {\n    return $this->b($x);\n  }\n}\n"),
            "cpp": ("a.cpp", "class A {\n public:\n  int add(int x);\n};\nint A::add(int x) {\n  return x + 1;\n}\n"),
            "c": ("a.c", "#include \"a.h\"\nstatic int add(int x) {\n  return helper(x);\n}\n"),
            "dart": ("a.dart", "class A {\n  int add(int x) {\n    return x + 1;\n  }\n}\n"),
            "lua": ("a.lua", "local function add(x)\n  return helper(x)\nend\n"),
            "elixir": ("a.ex", "defmodule A do\n  def add(x) do\n    helper(x)\n  end\nend\n"),
            "powershell": ("a.ps1", "function Add-Item {\n  param($x)\n  Write-Output $x\n}\n"),
            "solidity": ("a.sol", "contract Vault {\n  function withdraw(uint amount) public {\n    send(amount);\n  }\n}\n"),
            "r": ("a.r", "add <- function(x) {\n  helper(x)\n}\n"),
        }
        for lang, (path, src) in samples.items():
            with self.subTest(lang=lang):
                fi = parse_source(path, src)
                self.assertEqual(fi.lang, lang)
                self.assertIsNone(fi.error)
                self.assertTrue(any(s.kind in ("function", "method") for s in fi.symbols), quals(fi))
        cpp = parse_source("a.cpp", samples["cpp"][1])
        self.assertEqual(quals(cpp), ["A", "A.add"], "an out-of-class definition replaces the declaration")
        self.assertEqual(cpp.symbols[1].line, 5)


# ---------------------------------------------------------------- YAML/JSON reader + formats

class YamliteTest(unittest.TestCase):
    def test_yaml_documents_with_line_numbers(self):
        docs = parse_yaml("a: 1\nlist:\n  - x\n  - {k: v, n: [1, 2]}\ntext: |\n  hello\n  world\n---\nb: 'two # not a comment'\n")
        self.assertEqual(len(docs), 2)
        first = docs[0]
        self.assertEqual(first.at("a").py(), 1)
        self.assertEqual(first.at("a").line, 1)
        self.assertEqual(first.at("list").py(), ["x", {"k": "v", "n": [1, 2]}])
        self.assertEqual(first.at("text").text().rstrip(), "hello\nworld")
        self.assertEqual(docs[1].at("b").text(), "two # not a comment")
        self.assertEqual(docs[1].at("b").line, 9)

    def test_json_with_line_numbers(self):
        node = parse_json('{\n  "name": "web",\n  "dependencies": {\n    "react": "^18.3.1"\n  }\n}\n')
        self.assertEqual(node.at("dependencies", "react").text(), "^18.3.1")
        self.assertEqual(node.at("dependencies", "react").line, 4)


class FormatsTest(unittest.TestCase):
    def test_classify(self):
        cases = {
            ("deploy/k8s.yaml", "apiVersion: apps/v1\nkind: Deployment\n"): ("kubernetes", "infra"),
            ("chart/templates/dep.yaml", "apiVersion: apps/v1\nkind: Deployment\n"): ("kubernetes", "infra"),
            ("api/spec.yaml", "openapi: 3.0.0\n"): ("openapi", "api"),
            ("template.yaml", "AWSTemplateFormatVersion: '2010-09-09'\n"): ("cloudformation", "infra"),
            (".github/workflows/ci.yml", "on: push\n"): ("github-actions", "ci"),
            (".gitlab-ci.yml", ""): ("gitlab-ci", "ci"),
            ("Jenkinsfile", ""): ("jenkins", "ci"),
            ("docker-compose.yml", ""): ("compose", "infra"),
            ("Dockerfile", ""): ("dockerfile", "infra"),
            ("infra/main.tf", ""): ("terraform", "infra"),
            ("main.bicep", ""): ("bicep", "infra"),
            ("schema.graphql", ""): ("graphql", "api"),
            ("notes.proto", ""): ("proto", "api"),
            ("db/001_init.sql", ""): ("sql", "data"),
            ("package.json", "{}"): ("deps-npm", "deps"),
            ("pom.xml", ""): ("deps-maven", "deps"),
            ("go.mod", ""): ("deps-gomod", "deps"),
            ("train.ipynb", ""): ("notebook", "ai"),
            ("src/Main.java", ""): ("java", "code"),
            ("README.md", ""): ("markdown", "docs"),
            ("notes.txt", ""): ("text", "other"),
            ("package-lock.json", ""): (None, None),
        }
        for (path, head), want in cases.items():
            with self.subTest(path=path):
                self.assertEqual(classify(path, head), want)


# ---------------------------------------------------------------- DevOps / cloud / API / data parsers

class ConfigParserTest(unittest.TestCase):
    def syms(self, path, text):
        fi = parse_source(path, text)
        self.assertIsNone(fi.error)
        return fi, {s.qual: s for s in fi.symbols}

    def test_kubernetes_service_selects_deployment(self):
        _, s = self.syms("deploy/k8s.yaml", """apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  template:
    metadata:
      labels:
        app: api
    spec:
      containers:
        - name: api
          image: acme/api:1.0
---
apiVersion: v1
kind: Service
metadata:
  name: api
spec:
  selector:
    app: api
""")
        dep, svc = s["Deployment/api"], s["Service/api"]
        self.assertEqual((dep.sub, dep.line, dep.end), ("deployment", 1, 13))
        self.assertIn("acme/api:1.0", dep.doc)
        label_key = next(k for k in dep.keys if "label:app=api" in k)
        self.assertIn(label_key, [k for k, _ in svc.refs])

    def test_terraform_references(self):
        _, s = self.syms("infra/main.tf", '''resource "aws_s3_bucket" "logs" {
  bucket = "logs"
}

module "vpc" {
  source = "./modules/vpc"
}

output "bucket" {
  value = aws_s3_bucket.logs.bucket
}
''')
        self.assertEqual(set(s), {"aws_s3_bucket.logs", "module.vpc", "output.bucket"})
        self.assertIn(s["aws_s3_bucket.logs"].keys[0], [k for k, _ in s["output.bucket"].refs])
        self.assertIn("tfdir:infra/modules/vpc", [k for k, _ in s["module.vpc"].refs])

    def test_openapi_endpoints_and_schemas(self):
        fi, s = self.syms("api/openapi.yaml", """openapi: 3.0.0
paths:
  /notes/{id}:
    delete:
      summary: Delete a note
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Note"
components:
  schemas:
    Note:
      properties:
        id:
          type: string
""")
        self.assertEqual(fi.domain, "api")
        op = s["Endpoints.DELETE /notes/{id}"]
        self.assertEqual((op.sub, op.doc, op.routes), ("endpoint", "Delete a note", ["DELETE /notes/{}"]))
        self.assertIn("oas:api/openapi.yaml:schema:Note", [k for k, _ in op.refs])
        self.assertEqual(s["Schemas.Note"].attrs, {"id": ("string",)})

    def test_github_actions_jobs(self):
        _, s = self.syms(".github/workflows/ci.yml", """on:
  push:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: ./scripts/test.sh
  deploy:
    needs: [test]
    steps:
      - run: echo deploy
""")
        self.assertEqual(set(s), {"on", "test", "deploy"})
        self.assertIn("file:scripts/test.sh", [k for k, _ in s["test"].refs])
        self.assertIn(s["test"].keys[0], [k for k, _ in s["deploy"].refs])

    def test_compose_dockerfile_and_dependencies(self):
        _, compose = self.syms("docker-compose.yml",
                               "services:\n  api:\n    build: .\n  web:\n    image: nginx:1.27\n    depends_on:\n      - api\n")
        self.assertIn(compose["api"].keys[0], [k for k, _ in compose["web"].refs])
        self.assertIn("dockerfile:.", [k for k, _ in compose["api"].refs])
        _, docker = self.syms("Dockerfile", "FROM python:3.12-slim AS build\nRUN make\nFROM python:3.12-slim\n"
                                            "COPY --from=build /app /app\nUSER app\n")
        self.assertEqual(set(docker), {"build", "stage 2"})
        self.assertIn("runs as app", docker["stage 2"].doc)
        _, npm = self.syms("web/package.json", '{\n  "scripts": {"build": "vite build"},\n'
                                               '  "dependencies": {"react": "^18.3.1"},\n  "devDependencies": {"vite": "5.4.0"}\n}\n')
        self.assertEqual({q: s.sub for q, s in npm.items()},
                         {"npm run build": "npm script", "react": "package", "vite": "dev package"})
        _, pip = self.syms("requirements.txt", "requests==2.32.3\nboto3>=1.35\n# comment\n")
        self.assertEqual(set(pip), {"requests", "boto3"})

    def test_sql_tables_and_migrations(self):
        _, s = self.syms("db/001.sql", "CREATE TABLE notes (\n  id TEXT PRIMARY KEY,\n  title TEXT NOT NULL\n);\n"
                                       "CREATE INDEX notes_title ON notes(title);\n")
        self.assertEqual(s["notes"].sub, "table")
        self.assertEqual(set(s["notes"].attrs), {"id", "title"})
        self.assertIn("sql:table:notes", [k for k, _ in s["notes_title"].refs])


# ---------------------------------------------------------------- linking across languages

class RoutesTest(unittest.TestCase):
    SRC = {
        "server/main.py": '@app.get("/items/{item_id}")\ndef read_item(item_id: int):\n    return {}\n\n\n'
                          '@app.delete("/items/{item_id}")\ndef drop_item(item_id: int):\n    return None\n',
        "web/client.js": 'const BASE = "/items";\nexport async function loadItem(id) {\n  return fetch(`${BASE}/${id}`);\n}\n'
                         'export async function removeItem(id) {\n  return fetch(`/items/${id}`, { method: "DELETE" });\n}\n',
        "api/openapi.yaml": "openapi: 3.0.0\npaths:\n  /items/{id}:\n    get:\n      summary: Read\n    delete:\n      summary: Remove\n",
        "src/main/java/com/acme/ItemController.java": 'package com.acme;\n\n@RestController\n@RequestMapping("/v2/items")\n'
                                                      'public class ItemController {\n    @GetMapping("/{id}")\n'
                                                      '    public Item get(@PathVariable String id) {\n        return null;\n    }\n}\n',
    }

    def test_client_contract_and_handler_are_linked(self):
        files = {p: parse_source(p, t) for p, t in self.SRC.items()}
        self.assertEqual(apply_routes(files, self.SRC), 3)
        java = next(s for s in files["src/main/java/com/acme/ItemController.java"].symbols if s.name == "get")
        self.assertEqual(java.routes, ["GET /v2/items/{}"], "class-level prefix + method mapping")
        edges = CodeGraph(files).build().edges
        self.assertIn(("f:web/client.js#loadItem", "f:server/main.py#read_item"), edges)
        self.assertIn(("f:web/client.js#removeItem", "f:server/main.py#drop_item"), edges)
        self.assertIn(("f:web/client.js#removeItem", "f:api/openapi.yaml#Endpoints.DELETE /items/{id}"), edges)
        self.assertIn(("f:api/openapi.yaml#Endpoints.GET /items/{id}", "f:server/main.py#read_item"), edges)


# ---------------------------------------------------------------- security / risk lens

class RiskTest(unittest.TestCase):
    def scan(self, path, text):
        files = {path: parse_source(path, text)}
        scanner = RiskScanner()
        scanner.scan_files(files, {path: text})
        return scanner.flags

    def test_secrets_are_flagged_and_masked(self):
        flags = self.scan("settings.py", 'DEBUG = True\nAPI_KEY = "abcd1234efgh5678"\nPASSWORD = "changeme"\n'
                                         'AWS = "AKIAABCDEFGHIJKLMNOP"\nTOKEN = os.environ["TOKEN"]\n')
        rules = {f["rule"]: f for f in flags}
        self.assertEqual(set(rules), {"code.debug_enabled", "secret.hardcoded", "secret.aws_key"},
                         "placeholders and environment lookups are not secrets")
        blob = json.dumps(flags)
        self.assertNotIn("abcd1234efgh5678", blob)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", blob)
        self.assertEqual(rules["secret.aws_key"]["sev"], "high")

    def test_infra_rules(self):
        flags = self.scan("deploy/pod.yaml", "apiVersion: v1\nkind: Pod\nmetadata:\n  name: x\nspec:\n  hostNetwork: true\n"
                                             "  containers:\n    - name: x\n      securityContext:\n        privileged: true\n")
        self.assertEqual({f["rule"] for f in flags}, {"k8s.privileged", "k8s.host_access"})
        tf = self.scan("infra/s3.tf", 'resource "aws_security_group_rule" "ssh" {\n  cidr_blocks = ["0.0.0.0/0"]\n}\n')
        self.assertEqual([f["rule"] for f in tf], ["net.open_to_internet"])

    def test_rules_respect_domains(self):
        self.assertEqual(self.scan("docs/guide.md", "Never set `privileged: true` in production.\n"
                                                    "Install: curl -sSL https://get.example.sh | sh\n"), [],
                         "only secret checks run on prose")

    def test_open_network_needs_an_inbound_rule(self):
        tf = '''resource "aws_route" "out" {
  destination_cidr_block = "0.0.0.0/0"
}
resource "aws_network_acl_rule" "all" {
  cidr_block = "0.0.0.0/0"
}
resource "aws_security_group" "web" {
  egress {
    from_port   = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 22
    cidr_blocks = ["0.0.0.0/0"]
  }
}
'''
        self.assertEqual([(f["rule"], f["line"]) for f in self.scan("infra/net.tf", tf)], [("net.open_to_internet", 14)])
        policy = ("apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata:\n  name: p\nspec:\n  egress:\n"
                  "    - to:\n        - ipBlock:\n            cidr: 0.0.0.0/0\n  ingress:\n    - from:\n        - ipBlock:\n"
                  "            cidr: 0.0.0.0/0\n")
        self.assertEqual([f["line"] for f in self.scan("deploy/policy.yaml", policy)], [13])

    def test_reset_scripts_comments_and_hints_are_not_findings(self):
        self.assertEqual(self.scan("db/schema.sql", "DROP TABLE notes IF EXISTS;\nCREATE TABLE notes (\n  id INT\n);\n"), [])
        self.assertEqual([f["rule"] for f in self.scan("db/002.sql", "DROP TABLE users;\n")], ["data.destructive"])
        self.assertEqual(self.scan("app/run.py", "# os.system(cmd) was here\n# truncate the title to 80 chars\n"), [])
        self.assertEqual(self.scan("Makefile", 'lint:\n\t$(warning "run: curl -sfL https://x.sh/install | sh")\n'), [])
        self.assertEqual([f["rule"] for f in self.scan("app/run.py", "os.system(cmd)\n")], ["code.shell_injection"])

    def test_masking_keeps_the_key_name(self):
        flags = self.scan("Web/Startup.cs", 'options.ClientSecret = "supersecretvalue";\n')
        self.assertEqual(flags[0]["rule"], "secret.hardcoded")
        self.assertIn('ClientSecret = "[REDACTED]"', flags[0]["text"])
        self.assertNotIn("supersecretvalue", flags[0]["text"])


# ---------------------------------------------------------------- graph + algorithms

class GraphTest(unittest.TestCase):
    def test_cross_file_resolution(self):
        store = parse_python("app/store.py", "class JsonStore:\n    def save(self):\n        pass\n")
        svc = parse_python("app/service.py",
                           "from .store import JsonStore\n\nclass Service:\n"
                           "    def __init__(self, store: JsonStore):\n        self.store = store\n"
                           "    def add(self):\n        self.store.save()\n")
        api_js = parse_js("web/api.js", "export function getNotes() { return 1; }\nexport default function boot() {}\n")
        app_js = parse_js("web/app.js", "import boot, { getNotes } from './api.js';\nexport function start() { getNotes(); boot(); }\n")
        g = CodeGraph({f.path: f for f in (store, svc, api_js, app_js)}).build()
        self.assertIn(("f:app/service.py#Service.add", "f:app/store.py#JsonStore.save"), g.edges)
        self.assertIn(("f:web/app.js#start", "f:web/api.js#getNotes"), g.edges)
        self.assertIn(("f:web/app.js#start", "f:web/api.js#boot"), g.edges)

    def test_java_cross_file_resolution(self):
        repo = parse_source("src/main/java/com/acme/store/Repo.java",
                            "package com.acme.store;\n\npublic class Repo {\n    public void save(Note n) {\n    }\n}\n")
        svc = parse_source("src/main/java/com/acme/app/Service.java",
                           "package com.acme.app;\n\nimport com.acme.store.Repo;\n\npublic class Service {\n"
                           "    private final Repo repo;\n    public void add(Note n) {\n        repo.save(n);\n    }\n}\n")
        g = CodeGraph({f.path: f for f in (repo, svc)}).build()
        self.assertIn(("f:src/main/java/com/acme/app/Service.java#Service.add",
                       "f:src/main/java/com/acme/store/Repo.java#Repo.save"), g.edges)

    def test_louvain_finds_two_cliques(self):
        a, b = ["a1", "a2", "a3"], ["b1", "b2", "b3"]
        w = {}
        for grp in (a, b):
            for i in grp:
                for j in grp:
                    if i < j:
                        w[(i, j)] = 1.0
        w[("a1", "b1")] = 0.1
        comms = louvain(a + b, w)
        self.assertEqual(sorted(sorted(c) for c in comms), [a, b])

    def test_workflow_order_puts_callers_first(self):
        order = workflow_order(["store", "api", "cli", "service"],
                               [("cli", "api"), ("api", "service"), ("service", "store")], priority=lambda x: x)
        self.assertEqual(order, ["cli", "api", "service", "store"])

    def test_diff_texts_matches_git_numbering(self):
        hunks = diff_texts("a\nb\nc\n", "a\nc\nd\n")
        self.assertEqual([(h.old_start, h.old_len, h.new_start, h.new_len) for h in hunks], [(2, 1, 1, 0), (3, 0, 3, 1)])

    def test_parse_git_diff(self):
        text = ("diff --git a/x.py b/x.py\nindex 1..2 100644\n--- a/x.py\n+++ b/x.py\n"
                "@@ -2 +2,2 @@\n-old\n+new\n+more\n\\ No newline at end of file\n")
        fd = parse_diff(text)["x.py"]
        self.assertEqual((fd.status, fd.add, fd.dele), ("modified", 2, 1))
        self.assertEqual(fd.hunks[0].plus, [(2, "new"), (3, "more")])


# ---------------------------------------------------------------- end to end (polyglot demo)

@unittest.skipUnless(HAS_GIT, "git not installed")
class DemoEndToEndTest(TempDirCase):
    def test_free_then_labeled(self):
        root, uses_git = demo.create(self.tmp / "demo")
        self.assertTrue(uses_git)
        demo.apply_agent_changes(root)
        ir = pipeline.analyze(root, tier="free", log=silent_log)
        out = root / ".codemap"
        for name in ("codemap.md", "codemap.html", "ir.json", ".gitignore"):
            self.assertTrue((out / name).exists(), name)
        statuses = {}
        for n in ir["nodes"].values():
            if n["kind"] in ("class", "function", "method"):
                own = n.get("own_status", n["status"]) if n["kind"] == "class" else n["status"]
                statuses[own] = statuses.get(own, 0) + 1
        self.assertEqual((statuses.get("added"), statuses.get("modified"), statuses.get("deleted")), (22, 15, 1))
        for v in ir["views"]:
            self.assertLessEqual(len(v["children"]), MAX_VIEW, v["id"])
        nodes = ir["nodes"]
        self.assertEqual(nodes["f:app/service.py#NoteService.legacy_export"]["status"], "deleted")
        self.assertEqual(nodes["c:app/cache.py#LRUCache"]["status"], "added")
        self.assertIn("```mermaid", (out / "codemap.md").read_text(encoding="utf-8"))
        self.assertFalse((out / "worksheet.md").exists(), "free tier writes no worksheet")

        # one map for every kind of change: the top level is split by domain
        self.assertTrue({"code", "api", "infra", "ci", "deps", "docs"} <= set(ir["stats"]["domains"]))
        self.assertEqual(ir["views"][0]["children"][:3], ["g:@code", "g:@api", "g:@infra"])
        # Java and JavaScript are first-class: the browser's delete call reaches the Python handler and the contract
        self.assertEqual(nodes["f:services/stats/src/main/java/com/notes/stats/StatsService.java#StatsService.countByTag"]["status"],
                         "added")
        callees = [c for c, _ in nodes["f:web/api.js#deleteNote"]["calls"]]
        self.assertIn("f:app/api.py#NotesAPI.delete", callees)
        self.assertIn("f:api/openapi.yaml#Endpoints.DELETE /notes/{id}", callees)
        # risk lens
        self.assertEqual(ir["stats"]["risks"], {"high": 2, "medium": 3, "low": 2})
        self.assertEqual({f["rule"] for f in ir["risks"]},
                         {"k8s.privileged", "storage.public", "ci.unpinned_action", "api.removed_field",
                          "iam.service_wildcard", "iam.any_resource", "deps.new"})
        self.assertIn("## Risk flags", (out / "codemap.md").read_text(encoding="utf-8"))
        # a file nothing can parse is still shown, with its text, for the agent to describe
        runbook = nodes["m:docs/runbook.txt"]
        self.assertTrue(runbook["opaque"])
        self.assertEqual((runbook["status"], runbook["add"]), ("added", 4))
        self.assertTrue(runbook["diff"][0].startswith("+RUNBOOK"))

        quiet(pipeline.analyze, root, tier="video")
        sheet = (out / "worksheet.md").read_text(encoding="utf-8")
        self.assertIn("## Narration beats", sheet)
        self.assertIn("## Risk flags", sheet)
        self.assertIn("+RUNBOOK", sheet)
        self.assertLess(len(sheet) / 3.6, 6500, "demo worksheet should stay small")
        write_json(out / "labels.json", demo.sample_labels(ir))
        quiet(pipeline.render, out)
        ir2 = read_json(out / "ir.json")
        self.assertEqual(ir2["nodes"]["c:app/cache.py#LRUCache"]["title"], "Recent-results cache")
        self.assertEqual(ir2["why"][0]["source"], "agent")
        self.assertIn("why", [b["kind"] for b in ir2["beats"]])
        self.assertIn("risks", [b["kind"] for b in ir2["beats"]])
        self.assertTrue(all(f.get("note") for f in ir2["risks"]), "the agent confirmed every risk flag")
        self.assertIn("Recent-results cache", (out / "codemap.html").read_text(encoding="utf-8"))
        cache = read_json(out / "cache.json")
        self.assertGreater(len(cache), 40, "agent labels are cached by content hash")

        # a re-run reuses cached labels, so the worksheet shrinks to (almost) nothing
        quiet(pipeline.analyze, root, tier="full")
        ir3 = read_json(out / "ir.json")
        self.assertEqual(ir3["nodes"]["c:app/cache.py#LRUCache"]["lsrc"], "cache")

    def test_clean_tree_and_base(self):
        root, _ = demo.create(self.tmp / "demo")
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "-c", "commit.gpgsign=false",
               "-c", "core.autocrlf=false"]
        subprocess.run(git + ["checkout", "-q", "-b", "feature"], cwd=root, check=True, capture_output=True)
        demo.apply_agent_changes(root)
        subprocess.run(git + ["add", "-A"], cwd=root, check=True, capture_output=True)
        subprocess.run(git + ["commit", "-q", "-m", "Add note deletion and a read cache"], cwd=root, check=True,
                       capture_output=True)
        ir = pipeline.analyze(root, log=silent_log)
        self.assertTrue(ir["scope"]["label"].startswith("last commit"))
        self.assertEqual(ir["why"][0]["text"], "Add note deletion and a read cache")
        first = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
        ir = pipeline.analyze(root, base=first, log=silent_log)
        self.assertEqual(ir["scope"]["files"], 20)
        with self.assertRaises(pipeline.CodemapError):
            pipeline.analyze(root, base="does-not-exist", log=silent_log)

    def test_deep_folder(self):
        """Old file versions must load even when folder + commit + path passes Windows' 260-character limit."""
        deep = self.tmp / ("deep-" + "d" * (175 - len(str(self.tmp)))) / "demo"
        root, _ = demo.create(deep)
        demo.apply_agent_changes(root)
        ir = pipeline.analyze(root, tier="free", log=silent_log)
        java = "f:services/stats/src/main/java/com/notes/stats/StatsService.java#StatsService.countByTag"
        self.assertEqual(ir["nodes"][java]["status"], "added")

    def test_same_input_same_map(self):
        """Python randomizes set order per process; the map (and so the label cache) must not depend on it."""
        root, _ = demo.create(self.tmp / "demo")
        demo.apply_agent_changes(root)
        digests = set()
        for seed in ("1", "6"):
            out = self.tmp / f"out-{seed}"
            env = dict(os.environ, PYTHONHASHSEED=seed)
            subprocess.run([sys.executable, str(SCRIPTS / "codemap.py"), "analyze", str(root), "--tier", "video",
                            "--out", str(out)], check=True, capture_output=True, env=env)
            ir = read_json(out / "ir.json")
            ir.pop("generated")
            ir["stats"].pop("parse_seconds")
            digests.add(json.dumps(ir, sort_keys=True) + (out / "worksheet.md").read_text(encoding="utf-8"))
        self.assertEqual(len(digests), 1)

    def test_repo_audit(self):
        root, _ = demo.create(self.tmp / "demo")
        demo.apply_agent_changes(root)
        ir = pipeline.analyze(root, repo_mode=True, audit=True, log=silent_log)
        self.assertEqual(ir["scope"]["mode"], "repo")
        rules = {f["rule"] for f in ir["risks"]}
        self.assertTrue({"k8s.privileged", "storage.public"} <= rules, rules)
        self.assertFalse(any(f["sev"] == "low" for f in ir["risks"]), "audit mode reports medium and high only")


class SnapshotModeTest(TempDirCase):
    """No git at all: `mark` takes a file snapshot, `analyze` diffs against it."""

    def test_mark_then_analyze(self):
        proj = self.tmp / "plain"
        (proj / "pkg").mkdir(parents=True)
        (proj / "pkg" / "a.py").write_text("def one():\n    return 1\n\n\ndef two():\n    return one()\n", encoding="utf-8")
        (proj / "pkg" / "b.py").write_text("from .a import two\n\n\ndef three():\n    return two()\n", encoding="utf-8")
        out = self.tmp / "out"
        quiet(pipeline.mark, proj, out=out)
        (proj / "pkg" / "a.py").write_text("def one():\n    return 11\n\n\ndef four():\n    return 4\n", encoding="utf-8")
        (proj / "pkg" / "c.py").write_text("def five():\n    return 5\n", encoding="utf-8")
        ir = pipeline.analyze(proj, out=out, tier="free", log=silent_log)
        nodes = ir["nodes"]
        self.assertEqual(ir["scope"]["mode"], "diff")
        self.assertEqual(nodes["f:pkg/a.py#one"]["status"], "modified")
        self.assertEqual(nodes["f:pkg/a.py#two"]["status"], "deleted")
        self.assertEqual(nodes["f:pkg/a.py#four"]["status"], "added")
        self.assertEqual(nodes["f:pkg/c.py#five"]["status"], "added")
        quiet(pipeline.mark, proj, out=out, clear=True)
        ir = pipeline.analyze(proj, out=out, log=silent_log)
        self.assertEqual(ir["scope"]["mode"], "repo")


class CliTest(unittest.TestCase):
    def test_version_and_doctor(self):
        script = SCRIPTS / "codemap.py"
        ver = subprocess.run([sys.executable, str(script), "--version"], capture_output=True, text=True)
        self.assertEqual(ver.returncode, 0)
        self.assertIn("codemap", ver.stdout)
        doc = subprocess.run([sys.executable, str(script), "doctor"], capture_output=True, text=True)
        self.assertEqual(doc.returncode, 0)
        self.assertIn("python", doc.stdout)
        self.assertIn("piper", doc.stdout)

    def test_errors_are_one_line(self):
        script = SCRIPTS / "codemap.py"
        res = subprocess.run([sys.executable, str(script), "render", "--out", tempfile.gettempdir() + "/codemap-nothing-here"],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertEqual(len(res.stderr.strip().splitlines()), 1)

    def test_bad_tts_value_is_rejected(self):
        script = SCRIPTS / "codemap.py"
        res = subprocess.run([sys.executable, str(script), "video", "--tts", "loud"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertIn("cmd:<command>", res.stderr)


def _probe_streams(path: Path) -> list:
    """Codec names in a media file, read from ffmpeg's banner (ffprobe is not always installed)."""
    proc = subprocess.run([find_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    return [line.split(":", 3)[-1].strip().split()[0].rstrip(",")
            for line in proc.stderr.splitlines() if "Stream #" in line]


@unittest.skipUnless(os.environ.get("CODEMAP_TEST_VIDEO") and HAS_GIT and find_browser() and find_ffmpeg(),
                     "set CODEMAP_TEST_VIDEO=1 (needs git, ffmpeg and Chrome/Edge)")
class VideoTest(TempDirCase):
    def test_mp4_with_audio_webm_and_embedded_narration(self):
        root, _ = demo.create(self.tmp / "demo")
        demo.apply_agent_changes(root)
        quiet(pipeline.analyze, root, tier="free")
        out = root / ".codemap"
        # a tiny stand-in voice keeps the test fast and identical on every OS
        tone = sys.executable.replace("\\", "/")
        cmd = (f'cmd:"{tone}" -c "import sys,wave;w=wave.open(sys.argv[1],\'wb\');w.setnchannels(1);w.setsampwidth(2);'
               f'w.setframerate(22050);w.writeframes(bytes(range(256))*200);w.close()" "{{out}}"')
        mp4 = quiet(pipeline.video, out, tts=cmd, res=720, webm=True)
        self.assertGreater(mp4.stat().st_size, 50_000)
        self.assertEqual(_probe_streams(mp4), ["h264", "aac"])
        self.assertEqual(_probe_streams(out / "codemap.webm"), ["vp8", "opus"])
        html = (out / "codemap.html").read_text(encoding="utf-8")
        self.assertIn("data:audio/mpeg;base64,", html, "the HTML walkthrough carries the recorded narration")

    def test_silent_mp4(self):
        root, _ = demo.create(self.tmp / "demo")
        demo.apply_agent_changes(root)
        quiet(pipeline.analyze, root, tier="free")
        mp4 = quiet(pipeline.video, root / ".codemap", tts="none", res=720)
        self.assertEqual(_probe_streams(mp4), ["h264"])


if __name__ == "__main__":
    unittest.main()
