"""Actual frozen-pipeline checks for inclusive, source-backed learning metadata.

All source lives in disposable fixtures and is parsed only. It is never imported,
compiled, executed, tested or deployed.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "codemap" / "scripts"))
from codemap_lib import history, pipeline, tasks


BEFORE = {
    "app.py": "raise RuntimeError('Fixture source must never execute')\n\ndef calculate():\n    return 1\n",
    "web/app.js": "export function calculate() {\n  return 1;\n}\n",
    "web/typed.ts": "export interface Result { value: number; }\n\nexport function calculate(): number {\n  return 1;\n}\n",
    "java/Calculator.java": "public class Calculator {\n    public int calculate() {\n        return 1;\n    }\n}\n",
    "native/calculate.cpp": "int calculate() {\n    return 1;\n}\n",
    "Dockerfile": "FROM python:3.13-slim\nWORKDIR /app\nCOPY . .\nCMD [\"python\", \"app.py\", \"1\"]\n",
    "deploy/app.yaml": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: fixture-app\nspec:\n  replicas: 1\n  selector:\n    matchLabels:\n      app: fixture\n  template:\n    metadata:\n      labels:\n        app: fixture\n    spec:\n      containers:\n        - name: app\n          image: fixture:1\n",
    "infra/main.tf": 'resource "null_resource" "fixture" {\n  triggers = {\n    version = "1"\n  }\n}\n',
    ".github/workflows/check.yml": "name: Fixture checks\non: [push]\njobs:\n  verify:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo first\n",
    "data/schema.sql": "CREATE TABLE notes (\n    id INTEGER PRIMARY KEY\n);\n",
    "api/openapi.yaml": "openapi: 3.0.3\ninfo:\n  title: Fixture API\n  version: 1.0.0\npaths:\n  /notes:\n    get:\n      operationId: listNotes\n      responses:\n        '200':\n          description: Notes\n",
}


def build_fixture(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for name, content in BEFORE.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    started = tasks.start(root, "learning-fixture", "inclusive-change")
    for name, content in BEFORE.items():
        changed = (content.replace("return 1", "return 2")
                   .replace('"app.py", "1"', '"app.py", "2"')
                   .replace("replicas: 1", "replicas: 2")
                   .replace("image: fixture:1", "image: fixture:2")
                   .replace('version = "1"', 'version = "2"')
                   .replace("echo first", "echo second")
                   .replace("id INTEGER PRIMARY KEY", "id INTEGER PRIMARY KEY,\n    title TEXT NOT NULL")
                   .replace("description: Notes", "description: Updated notes"))
        (root / name).write_text(changed, encoding="utf-8")
    frozen = tasks.freeze(root, started["task_id"])
    ir = pipeline.analyze(frozen["work_root"], out=frozen["out_dir"], isolated=True,
                          isolated_files=sorted(frozen["after"]["files"]), project_name=root.name,
                          tier="video", structural_review=True, include_tests=True, log=lambda _: None)
    state = tasks.update(root, frozen["task_id"], status="awaiting_review", map_ready=True,
                         reviewed_files=0, video_status="pending")
    page = history.write_history(root, [state])
    return state, ir, page


class InclusiveLearningPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="codemap-inclusive-learning-")
        cls.root = Path(cls.temporary.name) / "polyglot-project"
        cls.state, cls.ir, cls.page = build_fixture(cls.root)
        cls.public = history._dashboard_data(cls.root, cls.root / ".codemap", [cls.state])
        cls.task = cls.public["tasks"][0]

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_actual_parser_covers_requested_languages_and_devops_domains(self):
        self.assertTrue({"python", "javascript", "typescript", "java", "cpp"} <= set(self.ir["stats"]["languages"]))
        self.assertTrue({"code", "infra", "ci", "api", "data"} <= set(self.ir["stats"]["domains"]))
        files = {node.get("file") for node in self.ir["nodes"].values()}
        self.assertTrue(set(BEFORE) <= files)
        self.assertTrue(tasks.verify(self.root, self.state["task_id"]))
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), BEFORE["app.py"].replace("return 1", "return 2"))

    def test_language_and_domain_charts_use_actual_parsed_file_counts(self):
        languages = {item["name"]: item["count"] for item in self.task["languages"]}
        domains = {item["name"]: item["count"] for item in self.task["domains"]}
        self.assertEqual(languages, self.ir["stats"]["languages"])
        self.assertEqual(domains, self.ir["stats"]["domains"])
        self.assertEqual(sum(languages.values()), self.ir["stats"]["files"])
        self.assertEqual(sum(domains.values()), self.ir["stats"]["files"])

    def test_learning_contains_real_redacted_changes_without_private_snapshot_paths(self):
        lesson = self.task["learning"]
        self.assertTrue(lesson["available"])
        self.assertTrue(lesson["revision"])
        self.assertTrue(lesson["steps"])
        concepts = {concept["id"] for concept in lesson["concepts"]}
        self.assertTrue(concepts)
        changes = [change for step in lesson["steps"] for change in step.get("changes", [])]
        self.assertTrue(changes)
        self.assertTrue(any(line["kind"] == "added" for change in changes for line in change.get("lines", [])))
        self.assertTrue(any(line["kind"] == "removed" for change in changes for line in change.get("lines", [])))
        for step in lesson["steps"]:
            self.assertTrue(set(step.get("conceptIds", [])) <= concepts)
        rendered = json.dumps(self.public, ensure_ascii=False)
        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for child in value.values():
                    yield from strings(child)
            elif isinstance(value, list):
                for child in value:
                    yield from strings(child)
        for text in strings(self.public):
            self.assertNotIn(str(self.root), text)
            self.assertNotIn(self.root.as_posix(), text)
        self.assertNotIn("/work/", rendered)
        self.assertNotIn("before_root", rendered)
        self.assertNotIn("Fixture source must never execute", rendered)

    def test_progress_reports_generation_stages_not_unreviewed_software_completion(self):
        progress = self.task["progress"]
        self.assertLess(progress["done"], progress["total"])
        self.assertLess(progress["percent"], 100)
        self.assertTrue(progress["remaining"])
        self.assertEqual(self.task["reviewCoverage"]["reviewed"], 0)
        self.assertNotEqual(self.task["reviewCoverage"].get("percent"), 100)
        self.assertNotIn("project completion", progress["label"].lower())
        self.assertEqual(self.public["projectProgress"]["done"], progress["done"])
        self.assertEqual(self.public["projectProgress"]["total"], progress["total"])
        self.assertEqual(self.public["projectProgress"]["remainingTasks"], 1)

    def test_missing_ir_and_skipped_tasks_do_not_invent_lessons_or_review_coverage(self):
        empty = {"task_id": "f" * 32, "status": "skipped", "reviewed_files": 0}
        task = history._dashboard_data(self.root, self.root / ".codemap", [empty])["tasks"][0]
        self.assertFalse(task["learning"]["available"])
        self.assertFalse(task["learning"].get("steps"))
        self.assertIsNone(task["reviewCoverage"].get("percent"))
        self.assertNotEqual(task["category"], "complete")


if __name__ == "__main__":
    unittest.main()
