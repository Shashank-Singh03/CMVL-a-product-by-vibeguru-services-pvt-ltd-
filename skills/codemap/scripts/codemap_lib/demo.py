"""`codemap demo`: build a small polyglot notes product, let a pretend AI agent change it, then map the change.

The project spans Python, JavaScript and a Java service, an OpenAPI contract, Docker, Compose,
Kubernetes, Terraform, GitHub Actions, dependencies and docs. The pretend change adds note deletion end
to end, a read cache and a nightly S3 backup, and it slips in a few realistic security mistakes
(a privileged pod, a public bucket, a broad IAM policy, an unpinned action, a removed API field) so the
risk lens has something to find.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .util import read_json, rmtree, write_json, write_text

MARKER = ".codemap-demo"

BEFORE = {
    "app/__init__.py": '"""Notes: a tiny note-taking app (codemap demo)."""\n',
    "app/models.py": '''"""Data model for notes."""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Note:
    """A single note with a title, body and tags."""
    id: str
    title: str
    body: str = ""
    tags: list = field(default_factory=list)
    created: str = field(default_factory=lambda: datetime.now().isoformat())


def validate_title(title):
    """Reject empty or overly long titles."""
    title = title.strip()
    if not title:
        raise ValueError("title is required")
    if len(title) > 120:
        raise ValueError("title is too long")
    return title
''',
    "app/textutil.py": '''"""Small text helpers."""
import re


def slugify(text):
    """Turn a title into a URL-safe id."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def tokenize(text):
    return [w for w in re.split(r"\\W+", text.lower()) if w]
''',
    "app/http.py": '''"""Minimal router: maps an HTTP method and path to a handler."""


class Router:
    def __init__(self):
        self.routes = {}

    def _add(self, method, path):
        def register(func):
            self.routes[(method, path)] = func
            return func
        return register

    def get(self, path):
        return self._add("GET", path)

    def post(self, path):
        return self._add("POST", path)

    def delete(self, path):
        return self._add("DELETE", path)


router = Router()
''',
    "app/store.py": '''"""JSON file persistence."""
import json
import os

from .models import Note


class JsonStore:
    """Keeps all notes in one JSON file."""

    def __init__(self, path):
        self.path = path

    def load(self):
        if not os.path.exists(self.path):
            return {}
        with open(self.path, encoding="utf-8") as f:
            raw = json.load(f)
        return {k: Note(**v) for k, v in raw.items()}

    def save(self, notes):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({k: vars(v) for k, v in notes.items()}, f, indent=2)

    def all(self):
        return list(self.load().values())
''',
    "app/service.py": '''"""Business rules for notes."""
from .models import Note, validate_title
from .store import JsonStore
from .textutil import slugify, tokenize


class NoteService:
    def __init__(self, store: JsonStore):
        self.store = store

    def add_note(self, title, body="", tags=None):
        title = validate_title(title)
        notes = self.store.load()
        note = Note(id=slugify(title), title=title, body=body, tags=tags or [])
        notes[note.id] = note
        self.store.save(notes)
        return note

    def list_notes(self, tag=None):
        notes = self.store.all()
        if tag:
            notes = [n for n in notes if tag in n.tags]
        return sorted(notes, key=lambda n: n.created)

    def search(self, query):
        words = set(tokenize(query))
        return [n for n in self.store.all() if words & set(tokenize(n.title + " " + n.body))]

    def legacy_export(self, path):
        with open(path, "w", encoding="utf-8") as f:
            for n in self.store.all():
                f.write(f"{n.title}\\t{n.body}\\n")
''',
    "app/api.py": '''"""HTTP handlers that sit between the web client and the service."""
from .http import router
from .service import NoteService
from .store import JsonStore


class NotesAPI:
    def __init__(self, db_path="notes.json"):
        self.service = NoteService(JsonStore(db_path))

    @router.post("/notes")
    def create(self, payload):
        note = self.service.add_note(payload["title"], payload.get("body", ""), payload.get("tags"))
        return {"status": 201, "note": vars(note)}

    @router.get("/notes")
    def list(self, params):
        notes = self.service.list_notes(params.get("tag"))
        return {"status": 200, "notes": [vars(n) for n in notes]}

    @router.get("/notes/search")
    def search(self, params):
        return {"status": 200, "notes": [vars(n) for n in self.service.search(params["q"])]}
''',
    "app/cli.py": '''"""Command-line entry point."""
import argparse

from .api import NotesAPI


