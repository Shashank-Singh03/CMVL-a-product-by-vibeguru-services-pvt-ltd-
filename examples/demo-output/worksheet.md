# codemap worksheet | tier: video | 92 items
Task: give each item a plain-English title and a one-line summary so a newcomer understands this code.
Scope: uncommitted changes vs HEAD bd14188 | 20 files +175 -14

## Views (top-down; each line is one diagram)
v0 L0 notes-app: g1 g2 g3 u1 u2 u3 u4
v1 L1 g1: g4 u5 g5 g6
v2 L1 g2: u6 u7 u8
v3 L1 g3: u9 u10 u11 u12
v4 L1 u1: f1 f2 f3
v5 L1 u2: f4 +more
v6 L1 u3: f5 +more
v7 L2 g4: u13 g7 u14 g8 g9 u15 u16
v8 L2 u5: f6
v9 L2 g5: u17 u18 u19 u20 u21
v10 L2 g6: u22 u23 u24 u25
v11 L2 u6: f7 f8 f9 f10 +more
v12 L2 u7: f11
v13 L2 u9: f12
v14 L2 u10: f13 f14
v15 L2 u11: f15 f16 f17
v16 L2 u12: f18 f19 f20
v17 L3 u13: f21 f22 f23 f24 +more
v18 L3 g7: u26 u27
v19 L3 u14: f25 f26
v20 L3 g8: u28 u29
v21 L3 g9: u30 u31
v22 L3 u15: f27 f28 f29 f30 +more
v23 L3 u16: f31 +more
v24 L3 u17: f32
v25 L3 u18: f33 +more
v26 L3 u20: f34 +more
v27 L3 u22: f35 +more
v28 L3 u23: f36 f37 f38 +more
v29 L3 u25: f39 f40
v30 L4 u26: f41 f42 f43 f44
v31 L4 u29: f45
v32 L4 u30: f46 f47 f48 f49 f50 f51 f52

## Items (id kind where | signature | status | facts; diff lines indented)
g1 group Code | 14 files | MODIFIED +100 -9 | has: app/, web/, services/, backup.sh
g4 group app/ | 7 files | MODIFIED +58 -7 | has: service.py, JsonStore, NotesAPI, cache.py, models.py
g6 group web/ | 3 files | MODIFIED +16 -2 | has: NotesApp, render.js, api.js, app.js
g5 group services/ | 3 files | MODIFIED +17 | has: StatsService, NoteClient, StatsController, StatsController.java, StatsService.java
g9 group app/service.py | 1 files | MODIFIED +17 -6 | has: NoteService, service.py
g3 group Infrastructure | 4 files | MODIFIED +27 -3 | has: docker-compose.yml, backup.tf, Dockerfile, k8s.yaml
g2 group API contracts | 1 files | MODIFIED +16 -2 | has: Endpoints, Schemas, openapi.yaml
g7 group app/cache.py | 1 files | ADDED +23 | has: LRUCache, cache.py
g8 group app/models.py | 1 files | has: Note, models.py
f11 schema Schemas.Note api/openapi.yaml:53 | Note: | MODIFIED +1 -1 | used by: f7, f8 | doc: Fields: id, title, body, tags, createdAt.
    -created:
    +createdAt:
    ! r6 MEDIUM api.removed_field: Field 'created' was removed from Note; clients that read it will break. (line 53)
f13 deployment Deployment/notes-api deploy/k8s.yaml:1 | apiVersion: apps/v1 | MODIFIED +4 -2 | used by: f14 | doc: runs acme/notes-api:1.5.0.
    -  replicas: 2
    +  replicas: 3
    -          image: acme/notes-api:1.4.0
    +          image: acme/notes-api:1.5.0
    +          securityContext:
    +            privileged: true
    ! r2 HIGH k8s.privileged: Runs a container privileged, with full access to the host. (line 21)
