"""Credentials must not survive outside source/snapshot inputs, including indirect copies."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "codemap" / "scripts"))

from codemap_lib import pipeline
from codemap_lib.redact import REDACTED, SecretRedactor
from codemap_lib.risks import RiskScanner
from codemap_lib.scan import parse_source


class RedactionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codemap-redaction-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / ".codemap"
        self.secret = "CM_CREDENTIAL_4a7b28c19d0e6f325890abcd"

    def analyze(self, **kwargs):
        return pipeline.analyze(self.root, log=lambda _: None, **kwargs)

    def scan(self, text):
        scanner = RiskScanner()
        fi = parse_source("app.py", text)
        scanner.scan_files({"app.py": fi}, {"app.py": text})
        return scanner.flags

    def assert_artifacts_clean(self, forbidden):
        for name in ("ir.json", "worksheet.md", "codemap.md", "codemap.html", "codemap.diff", "cache.json", "labels.json", "labels.previous.json"):
            path = self.out / name
            if path.exists():
                with self.subTest(artifact=name):
                    blob = path.read_text(encoding="utf-8")
                    for value in forbidden:
                        self.assertNotIn(value, blob)

    def test_changed_credentials_and_copied_values_redacted_in_every_output(self):
        source = self.root / "app.py"
        source.write_text('def connect():\n    return "safe"\n', encoding="utf-8")
        pipeline.mark(self.root, log=lambda _: None)
        source.write_text(f'def connect():\n    api_key = "{self.secret}"\n    return api_key\n', encoding="utf-8")
        ir = self.analyze(tier="video")
        self.assertIn("secret.hardcoded", [f["rule"] for f in ir["risks"]])
        nid = next(k for k, n in ir["nodes"].items() if n["kind"] == "function")
        labels = {"items": {nid: ["Connect", f"Uses {self.secret}"]}, "why": f"Authenticate using {self.secret}",
                  "beats": {"b1": f"Connect with {self.secret}"}}
        (self.out / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
        pipeline.render(self.out, log=None)
        self.assert_artifacts_clean([self.secret])
        rendered = json.loads((self.out / "ir.json").read_text(encoding="utf-8"))
        self.assertIn(REDACTED, rendered["nodes"][nid]["sum"])
        # Original inputs stay intact so real change detection remains truthful.
        self.assertIn(self.secret, source.read_text(encoding="utf-8"))

    def test_removed_credential_and_stale_labels_are_redacted(self):
        source = self.root / "app.py"
        source.write_text(f'def connect():\n    password = "{self.secret}"\n    return password\n', encoding="utf-8")
        pipeline.mark(self.root, log=lambda _: None)
        self.out.mkdir(exist_ok=True)
        (self.out / "labels.json").write_text(json.dumps({"why": f"Replaced {self.secret}"}), encoding="utf-8")
        source.write_text('def connect():\n    return "safe"\n', encoding="utf-8")
        self.analyze(tier="full")
        self.assert_artifacts_clean([self.secret])

    def test_same_line_nonsecret_finding_cannot_expose_credential(self):
        flags = self.scan(f'DEBUG = True; api_key = "{self.secret}"\n')
        self.assertEqual({f["rule"] for f in flags}, {"code.debug_enabled", "secret.hardcoded"})
        self.assertNotIn(self.secret, json.dumps(flags))
        self.assertTrue(all(REDACTED in f["text"] for f in flags))

    def test_long_credentials_masked_before_snippet_or_signature_truncation(self):
        long_secret = "CM_LONG_CREDENTIAL_" + "a1B2c3D4" * 100
        flags = self.scan(f'API_KEY = "{long_secret}" # sample configuration\n')
        self.assertEqual([f["rule"] for f in flags], ["secret.hardcoded"])
        self.assertNotIn(long_secret[:24], json.dumps(flags))
        (self.root / "app.py").write_text(f'def connect(api_key="{long_secret}"):\n    return api_key\n', encoding="utf-8")
        self.analyze(repo_mode=True, tier="full", audit=True)
        self.assert_artifacts_clean([long_secret[:24]])

    def test_placeholder_comment_does_not_suppress_real_credential(self):
        flags = self.scan(f'API_KEY = "{self.secret}" # sample placeholder\n')
        self.assertEqual([f["rule"] for f in flags], ["secret.hardcoded"])
        self.assertEqual(self.scan('PASSWORD = "changeme"\nAPI_KEY = "${API_KEY}"\n'), [])

    def test_private_key_body_and_unquoted_env_values_are_redacted(self):
        body = "MIIE_CREDENTIAL_REVIEW_ABCD1234567890"
        pem = f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----"
        (self.root / "app.py").write_text('def connect():\n    pass\n', encoding="utf-8")
        pipeline.mark(self.root, log=lambda _: None)
        (self.root / "key.pem").write_text(pem, encoding="utf-8")
        (self.root / ".env").write_text(f"API_KEY={self.secret}\nPORT=8000\n", encoding="utf-8")
        self.analyze(tier="full")
        self.assert_artifacts_clean([body, self.secret])

    def test_render_sanitizes_legacy_ir_before_rebuilding_outputs(self):
        (self.root / "app.py").write_text("def connect():\n    pass\n", encoding="utf-8")
        ir = self.analyze(repo_mode=True)
        nid = next(k for k, n in ir["nodes"].items() if n["kind"] == "function")
        ir["nodes"][nid]["diff"] = [f'+api_key = "{self.secret}"']
        ir["nodes"][nid]["sum"] = f"Uses {self.secret}"
        (self.out / "ir.json").write_text(json.dumps(ir), encoding="utf-8")
        pipeline.render(self.out, log=None)
        self.assert_artifacts_clean([self.secret])

    def test_audit_includes_generic_config_docs_and_generated_text(self):
        (self.root / "settings.json").write_text(json.dumps({"api_key": self.secret}), encoding="utf-8")
        (self.root / "credentials.md").write_text(f'# Credentials\nAPI_KEY = "{self.secret}"\n', encoding="utf-8")
        (self.root / "generated.py").write_text(f'# @generated\nAPI_KEY = "{self.secret}"\n', encoding="utf-8")
        ir = self.analyze(repo_mode=True, audit=True)
        self.assertEqual({f["file"] for f in ir["risks"]}, {"settings.json", "credentials.md", "generated.py"})
        self.assert_artifacts_clean([self.secret])

    def test_redaction_preserves_key_and_hides_tokens_even_in_comments(self):
        token = "ghp_" + "a1B2c3D4" * 5
        redactor = SecretRedactor()
        text = f'clientSecret = "{self.secret}"; // sample {token}'
        redactor.add_text(text)
        cleaned = redactor.text(text)
        self.assertIn('clientSecret = "[REDACTED]"', cleaned)
        self.assertNotIn(token, cleaned)
        self.assertNotIn(self.secret, cleaned)

    def test_credentials_matching_schema_words_do_not_corrupt_the_map(self):
        for password in ("children", "function", "module", "root", "a"):
            with self.subTest(password=password):
                (self.root / "app.py").write_text(
                    f'def connect():\n    password = "{password}"\n    return password\n', encoding="utf-8")
                ir = self.analyze(repo_mode=True)
                self.assertEqual(ir["nodes"]["root"]["kind"], "root")
                function = ir["nodes"]["f:app.py#connect"]
                self.assertEqual(function["kind"], "function")
                self.assertEqual(function["status"], "same")
                self.assertEqual(function["parent"], "m:app.py")
                self.assertIn("children", function)

    def test_prefixed_keys_and_plain_configuration_credentials(self):
        (self.root / "app.py").write_text("def connect():\n    pass\n", encoding="utf-8")
        pipeline.mark(self.root, log=lambda _: None)
        (self.root / "app.py").write_text(f'def connect():\n    DB_PASSWORD = "{self.secret}"\n', encoding="utf-8")
        (self.root / "settings.yaml").write_text(f"password: {self.secret}\n", encoding="utf-8")
        (self.root / ".env").write_text(f"DATABASE_PASSWORD={self.secret}\n", encoding="utf-8")
        ir = self.analyze(tier="full")
        self.assertEqual({f["file"] for f in ir["risks"]}, {"app.py", "settings.yaml", ".env"})
        self.assert_artifacts_clean([self.secret])

    def test_existing_backups_worksheets_and_cache_cleaned_without_new_labels(self):
        (self.root / "app.py").write_text(f'API_KEY = "{self.secret}"\n', encoding="utf-8")
        self.out.mkdir()
        (self.out / "labels.previous.json").write_text(json.dumps({"why": f"Uses {self.secret}"}), encoding="utf-8")
        (self.out / "cache.json").write_text(json.dumps({"old-hash": ["Connect", f"Uses {self.secret}"]}), encoding="utf-8")
        (self.out / "worksheet.md").write_text(f'API_KEY = "{self.secret}"\n', encoding="utf-8")
        self.analyze(repo_mode=True, tier="full")
        self.assert_artifacts_clean([self.secret])

    def test_legacy_private_key_split_across_diff_lines_is_redacted(self):
        body = "MIIE_LEGACY_REVIEW_abcdefghijklmnopqrstuvwxyz"
        diff = ["+-----BEGIN PRIVATE KEY-----", "+" + body, "+-----END PRIVATE KEY-----"]
        redactor = SecretRedactor()
        redactor.add_object({"diff": diff})
        self.assertNotIn(body, json.dumps(redactor.clean({"diff": diff})))

    def test_render_recovers_source_identity_after_display_path_redaction(self):
        (self.root / "appword.py").write_text('password = "appword"\ndef connect():\n    return password\n', encoding="utf-8")
        self.analyze(repo_mode=True)
        nid = "f:appword.py#connect"
        (self.out / "labels.json").write_text(json.dumps({"items": {nid: ["Connect", "Uses appword"]}}), encoding="utf-8")
        pipeline.render(self.out, log=None)
        ir = json.loads((self.out / "ir.json").read_text(encoding="utf-8"))
        self.assertIn(REDACTED, ir["nodes"][nid]["sum"])

    def test_plain_configuration_passphrases_redact_the_entire_value(self):
        password = "correct horse review battery staple"
        (self.root / "settings.yaml").write_text(f"password: {password} # local credential\n", encoding="utf-8")
        ir = self.analyze(repo_mode=True, audit=True, tier="full")
        self.assert_artifacts_clean([password, "horse review battery staple"])
        self.assertIn(REDACTED, ir["risks"][0]["text"])


if __name__ == "__main__":
    unittest.main()