def build_parser():
    p = argparse.ArgumentParser(prog="notes")
    sub = p.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("title")
    add.add_argument("--body", default="")
    sub.add_parser("list")
    find = sub.add_parser("find")
    find.add_argument("query")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    api = NotesAPI()
    if args.cmd == "add":
        print(api.create({"title": args.title, "body": args.body}))
    elif args.cmd == "list":
        print(api.list({}))
    else:
        print(api.search({"q": args.query}))


if __name__ == "__main__":
    main()
''',
    "web/api.js": '''// Thin client for the notes API.
const BASE = "/api/notes";

/** Fetch all notes, optionally filtered by tag. */
export async function fetchNotes(tag) {
  const url = tag ? `${BASE}?tag=${encodeURIComponent(tag)}` : BASE;
  const res = await fetch(url);
  return (await res.json()).notes;
}

/** Create a note and return it. */
export async function createNote(title, body) {
  const res = await fetch(BASE, { method: "POST", body: JSON.stringify({ title, body }) });
  return (await res.json()).note;
}
''',
    "web/render.js": '''/** Render one note as an HTML string. */
export function renderNote(note) {
  return `<li class="note"><h3>${note.title}</h3><p>${note.body}</p></li>`;
}

/** Render the whole list. */
export function renderList(notes) {
  return `<ul>${notes.map(renderNote).join("")}</ul>`;
}
''',
    "web/app.js": '''import { fetchNotes, createNote } from "./api.js";
import { renderList } from "./render.js";

/** The browser app: loads notes and redraws the list. */
export class NotesApp {
  constructor(root) {
    this.root = root;
    this.notes = [];
  }

  async start() {
    this.notes = await fetchNotes();
    this.draw();
  }

  async onSubmit(title, body) {
    const note = await createNote(title, body);
    this.notes.push(note);
    this.draw();
  }

  draw() {
    this.root.innerHTML = renderList(this.notes);
  }
}

new NotesApp(document.getElementById("app")).start();
''',
    "services/stats/src/main/java/com/notes/stats/StatsService.java": '''package com.notes.stats;

import java.util.List;

/** Computes statistics about notes for the dashboard. */
public class StatsService {
    private final NoteClient client;

    public StatsService(NoteClient client) {
        this.client = client;
    }

    /** Average number of words per note. */
    public double wordsPerNote() {
        List<NoteDto> notes = client.fetchAll();
        if (notes.isEmpty()) {
            return 0;
        }
        return notes.stream().mapToInt(n -> n.body().split(" ").length).average().orElse(0);
    }
}
''',
    "services/stats/src/main/java/com/notes/stats/StatsController.java": '''package com.notes.stats;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** HTTP endpoints of the stats service. */
@RestController
public class StatsController {
    private final StatsService stats;

    public StatsController(StatsService stats) {
        this.stats = stats;
    }

    @GetMapping("/stats/words")
    public double words() {
        return stats.wordsPerNote();
    }
}
''',
    "services/stats/src/main/java/com/notes/stats/NoteClient.java": '''package com.notes.stats;

import java.util.List;

/** Reads notes from the main API. */
public interface NoteClient {
    List<NoteDto> fetchAll();
}
''',
    "api/openapi.yaml": '''openapi: 3.0.3
info:
  title: Notes API
  version: "1.0"
paths:
  /notes:
    get:
      summary: List notes, optionally filtered by tag
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/Note"
    post:
      summary: Create a note
      responses:
        "201":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Note"
  /notes/search:
    get:
      summary: Full-text search over titles and bodies
      responses:
        "200":
          description: matching notes
  /stats/words:
    get:
      summary: Average words per note
      responses:
        "200":
          description: a number
components:
  schemas:
    Note:
      type: object
      required: [id, title]
      properties:
        id:
          type: string
        title:
          type: string
        body:
          type: string
        tags:
          type: array
        created:
          type: string
''',
    "Dockerfile": '''FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8000
CMD ["python", "-m", "app.cli"]
''',
    "docker-compose.yml": '''services:
  api:
    build: .
    ports:
      - "8000:8000"
  web:
    image: nginx:1.27
    depends_on:
      - api
  stats:
    build: ./services/stats
    depends_on:
      - api
''',
    "deploy/k8s.yaml": '''apiVersion: apps/v1
kind: Deployment
metadata:
  name: notes-api
spec:
  replicas: 2
  selector:
    matchLabels:
      app: notes-api
  template:
    metadata:
      labels:
        app: notes-api
    spec:
      containers:
        - name: api
          image: acme/notes-api:1.4.0
          ports:
            - containerPort: 8000