f18 resource aws_s3_bucket.backups infra/backup.tf:2 | resource "aws_s3_bucket" "backups" { | ADDED +4 | used by: f20
    +resource "aws_s3_bucket" "backups" {
    +  bucket = "notes-backups"
    +  acl    = "public-read"
    +}
    ! r3 HIGH storage.public: Makes storage publicly readable on the internet. (line 4)
f19 resource aws_iam_policy.backup_writer infra/backup.tf:7 | resource "aws_iam_policy" "backup_writer" { | ADDED +10
    +resource "aws_iam_policy" "backup_writer" {
    +  name   = "notes-backup-writer"
    +  policy = jsonencode({
    +    Statement = [{
    +      Effect   = "Allow"
    +      Action   = ["s3:*"]
    +      Resource = ["*"]
    +    }]
    ! r4 MEDIUM iam.service_wildcard: Grants every action of a cloud service (for example "s3:*"). (line 12)
    ! r5 LOW iam.any_resource: Applies a permission to every resource (Resource: "*"). (line 13)
f3 job backup .github/workflows/ci.yml:17 | job backup (runs on ubuntu-latest) | ADDED +6 | uses: f2, f6 | doc: steps: actions/checkout@main.
    +backup:
    +  needs: [test]
    +  runs-on: ubuntu-latest
    +  steps:
    +    - uses: actions/checkout@main
    +    - run: ./scripts/backup.sh
    ! r1 MEDIUM ci.unpinned_action: A third-party action is pinned to a moving branch, not a version or commit. (line 21)
f4 package boto3 requirements.txt:2 | boto3 ==1.35.0 | ADDED +1
    +boto3==1.35.0
    ! r7 LOW deps.new: New dependency boto3: check its license, maintenance and size. (line 2)
u4 file docs/runbook.txt (text file; codemap has no parser for it: describe it from these lines) | ADDED +4
    +RUNBOOK: restoring notes from a backup
    +1. Find yesterday's file in the notes-backups bucket.
    +2. Stop the api deployment (kubectl scale deploy/notes-api --replicas=0).
    +3. Copy the file over notes.json and start the deployment again.
u30 class app/service.py:8 | class NoteService | MODIFIED +16 -6 | methods: __init__(), add_note(), list_notes(), search(), delete_note(), _all_notes(), legacy_export()
u15 class app/store.py:8 | class JsonStore | MODIFIED +10 -1 | methods: load(), save(), delete(), all(), __init__() | doc: Keeps all notes in one JSON file.
u13 class app/api.py:7 | class NotesAPI | MODIFIED +4 | methods: create(), list(), search(), delete(), __init__()
u26 class app/cache.py:5 | class LRUCache | ADDED +21 | methods: __init__(), get(), put(), invalidate() | doc: Least-recently-used cache with a fixed capacity.
u23 class web/app.js:4 | export class NotesApp | MODIFIED +8 | methods: constructor(), onClick(), draw(), start(), onSubmit() | doc: The browser app: loads notes and redraws the list.
u6 API endpoints api/openapi.yaml:5 | paths: | MODIFIED +14 | members: GET /notes, POST /notes, DELETE /notes/{id}, GET /stats/tags, GET /notes/search, GET /stats/words | doc: HTTP operations in this contract.
    +/notes/{id}:
    +/stats/tags:
u20 class services/stats/src/main/java/com/notes/stats/StatsService.java:7 | public class StatsService | MODIFIED +10 | methods: countByTag(), StatsService(), wordsPerNote() | doc: Computes statistics about notes for the dashboard.
u25 module web/render.js | MODIFIED +2 -1 | functions: renderNote(), renderList()
u18 class services/stats/src/main/java/com/notes/stats/StatsController.java:7 | @RestController public class StatsController | MODIFIED +4 | methods: byTag(), StatsController(), words() | doc: HTTP endpoints of the stats service.
u11 Compose file docker-compose.yml | MODIFIED +2 | items: api, web, stats
u22 module web/api.js | MODIFIED +5 | functions: deleteNote(), fetchNotes(), createNote()
u12 Terraform infra/backup.tf | ADDED +18 | items: aws_s3_bucket.backups, aws_iam_policy.backup_writer, output.backup_bucket
    +# Nightly backups of the notes file.
u1 GitHub Actions workflow .github/workflows/ci.yml | MODIFIED +8 | items: on, test, backup
u9 Dockerfile Dockerfile | MODIFIED +3 -1 | items: container image
u3 document README.md | MODIFIED +5 | items: Deleting notes, Notes, Running
u10 Kubernetes manifest deploy/k8s.yaml | MODIFIED +4 -2 | items: Deployment/notes-api, Service/notes-api
u7 API schemas api/openapi.yaml:52 | schemas: | MODIFIED +1 -1 | members: Note | doc: Data shapes the endpoints exchange.
u14 module app/cli.py | MODIFIED +4 | functions: build_parser(), main() | doc: Command-line entry point.
u2 Python dependencies requirements.txt | MODIFIED +1 | items: boto3, requests
u5 module scripts/backup.sh | ADDED +9 | functions: backup_notes()
    +#!/usr/bin/env bash
    +# Copy the notes file to the backup bucket.
    +set -euo pipefail
    +backup_notes
u19 module services/stats/src/main/java/com/notes/stats/StatsController.java | MODIFIED +1 | functions: -
    +import java.util.Map;
u21 module services/stats/src/main/java/com/notes/stats/StatsService.java | MODIFIED +2 | functions: -
    +import java.util.HashMap;
    +import java.util.Map;
u24 module web/app.js | MODIFIED +1 -1 | functions: -
    -import { fetchNotes, createNote } from "./api.js";
    +import { fetchNotes, createNote, deleteNote } from "./api.js";
u27 module app/cache.py | ADDED +2 | functions: - | doc: In-memory cache so repeated reads skip the disk.
    +"""In-memory cache so repeated reads skip the disk."""
    +from collections import OrderedDict
u31 module app/service.py | MODIFIED +1 | functions: - | doc: Business rules for notes.
    +from .cache import LRUCache
u8 OpenAPI contract api/openapi.yaml | MODIFIED +1 -1 | items: -
    -version: "1.0"
    +version: "1.1"
f51 method NoteService._all_notes app/service.py:39 | def _all_notes(self) | ADDED +6 | calls: f42, f43, f30 | used by: f49, f48
    +def _all_notes(self):
    +    cached = self.cache.get("all")
    +    if cached is None:
    +        cached = self.store.all()
    +        self.cache.put("all", cached)
    +    return cached
f50 method NoteService.delete_note app/service.py:32 | def delete_note(self, note_id) | ADDED +6 | calls: f44, f29 | used by: f24 | doc: Remove a note; returns False when it does not exist.
    +def delete_note(self, note_id):
    +    """Remove a note; returns False when it does not exist."""
    +    removed = self.store.delete(note_id)
    +    if removed:
    +        self.cache.invalidate()
    +    return removed
f39 function renderNote web/render.js:1 | export function renderNote(note) | MODIFIED +2 -1 | used by: f40 | doc: Render one note as an HTML string.
    -  return `<li class="note"><h3>${note.title}</h3><p>${note.body}</p></li>`;
    +  return `<li class="note" data-id="${note.id}"><h3>${note.title}</h3><p>${note.body}</p>` +
    +    `<button class="delete" data-id="${note.id}">Delete</button></li>`;
f12 stage container image Dockerfile:1 | FROM python:3.13-slim | MODIFIED +3 -1 | used by: f15 | doc: from python:3.13-slim; runs as app; exposes 8000; cmd ["python", "-m", "app.cli"].
    -FROM python:3.12-slim
    +FROM python:3.13-slim
    +RUN useradd --create-home app
    +USER app
f24 method NotesAPI.delete app/api.py:25 | def delete(self, params) | ADDED +4 | serves: DELETE /notes/{} | calls: f50 | used by: f35, f9
    +@router.delete("/notes/{note_id}")
    +def delete(self, params):
    +    ok = self.service.delete_note(params["id"])
    +    return {"status": 204 if ok else 404}
f44 method LRUCache.invalidate app/cache.py:24 | def invalidate(self) | ADDED +2 | used by: f50, f47
    +def invalidate(self):
    +    self.items.clear()
f15 service api docker-compose.yml:2 | api: | MODIFIED +2 | uses: f12 | used by: f17, f16 | doc: built from .; ports 8000:8000.
    +environment:
    +  CACHE_SIZE: "256"
f29 method JsonStore.delete app/store.py:27 | def delete(self, note_id) | ADDED +7 | calls: f27, f28 | used by: f50
    +def delete(self, note_id):
    +    notes = self.load()
    +    if note_id not in notes:
    +        return False
    +    del notes[note_id]
    +    self.save(notes)
    +    return True
f34 method StatsService.countByTag services/stats/src/main/java/com/notes/stats/StatsService.java:24 | public Map<String, Integer> countByTag() | ADDED +10 | calls: f32 | used by: f33 | doc: How many notes carry each tag.
    +/** How many notes carry each tag. */
    +public Map<String, Integer> countByTag() {
    +    Map<String, Integer> counts = new HashMap<>();
    +    for (NoteDto note : client.fetchAll()) {
    +        for (String tag : note.tags()) {
    +            counts.merge(tag, 1, Integer::sum);
    +        }
    +    }
f49 method NoteService.search app/service.py:28 | def search(self, query) | MODIFIED +1 -1 | calls: f51, f31 | used by: f23
    -    return [n for n in self.store.all() if words & set(tokenize(n.title + " " + n.body))]
    +    return [n for n in self._all_notes() if words & set(tokenize(n.title + " " + n.body))]
f28 method JsonStore.save app/store.py:21 | def save(self, notes) | MODIFIED +3 -1 | used by: f29, f47
    -    with open(self.path, "w", encoding="utf-8") as f:
    +    tmp = self.path + ".tmp"
    +    with open(tmp, "w", encoding="utf-8") as f:
    +    os.replace(tmp, self.path)
f47 method NoteService.add_note app/service.py:13 | def add_note(self, title, body='', tags=None) | MODIFIED +1 | calls: u28, f27, f44, f28 | used by: f21
    +self.cache.invalidate()
f48 method NoteService.list_notes app/service.py:22 | def list_notes(self, tag=None) | MODIFIED +1 -1 | calls: f51 | used by: f22
    -    notes = self.store.all()
    +    notes = self._all_notes()
f42 method LRUCache.get app/cache.py:12 | def get(self, key) | ADDED +5 | used by: f51
    +def get(self, key):
    +    if key not in self.items:
    +        return None
    +    self.items.move_to_end(key)
    +    return self.items[key]
f43 method LRUCache.put app/cache.py:18 | def put(self, key, value) | ADDED +5 | used by: f51
    +def put(self, key, value):
    +    self.items[key] = value
    +    self.items.move_to_end(key)
    +    if len(self.items) > self.capacity:
    +        self.items.popitem(last=False)
f33 method StatsController.byTag services/stats/src/main/java/com/notes/stats/StatsController.java:21 | public Map<String, Integer> byTag() | ADDED +4 | serves: GET /stats/tags | calls: f34 | used by: f10
    +@GetMapping("/stats/tags")
    +public Map<String, Integer> byTag() {
    +    return stats.countByTag();
    +}
f37 method NotesApp.onClick web/app.js:23 | async onClick(event) | ADDED +7 | calls: f38, f35 | used by: f36
    +async onClick(event) {
    +  const id = event.target.dataset.id;
    +  if (event.target.classList.contains("delete") && (await deleteNote(id))) {
    +    this.notes = this.notes.filter((n) => n.id !== id);
    +    this.draw();
    +  }
    +}
f35 function deleteNote web/api.js:17 | export async function deleteNote(id) | ADDED +5 | calls: f24, f9 | used by: f37 | doc: Delete a note by id.
    +/** Delete a note by id. */
    +export async function deleteNote(id) {
    +  const res = await fetch(`${BASE}/${encodeURIComponent(id)}`, { method: "DELETE" });
    +  return res.status === 204;
    +}
f9 endpoint Endpoints.DELETE /notes/{id} api/openapi.yaml:26 | DELETE /notes/{id} | ADDED +7 | serves: DELETE /notes/{} | uses: f24 | used by: f35 | doc: Delete a note
    +delete:
    +  summary: Delete a note
    +  responses:
    +    "204":
    +      description: deleted
    +    "404":
    +      description: no such note
f25 function build_parser app/cli.py:7 | def build_parser() | MODIFIED +2 | used by: f26
    +rm = sub.add_parser("rm")
    +rm.add_argument("id")
f6 function backup_notes scripts/backup.sh:5 | backup_notes() | ADDED +5 | used by: f3
    +backup_notes() {
    +  local stamp
    +  stamp="$(date +%F)"
    +  aws s3 cp notes.json "s3://notes-backups/notes-${stamp}.json"
    +}
f1 trigger on .github/workflows/ci.yml:2 | on: | MODIFIED +2 | doc: Triggered by push, schedule.
    +schedule:
    +  - cron: "0 2 * * *"
f10 endpoint Endpoints.GET /stats/tags api/openapi.yaml:46 | GET /stats/tags | ADDED +5 | serves: GET /stats/tags | uses: f33 | doc: Number of notes per tag
    +get:
    +  summary: Number of notes per tag
    +  responses:
    +    "200":
    +      description: tag counts
f20 output output.backup_bucket infra/backup.tf:18 | output "backup_bucket" { | ADDED +3 | uses: f18
    +output "backup_bucket" {
    +  value = aws_s3_bucket.backups.bucket
    +}
f26 function main app/cli.py:21 | def main(argv=None) | MODIFIED +2 | calls: u13, f25
    +elif args.cmd == "rm":
    +    print(api.delete({"id": args.id}))
f36 method NotesApp.constructor web/app.js:6 | constructor(root) | MODIFIED +1 | calls: f37
    +  this.root.addEventListener("click", (e) => this.onClick(e));
f41 method LRUCache.__init__ app/cache.py:8 | def __init__(self, capacity=128) | ADDED +3
    +def __init__(self, capacity=128):
    +    self.capacity = capacity
    +    self.items = OrderedDict()
f46 method NoteService.__init__ app/service.py:9 | def __init__(self, store: JsonStore) | MODIFIED +1 | calls: u26
    +    self.cache = LRUCache(capacity=256)
f5 section Deleting notes README.md:9 | ## Deleting notes | ADDED +5
    +## Deleting notes
    +
    +    python -m app.cli rm buy-milk
    +
    +Deleted notes are gone for good; the nightly backup keeps a copy for 30 days.
f52 method NoteService.legacy_export app/service.py:29 | def legacy_export(self, path) | DELETED -4
    -def legacy_export(self, path):
    -    with open(path, "w", encoding="utf-8") as f:
    -        for n in self.store.all():
    -            f.write(f"{n.title}\t{n.body}\n")
u17 interface services/stats/src/main/java/com/notes/stats/NoteClient.java:5 | public interface NoteClient | methods: fetchAll() | doc: Reads notes from the main API.
u28 class app/models.py:6 | class Note | methods: - | doc: A single note with a title, body and tags.
u16 module app/textutil.py | functions: tokenize(), slugify() | doc: Small text helpers.
u29 module app/models.py | functions: validate_title() | doc: Data model for notes.
f32 abstract NoteClient.fetchAll services/stats/src/main/java/com/notes/stats/NoteClient.java:7 | List<NoteDto> fetchAll() | used by: f34, wordsPerNote()
f27 method JsonStore.load app/store.py:14 | def load(self) | calls: u28 | used by: f29, f47, f30
f40 function renderList web/render.js:7 | export function renderList(notes) | calls: f39 | used by: f38 | doc: Render the whole list.
f38 method NotesApp.draw web/app.js:31 | draw() | calls: f40 | used by: f37, onSubmit(), start()
f30 method JsonStore.all app/store.py:35 | def all(self) | calls: f27 | used by: f51
f31 function tokenize app/textutil.py:10 | def tokenize(text) | used by: f49
f23 method NotesAPI.search app/api.py:21 | def search(self, params) | serves: GET /notes/search | calls: f49 | used by: GET /notes/search()
f2 job test .github/workflows/ci.yml:8 | job test (runs on ubuntu-latest) | used by: f3 | doc: steps: actions/checkout@v4, actions/setup-python@v5.
f21 method NotesAPI.create app/api.py:11 | def create(self, payload) | serves: POST /notes | calls: f47 | used by: f8
f22 method NotesAPI.list app/api.py:16 | def list(self, params) | serves: GET /notes | calls: f48 | used by: f7
f45 function validate_title app/models.py:16 | def validate_title(title) | used by: f47 | doc: Reject empty or overly long titles.
f14 service Service/notes-api deploy/k8s.yaml:23 | apiVersion: v1 | uses: f13 | doc: ports 80.
f16 service web docker-compose.yml:8 | web: | uses: f15 | doc: image nginx:1.27.
f17 service stats docker-compose.yml:12 | stats: | uses: f15 | doc: built from ./services/stats.
f7 endpoint Endpoints.GET /notes api/openapi.yaml:7 | GET /notes | serves: GET /notes | uses: f11, f22 | doc: List notes, optionally filtered by tag
f8 endpoint Endpoints.POST /notes api/openapi.yaml:17 | POST /notes | serves: POST /notes | uses: f11, f21 | doc: Create a note

## Narration beats (one spoken sentence each, <= 25 words; describe, never read code aloud)
b1 title
b2 why
b3 overview v0
b4 overview g1
b5 overview g6
b6 overview g4
b7 overview g9
b8 overview g7
b9 overview g5
b10 overview g2
b11 overview g3
b12 risks
b13 outro

## Risk flags (deterministic rules; confirm each, or say why it is a false positive)
r2 HIGH deploy/k8s.yaml:21 k8s.privileged: Runs a container privileged, with full access to the host.
r3 HIGH infra/backup.tf:4 storage.public: Makes storage publicly readable on the internet.
r1 MEDIUM .github/workflows/ci.yml:21 ci.unpinned_action: A third-party action is pinned to a moving branch, not a version or commit.
r6 MEDIUM api/openapi.yaml:53 api.removed_field: Field 'created' was removed from Note; clients that read it will break.
r4 MEDIUM infra/backup.tf:12 iam.service_wildcard: Grants every action of a cloud service (for example "s3:*").

## Reply: write .codemap/labels.json, then run `codemap render`
{"why": "1-2 sentences: the goal of this change (what problem it solves)",
 "items": {"g1": ["Title of 2-5 words", "What it does / what changed, max 20 words"]},
 "views": {"v0": "One-line caption for the diagram"},
 "risks": {"r1": "Real issue: ... / False positive: ..."},
 "beats": {"b1": "Narration sentence"}}
Rules: plain words, no identifiers in titles; for changed items say what changed and why; skip items you cannot improve.
