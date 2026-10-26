"""Review artifacts retain all collected text changes and redact credentials."""
from __future__ import annotations

import sys
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "codemap" / "scripts"))
from codemap_lib import pipeline


class ReviewDiffTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codemap-review-diff-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / ".codemap"

    def test_complete_diff_retains_changes_beyond_display_cap(self):
        source = self.root / "app.py"
        source.write_text("def work():\n    return 0\n", encoding="utf-8")
        pipeline.mark(self.root, log=lambda _: None)
        source.write_text("def work():\n" + "".join(f"    value_{i} = {i}\n" for i in range(80)) +
                          "    return value_79\n", encoding="utf-8")
        ir = pipeline.analyze(self.root, tier="free", log=lambda _: None)
        diff = (self.out / "codemap.diff").read_text(encoding="utf-8")
        self.assertIn("+    value_79 = 79", diff)
        self.assertIn("-    return 0", diff)
        self.assertIn("@@", diff)
        self.assertEqual(ir["artifacts"]["diff"], "codemap.diff")
        self.assertEqual(ir["artifacts"]["source_base"], "../")
        self.assertNotIn("value_79 = 79", "\n".join(ir["nodes"]["f:app.py#work"]["diff"]))

    def test_added_deleted_and_unknown_text_are_included(self):
        (self.root / "removed.py").write_text("def old():\n    pass\n", encoding="utf-8")
        pipeline.mark(self.root, log=lambda _: None)
        (self.root / "removed.py").unlink()
        (self.root / "new.py").write_text("def added():\n    pass\n", encoding="utf-8")
        (self.root / "runbook.custom").write_text("Remember the deploy step.\n", encoding="utf-8")
        pipeline.analyze(self.root, tier="free", log=lambda _: None)
        diff = (self.out / "codemap.diff").read_text(encoding="utf-8")
        self.assertIn("--- a/removed.py\n+++ /dev/null", diff)
        self.assertIn("--- /dev/null\n+++ b/new.py", diff)
        self.assertIn("+Remember the deploy step.", diff)

    def test_full_diff_and_rerenders_redact_old_and_new_credentials(self):
        old = "CM_PREVIOUS_CREDENTIAL_1234567890"
        new = "CM_CURRENT_CREDENTIAL_1234567890"
        source = self.root / "app.py"
        source.write_text(f'PASSWORD = "{old}"\n', encoding="utf-8")
        pipeline.mark(self.root, log=lambda _: None)
        source.write_text(f'PASSWORD = "{new}"\n', encoding="utf-8")
        pipeline.analyze(self.root, tier="full", log=lambda _: None)
        pipeline.render(self.out, log=None)
        diff = (self.out / "codemap.diff").read_text(encoding="utf-8")
        self.assertNotIn(old, diff)
        self.assertNotIn(new, diff)
        self.assertIn('[REDACTED]', diff)

    def test_repo_map_has_no_diff_link_and_custom_output_uses_relative_source_base(self):
        (self.root / "app.py").write_text("def work():\n    pass\n", encoding="utf-8")
        out = self.root / "reports" / "map"
        ir = pipeline.analyze(self.root, out=out, repo_mode=True, tier="free", log=lambda _: None)
        self.assertNotIn("diff", ir["artifacts"])
        self.assertEqual(ir["artifacts"]["source_base"], "../../")
        self.assertNotIn(str(self.root), (out / "codemap.html").read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("git"), "large tracked-file comparison requires git")
    def test_large_unmapped_file_still_redacts_changed_private_key_body(self):
        def git(*args):
            subprocess.run(["git", *args], cwd=self.root, capture_output=True, check=True)
        git("init")
        git("config", "user.name", "Codemap test")
        git("config", "user.email", "codemap@example.invalid")
        source = self.root / "large.py"
        old = "MIIE_PREVIOUS_REVIEW_abcdefghijklmnopqrstuvwxyz1234567890"
        new = "MIIE_CURRENT_REVIEW_abcdefghijklmnopqrstuvwxyz9876543210"
        text = ("# " + "padding" * 28 + "\n") * 3400 + f"KEY = '''-----BEGIN PRIVATE KEY-----\n{old}\n-----END PRIVATE KEY-----'''\n"
        self.assertGreater(len(text.encode("utf-8")), 600_000)
        source.write_text(text, encoding="utf-8")
        (self.root / "app.py").write_text("def work():\n    return 0\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-m", "baseline")
        source.write_text(text.replace(old, new), encoding="utf-8")
        (self.root / "app.py").write_text("def work():\n    return 1\n", encoding="utf-8")
        ir = pipeline.analyze(self.root, tier="free", log=lambda _: None)
        self.assertIn("large.py", [f["path"] for f in ir["other_files"]])
        diff = (self.out / "codemap.diff").read_text(encoding="utf-8")
        self.assertNotIn(old, diff)
        self.assertNotIn(new, diff)
        self.assertIn("[REDACTED]", diff)

    def test_optional_source_directory_cannot_publish_a_known_credential(self):
        secret = "CM_DIRECTORY_CREDENTIAL_1234567890"
        root = self.root / secret
        root.mkdir()
        (root / "app.py").write_text(f'API_KEY = "{secret}"\n', encoding="utf-8")
        out = self.root / "reports"
        ir = pipeline.analyze(root, out=out, repo_mode=True, tier="free", log=lambda _: None)
        self.assertNotIn("source_base", ir["artifacts"])
        self.assertNotIn(secret, (out / "codemap.html").read_text(encoding="utf-8"))

    def test_oversized_snapshot_change_is_disclosed_instead_of_reported_deleted(self):
        (self.root / "grow.py").write_text("value = 0\n", encoding="utf-8")
        (self.root / "app.py").write_text("def work():\n    return 0\n", encoding="utf-8")
        pipeline.mark(self.root, log=lambda _: None)
        (self.root / "grow.py").write_text("# " + "padding" * 100_000, encoding="utf-8")
        (self.root / "app.py").write_text("def work():\n    return 1\n", encoding="utf-8")
        ir = pipeline.analyze(self.root, tier="free", log=lambda _: None)
        skipped = next(item for item in ir["other_files"] if item["path"] == "grow.py")
        self.assertEqual(skipped["status"], "skipped")
        self.assertEqual(ir["scope"]["files"], 2)
        diff = (self.out / "codemap.diff").read_text(encoding="utf-8")
        self.assertIn("# Omitted grow.py:", diff)
        self.assertNotIn("--- a/grow.py", diff)

    def test_only_skipped_snapshot_change_reports_an_honest_error(self):
        (self.root / "grow.py").write_text("value = 0\n", encoding="utf-8")
        pipeline.mark(self.root, log=lambda _: None)
        (self.root / "grow.py").write_text("# " + "padding" * 100_000, encoding="utf-8")
        with self.assertRaisesRegex(pipeline.CodemapError, "no readable changes.*skipped"):
            pipeline.analyze(self.root, tier="free", log=lambda _: None)


if __name__ == "__main__":
    unittest.main()