---
apiVersion: v1
kind: Service
metadata:
  name: notes-api
spec:
  selector:
    app: notes-api
  ports:
    - port: 80
      targetPort: 8000
''',
    "infra/main.tf": '''provider "aws" {
  region = var.region
}

variable "region" {
  description = "AWS region for every resource"
  default     = "eu-west-1"
}

resource "aws_s3_bucket" "assets" {
  bucket = "notes-assets"
}
''',
    ".github/workflows/ci.yml": '''name: CI
on:
  push:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: python -m pytest -q
''',
    "requirements.txt": "requests==2.32.3\n",
    "README.md": '''# Notes

A tiny note-taking product: a Python API, a web client and a Java stats service.

## Running

    python -m app.cli add "Buy milk"
''',
}

# What the pretend AI agent changed.
AFTER = {
    "app/cache.py": '''"""In-memory cache so repeated reads skip the disk."""
from collections import OrderedDict


class LRUCache:
    """Least-recently-used cache with a fixed capacity."""

    def __init__(self, capacity=128):
        self.capacity = capacity
        self.items = OrderedDict()

    def get(self, key):
        if key not in self.items:
            return None
        self.items.move_to_end(key)
        return self.items[key]

    def put(self, key, value):
        self.items[key] = value
        self.items.move_to_end(key)
        if len(self.items) > self.capacity:
            self.items.popitem(last=False)

    def invalidate(self):
        self.items.clear()
''',
    "app/service.py": '''"""Business rules for notes."""
from .cache import LRUCache
from .models import Note, validate_title
from .store import JsonStore
from .textutil import slugify, tokenize


class NoteService:
    def __init__(self, store: JsonStore):
        self.store = store
        self.cache = LRUCache(capacity=256)

    def add_note(self, title, body="", tags=None):
        title = validate_title(title)
        notes = self.store.load()
        note = Note(id=slugify(title), title=title, body=body, tags=tags or [])
        notes[note.id] = note
        self.store.save(notes)
        self.cache.invalidate()
        return note

    def list_notes(self, tag=None):
        notes = self._all_notes()
        if tag:
            notes = [n for n in notes if tag in n.tags]
        return sorted(notes, key=lambda n: n.created)

    def search(self, query):
        words = set(tokenize(query))
        return [n for n in self._all_notes() if words & set(tokenize(n.title + " " + n.body))]

    def delete_note(self, note_id):
        """Remove a note; returns False when it does not exist."""
        removed = self.store.delete(note_id)
        if removed:
            self.cache.invalidate()
        return removed

    def _all_notes(self):
        cached = self.cache.get("all")
        if cached is None:
            cached = self.store.all()
            self.cache.put("all", cached)
        return cached
''',
    "app/store.py": '''"""JSON file persistence."""
import json
import os

from .models import Note


class JsonStore:
    """Keeps all notes in one JSON file."""

    def __init__(self, path):
        self.path = path

    def load(self):
        if not os.path.exists(self.path):
            return {}
        with open(self.path, encoding="utf-8") as f:
            raw = json.load(f)
        return {k: Note(**v) for k, v in raw.items()}

    def save(self, notes):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: vars(v) for k, v in notes.items()}, f, indent=2)
        os.replace(tmp, self.path)

    def delete(self, note_id):
        notes = self.load()
        if note_id not in notes:
            return False
        del notes[note_id]
        self.save(notes)
        return True

    def all(self):
        return list(self.load().values())
''',
    "app/api.py": '''"""HTTP handlers that sit between the web client and the service."""
from .http import router
from .service import NoteService
from .store import JsonStore


class NotesAPI:
    def __init__(self, db_path="notes.json"):
        self.service = NoteService(JsonStore(db_path))

    @router.post("/notes")
    def create(self, payload):
        note = self.service.add_note(payload["title"], payload.get("body", ""), payload.get("tags"))
        return {"status": 201, "note": vars(note)}

    @router.get("/notes")
    def list(self, params):
        notes = self.service.list_notes(params.get("tag"))
        return {"status": 200, "notes": [vars(n) for n in notes]}

    @router.get("/notes/search")
    def search(self, params):
        return {"status": 200, "notes": [vars(n) for n in self.service.search(params["q"])]}

    @router.delete("/notes/{note_id}")
    def delete(self, params):
        ok = self.service.delete_note(params["id"])
        return {"status": 204 if ok else 404}
