"""Source binding and adversarial boundaries for model graph corrections."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "codemap" / "scripts"))

from codemap_lib.graph import CodeGraph
from codemap_lib.model import FileInfo, Sym
from codemap_lib.review import (LABEL, MODEL_CONFIDENCE, ReviewError, apply_edge_ops,
                                build_request, review_worksheet, validate_review)
from codemap_lib.scan import parse_source


class ModelReviewTest(unittest.TestCase):
    def setUp(self):
        self.texts = {
            "lib.py": "def helper():\n    return 1\n\nclass Base:\n    pass\n",
            "app.py": "from lib import helper, Base\n\ndef caller():\n    return helper()\n\n"
                      "class Child(Base):\n    def run(self):\n        return helper()\n\n"
                      "def opaque():\n    return helper()\n",
        }
        self.files = {p: parse_source(p, text) for p, text in self.texts.items()}
        # Simulate extraction omissions without copying any target-repository code.
        self.files["app.py"].symbols = [s for s in self.files["app.py"].symbols if s.name != "opaque"]
        next(s for s in self.files["app.py"].symbols if s.name == "caller").calls = []
        self.graph = CodeGraph(self.files).build()
        self.request = build_request(self.files, self.texts, self.graph, {"mode": "repo"})

    def patch(self, **updates):
        result = {"version": 1, "analysis_id": self.request["analysis_id"],
                  "reviewed_files": ["app.py"], "symbols": [], "edges": [], "uncertain": []}
        result.update(updates)
        return result

    def quote(self, file="app.py", line=4, end=None):
        end = line if end is None else end
        return {"file": file, "line": line, "end_line": end,
                "quote": "\n".join(self.texts[file].splitlines()[line - 1:end])}

    def symbol(self, **updates):
        result = {"kind": "function", "name": "opaque", "qual": "opaque", "file": "app.py",
                  "line": 10, "end": 11, "certainty": "supported", "reason": "Declaration omitted by extraction.",
                  "evidence": [self.quote(line=10)]}
        result.update(updates)
        return result

    def edge(self, **updates):
        result = {"op": "add", "from": "f:app.py#caller", "to": "f:lib.py#helper", "kind": "calls",
                  "certainty": "supported", "reason": "Imported helper is directly called.",
                  "evidence": [self.quote()]}
        result.update(updates)
        return result

    def validate(self, patch):
        return validate_review(self.request, self.files, self.texts, self.graph, patch)

    def test_valid_symbol_and_edge_have_visible_model_provenance_and_fixed_confidence(self):
        symbols, ops, report = self.validate(self.patch(symbols=[self.symbol()], edges=[self.edge(confidence=1.0)]))
        self.assertEqual(len(symbols), 1)
        self.assertEqual(symbols[0].id, "f:app.py#opaque")
        self.assertEqual(symbols[0].provenance["label"], LABEL)
        self.assertEqual(ops[0]["confidence"], MODEL_CONFIDENCE)
        self.assertEqual(report["label"], "source-backed model inference")
        self.assertTrue(report["source_backed_only"])
        self.assertEqual(report["rejected"], [])
        self.assertNotIn(symbols[0].id, self.graph.syms, "validation must not mutate graph")
        json.dumps(report, allow_nan=False)

    def test_new_symbol_can_be_an_endpoint_in_the_same_patch(self):
        edge = self.edge(**{"from": "f:app.py#opaque", "evidence": [self.quote(line=11)]})
        symbols, ops, report = self.validate(self.patch(symbols=[self.symbol()], edges=[edge]))
        self.assertEqual(len(ops), 1)
        self.assertFalse(report["rejected"])
        self.graph.syms[symbols[0].id] = symbols[0]
        self.files["app.py"].symbols.extend(symbols)
        apply_edge_ops(self.graph, ops)
        self.assertIn((symbols[0].id, "f:lib.py#helper"), self.graph.edges)
        self.assertIn(symbols[0].id, self.graph.rank)

    def test_apply_rebuilds_all_adjacency_and_preserves_import_file_weight(self):
        before = self.graph.file_edges[("app.py", "lib.py")]
        old_edge = list(self.graph.edges[("f:app.py#Child.run", "f:lib.py#helper")])
        remove = self.edge(op="remove", **{"from": "f:app.py#Child.run", "evidence": [self.quote(line=8)]})
        _, ops, report = self.validate(self.patch(edges=[self.edge(), remove]))
        self.assertEqual(len(ops), 2)
        apply_edge_ops(self.graph, ops)
        self.assertIn("f:lib.py#helper", self.graph.callees["f:app.py#caller"])
        self.assertNotIn("f:app.py#Child.run", self.graph.callers["f:lib.py#helper"])
        self.assertEqual(self.graph.file_edges[("app.py", "lib.py")], before - old_edge[1] + 1)
        self.assertEqual(self.graph.review_removed_edges[0]["previous_edge"],
                         {"kind": old_edge[0], "weight": old_edge[1], "confidence": old_edge[2]})
        self.assertEqual(self.graph.review_edge_provenance[("f:app.py#caller", "f:lib.py#helper")]["label"], LABEL)
        self.assertAlmostEqual(sum(self.graph.rank.values()), 1.0)
        self.assertEqual(self.graph.bases["c:app.py#Child"], ["c:lib.py#Base"])

    def test_readding_parser_edge_does_not_inflate_weight_or_confidence(self):
        key = ("f:app.py#Child.run", "f:lib.py#helper")
        original = list(self.graph.edges[key])
        edge = self.edge(**{"from": key[0], "evidence": [self.quote(line=8)]})
        _, ops, report = self.validate(self.patch(edges=[edge]))
        self.assertFalse(ops)
        self.assertEqual(self.graph.edges[key], original)
        self.assertIn("already exists", report["rejected"][0]["reason"])

    def test_empty_review_is_valid_without_claiming_perfect_accuracy(self):
        symbols, ops, report = self.validate(self.patch())
        self.assertEqual((symbols, ops), ([], []))
        self.assertEqual(report["reviewed_files"], ["app.py"])
        self.assertEqual(report["status"], "reviewed")
        self.assertNotIn("confirmed", json.dumps(report))

    def test_request_is_deterministic_and_normalizes_line_endings(self):
        crlf = {p: t.replace("\n", "\r\n") for p, t in self.texts.items()}
        other = build_request(dict(reversed(list(self.files.items()))), crlf, self.graph, {"mode": "repo"})
        self.assertEqual(self.request["analysis_id"], other["analysis_id"])
        self.assertNotIn("return helper()", json.dumps(self.request))
        self.assertEqual(self.request["files"]["app.py"]["lines"], 11)

    def test_source_edits_including_outside_symbols_invalidate_patch(self):
        self.texts["app.py"] += "\nCONFIG = 2\n"
        with self.assertRaisesRegex(ReviewError, "source or graph changed"):
            self.validate(self.patch())

    def test_graph_edges_and_symbol_spans_and_status_are_bound(self):
        mutations = [lambda: self.graph.edges.clear(),
                     lambda: setattr(self.graph.syms["f:app.py#caller"], "end", 5),
                     lambda: setattr(self.graph.syms["f:app.py#caller"], "status", "modified")]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.setUp()
                mutate()
                with self.assertRaises(ReviewError):
                    self.validate(self.patch())

    def test_tampered_request_metadata_is_rejected_even_when_id_unchanged(self):
        self.request["files"]["app.py"]["sha256"] = "0" * 64
        with self.assertRaises(ReviewError):
            self.validate(self.patch())

    def test_request_numeric_types_cannot_be_tampered_using_equal_json_values(self):
        self.request["version"] = True
        with self.assertRaises(ReviewError):
            self.validate(self.patch())
        self.setUp()
        self.request["symbols"][0]["line"] = float(self.request["symbols"][0]["line"])
        with self.assertRaises(ReviewError):
            self.validate(self.patch())

    def test_version_stale_ids_and_malformed_top_level_are_rejected(self):
        patches = [self.patch(version=2), self.patch(version=True), self.patch(analysis_id="stale"),
                   self.patch(edges={}), self.patch(symbols=[{}] * 1001),
                   self.patch(uncertain=[{"reason": float("nan")}])]
        for patch in patches:
            with self.subTest(patch=str(patch)[:80]), self.assertRaises(ReviewError):
                self.validate(patch)

    def test_incorrect_quote_spans_and_optional_evidence_hash_are_rejected(self):
        variants = [dict(self.quote(), quote="return helper()"), dict(self.quote(), line=3),
                    dict(self.quote(), end_line=8), dict(self.quote(), sha256="fake"),
                    dict(self.quote(), line=True), dict(self.quote(), quote="   "),
                    dict(self.quote(), quote="    return imaginary()")]
        for proof in variants:
            with self.subTest(proof=proof):
                _, ops, report = self.validate(self.patch(edges=[self.edge(evidence=[proof])]))
                self.assertFalse(ops)
                self.assertEqual(len(report["rejected"]), 1)

    def test_optional_final_newline_and_end_line_are_accepted(self):
        proof = self.quote()
        proof.pop("end_line")
        proof["quote"] += "\n"
        _, ops, report = self.validate(self.patch(edges=[self.edge(evidence=[proof])]))
        self.assertEqual(len(ops), 1)
        self.assertFalse(report["rejected"])

    def test_edge_quotes_from_other_functions_or_files_are_rejected(self):
        for proof in (self.quote(line=8), self.quote("lib.py", 2)):
            with self.subTest(proof=proof):
                _, ops, report = self.validate(self.patch(edges=[self.edge(evidence=[proof])]))
                self.assertFalse(ops)
                self.assertIn("caller", report["rejected"][0]["reason"])

    def test_unsafe_paths_unknown_files_and_deleted_endpoints_are_rejected(self):
        for path in ("../app.py", "/app.py", "C:/app.py", "dir\\app.py", "a//b.py", "a/./b.py", "unknown.py"):
            with self.subTest(path=path):
                symbols, _, report = self.validate(self.patch(symbols=[self.symbol(file=path)]))
                self.assertFalse(symbols)
                self.assertTrue(report["rejected"])
        self.graph.syms["f:lib.py#helper"].status = "deleted"
        self.request = build_request(self.files, self.texts, self.graph, {})
        _, ops, report = self.validate(self.patch(edges=[self.edge()]))
        self.assertFalse(ops)
        self.assertIn("deleted", report["rejected"][0]["reason"])

    def test_unsafe_repository_input_is_rejected_even_if_key_exists(self):
        fi = FileInfo("../app.py", "python")
        with self.assertRaises(ReviewError):
            build_request({"../app.py": fi}, {"../app.py": "pass\n"}, CodeGraph({"../app.py": fi}).build(), {})

    def test_missing_source_and_deleted_file_cannot_receive_new_symbols(self):
        for deleted in (False, True):
            with self.subTest(deleted=deleted):
                self.setUp()
                proposal = self.symbol(evidence=[])
                if deleted:
                    self.files["app.py"].status = "deleted"
                else:
                    self.texts.pop("app.py")
                self.request = build_request(self.files, self.texts, self.graph, {})
                symbols, _, report = self.validate(self.patch(symbols=[proposal]))
                self.assertFalse(symbols)
                self.assertTrue(report["rejected"])

    def test_fabricated_names_conflicting_ids_qual_and_boolean_lines_are_rejected(self):
        variants = [self.symbol(name="imaginary", qual="imaginary"),
                    self.symbol(id="f:app.py#different"), self.symbol(qual="different"),
                    self.symbol(line=True), self.symbol(line=10, end=9),
                    self.symbol(kind="package"), self.symbol(kind=["class"]),
                    self.symbol(evidence=[]), self.symbol(reason=""),
                    self.symbol(qual="invented.opaque"), self.symbol(parent="c:lib.py#Base")]
        for symbol in variants:
            with self.subTest(symbol=symbol):
                symbols, _, report = self.validate(self.patch(symbols=[symbol]))
                self.assertFalse(symbols)
                self.assertEqual(len(report["rejected"]), 1)

    def test_existing_symbols_and_duplicate_proposals_are_not_accepted(self):
        existing = self.symbol(name="caller", qual="caller", line=3, end=4, evidence=[self.quote(line=3)])
        symbols, _, report = self.validate(self.patch(symbols=[existing]))
        self.assertFalse(symbols)
        self.assertIn("exists", report["rejected"][0]["reason"])
        symbols, _, report = self.validate(self.patch(symbols=[self.symbol(), self.symbol(end=10)]))
        self.assertFalse(symbols)
        self.assertEqual(len(report["rejected"]), 2)

    def test_duplicate_or_contradictory_edge_operations_reject_both(self):
        for second in (self.edge(), self.edge(op="remove")):
            with self.subTest(second=second):
                _, ops, report = self.validate(self.patch(edges=[self.edge(), second]))
                self.assertFalse(ops)
                self.assertEqual(len(report["rejected"]), 2)

    def test_missing_endpoints_wrong_remove_kind_and_invalid_inheritance_are_rejected(self):
        variants = [self.edge(**{"to": "f:lib.py#imaginary"}), self.edge(kind="inherits"),
                    self.edge(op="remove"), self.edge(kind="imports"),
                    self.edge(**{"to": "f:app.py#caller"}),
                    self.edge(op="remove", kind="refs", **{"from": "f:app.py#Child.run", "evidence": [self.quote(line=8)]})]
        for edge in variants:
            with self.subTest(edge=edge):
                _, ops, report = self.validate(self.patch(edges=[edge]))
                self.assertFalse(ops)
                self.assertEqual(len(report["rejected"]), 1)

    def test_uncertain_proposals_are_retained_without_modifying_graph(self):
        symbols, ops, report = self.validate(self.patch(symbols=[self.symbol(certainty="uncertain")],
                           edges=[self.edge(certainty="uncertain")],
                           uncertain=[{"file": "app.py", "line": 4, "reason": "Runtime overrides remain possible."}]))
        self.assertFalse(symbols)
        self.assertFalse(ops)
        self.assertEqual(len(report["uncertain"]), 3)
        self.assertFalse(report["rejected"])

    def test_malformed_entries_are_bounded_rejections_not_unchecked_payloads(self):
        secret_payload = "UNTRUSTED_PAYLOAD_MARKER"
        _, _, report = self.validate(self.patch(symbols=[None, [], {"name": secret_payload}],
                                    edges=[None, [], {"from": {"x": secret_payload}}],
                                    uncertain=[None, [], {"file": "app.py", "line": True, "reason": secret_payload}]))
        self.assertEqual(len(report["rejected"]), 9)
        self.assertNotIn(secret_payload, json.dumps(report))

    def test_reviewed_file_claims_and_uncertainty_must_name_current_paths_and_lines(self):
        _, _, report = self.validate(self.patch(reviewed_files=["app.py", "app.py", "../escape.py", "missing.py"],
                                      uncertain=[{"file": "app.py", "line": 999, "reason": "Unclear."}]))
        self.assertEqual(report["reviewed_files"], ["app.py"])
        self.assertEqual(len(report["rejected"]), 4)

    def test_new_class_parent_and_method_can_arrive_in_reverse_order(self):
        texts = {"component.ts": "const Panel = wrap({\n  draw() {\n    return 1;\n  }\n});\n"}
        files = {"component.ts": FileInfo("component.ts", "typescript")}
        graph = CodeGraph(files).build()
        request = build_request(files, texts, graph, {})
        proof = lambda line: {"file": "component.ts", "line": line, "quote": texts["component.ts"].splitlines()[line - 1]}
        base = {"file": "component.ts", "certainty": "supported", "reason": "Source-backed wrapper declaration."}
        owner = dict(base, kind="class", name="Panel", qual="Panel", line=1, end=5, evidence=[proof(1)])
        method = dict(base, kind="method", name="draw", qual="Panel.draw", line=2, end=4,
                      parent="c:component.ts#Panel", evidence=[proof(2)])
        symbols, _, report = validate_review(request, files, texts, graph,
                    self.patch(analysis_id=request["analysis_id"], reviewed_files=["component.ts"], symbols=[method, owner]))
        self.assertEqual([s.id for s in symbols], ["c:component.ts#Panel", "f:component.ts#Panel.draw"])
        self.assertFalse(report["rejected"])
        invalid_method = dict(method, parent="c:lib.py#Base")
        _, _, bad = validate_review(request, files, texts, graph,
                    self.patch(analysis_id=request["analysis_id"], reviewed_files=["component.ts"], symbols=[owner, invalid_method]))
        self.assertEqual(len(bad["accepted_symbols"]), 1)
        self.assertEqual(len(bad["rejected"]), 1)

    def test_nested_function_tracks_lexical_owner_separately_from_class_membership(self):
        text = "def outer():\n    def inner():\n        return 1\n    return inner()\n"
        files = {"nested.py": FileInfo("nested.py", "python", symbols=[Sym("f:nested.py#outer", "function", "outer", "outer", "nested.py", 1, 4)])}
        graph = CodeGraph(files).build()
        request = build_request(files, {"nested.py": text}, graph, {})
        item = {"kind": "function", "name": "inner", "qual": "outer.inner", "file": "nested.py", "line": 2, "end": 3,
                "certainty": "supported", "reason": "Named nested declaration.",
                "evidence": [{"file": "nested.py", "line": 2, "quote": "    def inner():"}]}
        symbols, _, report = validate_review(request, files, {"nested.py": text}, graph,
                    self.patch(analysis_id=request["analysis_id"], reviewed_files=["nested.py"], symbols=[item]))
        self.assertFalse(report["rejected"])
        self.assertEqual(symbols[0].lexical_parent, "f:nested.py#outer")
        self.assertIsNone(symbols[0].parent)

    def test_class_inheritance_add_and_remove_update_bases_and_audit(self):
        remove = self.edge(op="remove", kind="inherits", **{"from": "c:app.py#Child", "to": "c:lib.py#Base", "evidence": [self.quote(line=6)]})
        _, ops, report = self.validate(self.patch(edges=[remove]))
        self.assertFalse(report["rejected"])
        apply_edge_ops(self.graph, ops)
        self.assertFalse(self.graph.bases["c:app.py#Child"])
        self.request = build_request(self.files, self.texts, self.graph, {})
        _, ops, report = self.validate(self.patch(edges=[dict(remove, op="add")]))
        self.assertFalse(report["rejected"])
        apply_edge_ops(self.graph, ops)
        self.assertEqual(self.graph.bases["c:app.py#Child"], ["c:lib.py#Base"])
        self.assertEqual(self.graph.edges[("c:app.py#Child", "c:lib.py#Base")][2], MODEL_CONFIDENCE)
        self.assertEqual(len(self.graph.review_removed_edges), 1)

    def test_worksheet_requires_source_reading_and_discloses_semantic_limit(self):
        text = review_worksheet(self.request)
        self.assertIn(self.request["analysis_id"], text)
        self.assertIn("actual local source", text)
        self.assertIn("complete exact source lines", text)
        self.assertIn("cannot prove semantics", text)
        self.assertIn("reviewed_files", text)
        self.assertNotIn("return helper()", text)


if __name__ == "__main__":
    unittest.main()
