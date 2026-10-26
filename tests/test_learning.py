"""Learning packs use bounded captured evidence, not source execution or guesses."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/codemap/scripts"))
from codemap_lib.learning import MAX_CHANGES, MAX_CONCEPTS, MAX_LINES, MAX_PACK_BYTES, build_learning
from codemap_lib.redact import SecretRedactor


class LearningPackTest(unittest.TestCase):
    def clean(self, value, limit=600):
        self.redactor.add_text(value, config=True)
        return self.redactor.text(value)[:limit]

    def setUp(self):
        self.redactor = SecretRedactor()

    def node(self, path="app.py", name="answer", lang="python", domain="code", **fields):
        return dict(kind="function", name=name, title="Adjust the answer", file=path, lang=lang,
                    domain=domain, status="modified", diff=[" def answer():", "-    return 1", "+    return 2"],
                    **fields)

    def ir(self):
        return dict(nodes={"f:app.py#answer": self.node()}, stats=dict(files=1, languages={"python": 1},
                    risks=dict(high=0, medium=0, low=0)), scope=dict(files=1, mode="diff"),
                    why=[dict(source="agent", text="Return the updated answer.")],
                    review=dict(status="reviewed", reviewed_files=["app.py"]), views=[])

    def build(self, ir):
        return build_learning(ir, clean_text=self.clean)

    def test_real_changed_lines_and_five_lessons_without_invented_line_numbers(self):
        pack = self.build(self.ir())
        self.assertTrue(pack["available"])
        self.assertEqual([step["id"] for step in pack["steps"]], ["overview", "concepts", "changes", "connections", "review"])
        changes = pack["steps"][2]["changes"]
        self.assertEqual(changes[0]["file"], "app.py")
        self.assertEqual([line["kind"] for line in changes[0]["lines"]], ["context", "removed", "added"])
        self.assertTrue(all(line["line"] is None for line in changes[0]["lines"]))
        self.assertTrue({"return", "literal"} <= {concept["id"] for concept in pack["concepts"]})
        self.assertIn("self-reported", pack["steps"][4]["body"])
        self.assertNotIn("tests passed", json.dumps(pack))

    def test_pending_review_is_provisional_and_completion_is_not_knowledge_proof(self):
        ir = self.ir()
        ir["review"] = dict(status="pending", reviewed_files=[])
        pack = self.build(ir)
        self.assertEqual(pack["reviewStatus"], "pending")
        self.assertIn("has not completed", pack["steps"][0]["body"])
        self.assertIn("does not measure verified knowledge", pack["steps"][4]["body"])

    def test_polyglot_and_devops_have_relevant_definitions_and_selected_excerpts(self):
        ir = self.ir()
        specs = [("web/app.js", "javascript", "code"), ("web/app.ts", "typescript", "code"),
                 ("Main.java", "java", "code"), ("main.cpp", "cpp", "code"),
                 ("Dockerfile", "dockerfile", "infra"), ("deploy.yaml", "kubernetes", "infra"),
                 ("main.tf", "terraform", "infra"), (".github/workflows/test.yml", "github-actions", "ci"),
                 ("schema.sql", "sql", "data"), ("api.yaml", "openapi", "api")]
        for index, (path, lang, domain) in enumerate(specs):
            ir["nodes"][f"f:{path}#item{index}"] = self.node(path, f"item{index}", lang, domain)
        ir["stats"]["languages"] = {lang: 1 for _, lang, _ in specs}
        pack = self.build(ir)
        concepts = {item["id"] for item in pack["concepts"]}
        self.assertTrue({"type", "class", "pointer", "container", "deployment", "pipeline", "schema", "api"} <= concepts)
        changes = pack["steps"][2]["changes"]
        self.assertTrue({"javascript", "typescript", "java", "cpp", "github-actions", "terraform"} <= {item["language"] for item in changes})

    def test_private_fields_paths_raw_errors_and_signature_are_never_promoted(self):
        ir = self.ir()
        node = ir["nodes"]["f:app.py#answer"]
        node.update(sig="PRIVATE_SIGNATURE", doc="PRIVATE_DOC", callers=[["PRIVATE_CALLER", "private"]],
                    source="PRIVATE_SOURCE_BODY", error="PRIVATE_NODE_ERROR")
        ir.update(root="C:/Users/PRIVATE_OWNER/repo", analysis={"target": "/home/PRIVATE_OWNER/repo"},
                  error="PRIVATE_ERROR", prompt="PRIVATE_PROMPT")
        ir["nodes"]["unsafe"] = self.node("../../private.py", "PRIVATE_PATH_NODE")
        ir["nodes"]["private-task"] = self.node(".codemap/tasks/private/work/app.py", "PRIVATE_TASK_PATH_NODE")
        public = json.dumps(self.build(ir))
        for marker in ("PRIVATE_SIGNATURE", "PRIVATE_DOC", "PRIVATE_CALLER", "PRIVATE_SOURCE_BODY", "PRIVATE_NODE_ERROR",
                       "PRIVATE_OWNER", "PRIVATE_ERROR", "PRIVATE_PROMPT", "PRIVATE_PATH_NODE", "PRIVATE_TASK_PATH_NODE"):
            self.assertNotIn(marker, public)

    def test_credential_redaction_happens_before_individual_excerpt_clipping(self):
        ir = self.ir()
        ir["nodes"]["f:app.py#answer"]["diff"] = ["+api_key='LEARNING_CREDENTIAL_12345678901234567890'", "+return api_key"]
        public = json.dumps(self.build(ir))
        self.assertNotIn("LEARNING_CREDENTIAL_12345678901234567890", public)
        self.assertIn("[REDACTED]", public)

    def test_connections_distinguish_static_and_model_inference(self):
        ir = self.ir()
        ir["nodes"]["f:helpers.py#helper"] = self.node("helpers.py", "helper")
        ir["nodes"]["f:other.py#other"] = self.node("other.py", "other")
        ir["views"] = [dict(edges=[["f:app.py#answer", "f:helpers.py#helper", 1, 0.8, "calls"],
                                   ["f:helpers.py#helper", "f:other.py#other", 1, 0.6, "refs"]],
                            review_edges=[["f:helpers.py#helper", "f:other.py#other"]])]
        pack = self.build(ir)
        connections = pack["steps"][3]["connections"]
        self.assertEqual([item["confidence"] for item in connections], ["static", "model"])
        self.assertIn("Neither is an observed execution trace", pack["steps"][3]["body"])

    def test_context_map_does_not_manufacture_a_change(self):
        ir = self.ir()
        ir["nodes"]["f:app.py#answer"].update(status="same", diff=[])
        pack = self.build(ir)
        self.assertEqual(pack["steps"][2]["changes"][0]["status"], "context")
        self.assertIn("no recorded added, modified or deleted", pack["steps"][2]["body"])
        self.assertNotIn("diff", {item["id"] for item in pack["concepts"]})

    def test_bounds_hold_even_for_large_unicode_excerpts(self):
        ir = self.ir()
        ir["nodes"] = {f"f:item{index}.ts#work": self.node(f"item{index}.ts", lang="typescript") for index in range(80)}
        for node in ir["nodes"].values():
            node.update(title="界" * 160, sum="界" * 480, diff=["+" + "界" * 2000] * 100)
        pack = self.build(ir)
        self.assertLessEqual(len(pack["steps"][2]["changes"]), MAX_CHANGES)
        self.assertLessEqual(len(pack["concepts"]), MAX_CONCEPTS)
        self.assertTrue(all(len(item["lines"]) <= MAX_LINES for item in pack["steps"][2]["changes"]))
        self.assertLessEqual(len(json.dumps(pack, ensure_ascii=True, separators=(",", ":")).encode()), MAX_PACK_BYTES)
        self.assertTrue(pack["truncated"])

    def test_revision_is_stable_but_changes_when_displayed_evidence_changes(self):
        ir = self.ir()
        pack = self.build(ir)
        self.assertEqual(pack["revision"], self.build(copy.deepcopy(ir))["revision"])
        ir["nodes"]["f:app.py#answer"]["diff"][-1] = "+    return 9"
        self.assertNotEqual(pack["revision"], self.build(ir)["revision"])

    def test_malformed_ir_types_do_not_escape_the_data_boundary(self):
        for ir in (None, [], {}, {"nodes": {}}, {"nodes": {"x": {"kind": [], "file": "x.py"}}}):
            with self.subTest(ir=ir):
                self.assertFalse(self.build(ir)["available"])
        ir = self.ir()
        ir["nodes"]["f:app.py#answer"].update(lang=[], domain={}, status={})
        pack = self.build(ir)
        self.assertEqual(pack["steps"][2]["changes"][0]["language"], "text")
        self.assertEqual(pack["steps"][2]["changes"][0]["domain"], "other")
        ir["nodes"]["surrogate-\ud800"] = self.node()
        self.assertTrue(self.build(ir)["available"])


if __name__ == "__main__":
    unittest.main()