''',
    "app/cli.py": '''"""Command-line entry point."""
import argparse

from .api import NotesAPI


def build_parser():
    p = argparse.ArgumentParser(prog="notes")
    sub = p.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("title")
    add.add_argument("--body", default="")
    sub.add_parser("list")
    find = sub.add_parser("find")
    find.add_argument("query")
    rm = sub.add_parser("rm")
    rm.add_argument("id")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    api = NotesAPI()
    if args.cmd == "add":
        print(api.create({"title": args.title, "body": args.body}))
    elif args.cmd == "list":
        print(api.list({}))
    elif args.cmd == "rm":
        print(api.delete({"id": args.id}))
    else:
        print(api.search({"q": args.query}))


if __name__ == "__main__":
    main()
''',
    "web/api.js": None,          # filled below: BEFORE + deleteNote
    "web/render.js": '''/** Render one note as an HTML string. */
export function renderNote(note) {
  return `<li class="note" data-id="${note.id}"><h3>${note.title}</h3><p>${note.body}</p>` +
    `<button class="delete" data-id="${note.id}">Delete</button></li>`;
}

/** Render the whole list. */
export function renderList(notes) {
  return `<ul>${notes.map(renderNote).join("")}</ul>`;
}
''',
    "web/app.js": '''import { fetchNotes, createNote, deleteNote } from "./api.js";
import { renderList } from "./render.js";

/** The browser app: loads notes and redraws the list. */
export class NotesApp {
  constructor(root) {
    this.root = root;
    this.notes = [];
    this.root.addEventListener("click", (e) => this.onClick(e));
  }

  async start() {
    this.notes = await fetchNotes();
    this.draw();
  }

  async onSubmit(title, body) {
    const note = await createNote(title, body);
    this.notes.push(note);
    this.draw();
  }

  async onClick(event) {
    const id = event.target.dataset.id;
    if (event.target.classList.contains("delete") && (await deleteNote(id))) {
      this.notes = this.notes.filter((n) => n.id !== id);
      this.draw();
    }
  }

  draw() {
    this.root.innerHTML = renderList(this.notes);
  }
}

new NotesApp(document.getElementById("app")).start();
''',
    "services/stats/src/main/java/com/notes/stats/StatsService.java": '''package com.notes.stats;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Computes statistics about notes for the dashboard. */
public class StatsService {
    private final NoteClient client;

    public StatsService(NoteClient client) {
        this.client = client;
    }

    /** Average number of words per note. */
    public double wordsPerNote() {
        List<NoteDto> notes = client.fetchAll();
        if (notes.isEmpty()) {
            return 0;
        }
        return notes.stream().mapToInt(n -> n.body().split(" ").length).average().orElse(0);
    }

    /** How many notes carry each tag. */
    public Map<String, Integer> countByTag() {
        Map<String, Integer> counts = new HashMap<>();
        for (NoteDto note : client.fetchAll()) {
            for (String tag : note.tags()) {
                counts.merge(tag, 1, Integer::sum);
            }
        }
        return counts;
    }
}
''',
    "services/stats/src/main/java/com/notes/stats/StatsController.java": '''package com.notes.stats;

import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** HTTP endpoints of the stats service. */
@RestController
public class StatsController {
    private final StatsService stats;

    public StatsController(StatsService stats) {
        this.stats = stats;
    }

    @GetMapping("/stats/words")
    public double words() {
        return stats.wordsPerNote();
    }

    @GetMapping("/stats/tags")
    public Map<String, Integer> byTag() {
        return stats.countByTag();
    }
}
''',
    "api/openapi.yaml": '''openapi: 3.0.3
info:
  title: Notes API
  version: "1.1"
paths:
  /notes:
    get:
      summary: List notes, optionally filtered by tag
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/Note"
    post:
      summary: Create a note
      responses:
        "201":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Note"
  /notes/{id}:
    delete:
      summary: Delete a note
      responses:
        "204":
          description: deleted
        "404":
          description: no such note
  /notes/search:
    get:
      summary: Full-text search over titles and bodies
      responses:
        "200":
          description: matching notes
  /stats/words:
    get:
      summary: Average words per note
      responses:
        "200":
          description: a number
  /stats/tags:
    get:
      summary: Number of notes per tag
      responses:
        "200":
          description: tag counts
components:
  schemas:
    Note:
      type: object
      required: [id, title]
      properties:
        id:
          type: string
        title:
          type: string
        body:
          type: string
        tags:
          type: array
        createdAt:
          type: string
''',
    "Dockerfile": '''FROM python:3.13-slim
RUN useradd --create-home app
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
USER app
EXPOSE 8000
CMD ["python", "-m", "app.cli"]
''',
    "docker-compose.yml": '''services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      CACHE_SIZE: "256"
  web:
    image: nginx:1.27
    depends_on:
      - api
  stats:
    build: ./services/stats
    depends_on:
      - api
''',
    "deploy/k8s.yaml": '''apiVersion: apps/v1
kind: Deployment
metadata:
  name: notes-api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: notes-api
  template:
    metadata:
      labels:
        app: notes-api
    spec:
      containers:
        - name: api
          image: acme/notes-api:1.5.0
          ports:
            - containerPort: 8000
          securityContext:
            privileged: true
---
apiVersion: v1
kind: Service
metadata:
  name: notes-api
spec:
  selector:
    app: notes-api
  ports:
    - port: 80
      targetPort: 8000
''',
    "infra/backup.tf": '''# Nightly backups of the notes file.
resource "aws_s3_bucket" "backups" {
  bucket = "notes-backups"
  acl    = "public-read"
}

resource "aws_iam_policy" "backup_writer" {
  name   = "notes-backup-writer"
  policy = jsonencode({
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:*"]
      Resource = ["*"]
    }]
  })
}

output "backup_bucket" {
  value = aws_s3_bucket.backups.bucket
}
''',
    "scripts/backup.sh": '''#!/usr/bin/env bash
# Copy the notes file to the backup bucket.
set -euo pipefail

backup_notes() {
  local stamp
  stamp="$(date +%F)"
  aws s3 cp notes.json "s3://notes-backups/notes-${stamp}.json"
}

backup_notes
''',
    ".github/workflows/ci.yml": '''name: CI
on:
  push:
    branches: [main]
  schedule:
    - cron: "0 2 * * *"
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: python -m pytest -q
  backup:
    needs: [test]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@main
      - run: ./scripts/backup.sh
''',
    "requirements.txt": "requests==2.32.3\nboto3==1.35.0\n",
    "README.md": BEFORE["README.md"] + '''
## Deleting notes

    python -m app.cli rm buy-milk

Deleted notes are gone for good; the nightly backup keeps a copy for 30 days.
''',
    "docs/runbook.txt": '''RUNBOOK: restoring notes from a backup
1. Find yesterday's file in the notes-backups bucket.
2. Stop the api deployment (kubectl scale deploy/notes-api --replicas=0).
3. Copy the file over notes.json and start the deployment again.
''',
}
AFTER["web/api.js"] = BEFORE["web/api.js"] + '''
/** Delete a note by id. */
export async function deleteNote(id) {
  const res = await fetch(`${BASE}/${encodeURIComponent(id)}`, { method: "DELETE" });
  return res.status === 204;
}
'''


def _write(root: Path, files: dict) -> None:
    for rel, text in files.items():
        write_text(root / rel, text)


def create(target: Path | None = None) -> tuple:
    """Create the demo project. Returns (folder, uses_git)."""
    root = Path(target).resolve() if target else Path(tempfile.gettempdir()) / "codemap-demo"
    if root.exists():
        if not (root / MARKER).exists() and any(root.iterdir()):
            raise RuntimeError(f"{root} exists and is not a codemap demo folder; pick another --dir")
        rmtree(root)
    root.mkdir(parents=True)
    write_text(root / MARKER, "created by `codemap demo`; safe to delete\n")
    _write(root, BEFORE)
    use_git = shutil.which("git") is not None
    if use_git:
        def g(*args):
            subprocess.run(["git", "-c", "user.name=codemap demo", "-c", "user.email=demo@codemap.invalid",
                            "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                           cwd=root, check=True, capture_output=True)
        g("init", "-q")
        write_text(root / ".gitignore", ".codemap/\n" + MARKER + "\n")
        g("add", "-A")
        g("commit", "-q", "-m", "Notes product: API, web client, stats service, infra and CI")
    return root, use_git


def apply_agent_changes(root: Path) -> None:
    _write(root, AFTER)


def sample_labels(ir: dict) -> dict:
    """Labels an agent would write for this demo (keyed by stable full ids)."""
    from .render import ASSETS
    return read_json(ASSETS / "demo-labels.json", {}) or {}


def write_sample_labels(out_dir: Path, ir: dict) -> None:
    write_json(out_dir / "labels.json", sample_labels(ir), indent=2)
