"""Release archives contain committed public inputs and the offline runtime."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("cmvl_release_builder", Path(__file__).resolve().parents[1] / "scripts/build_release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


@unittest.skipUnless(shutil.which("git"), "release packaging requires Git")
class ReleasePackageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cmvl-release-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.output = Path(self.temp.name) / "artifacts"
        self.git("init", "-q")
        self.git("config", "user.name", "Release fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "core.symlinks", "false")
        for name in release.REQUIRED:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('VERSION = "0.3.0-beta.1"\n' if name == release.VERSION_FILE else "public fixture\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "Public fixture")

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.root), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        return result.stdout

    def test_reproducible_archive_has_runtime_hashes_and_ignores_untracked_private_data(self):
        private = self.root / ".codemap/tasks/private/source.py"
        private.parent.mkdir(parents=True)
        private.write_text("PRIVATE_CAPTURE_SHOULD_NOT_PUBLISH", encoding="utf-8")
        (self.root / "scratch.txt").write_text("UNTRACKED_SCRATCH", encoding="utf-8")
        result = release.build(self.root, self.output, expect_version="0.3.0-beta.1")
        first = Path(result["archive"]).read_bytes()
        result2 = release.build(self.root, self.output, expect_version="0.3.0-beta.1")
        self.assertEqual(first, Path(result2["archive"]).read_bytes())
        self.assertNotIn(b"PRIVATE_CAPTURE_SHOULD_NOT_PUBLISH", first)
        with zipfile.ZipFile(result["archive"]) as archive:
            manifest = json.loads(archive.read("CMVL-0.3.0-beta.1/release-manifest.json"))
            names = set(archive.namelist())
            self.assertTrue(all(name.startswith("CMVL-0.3.0-beta.1/") for name in names))
            self.assertFalse(any(".codemap" in name or "scratch.txt" in name for name in names))
            for name, entry in manifest["files"].items():
                data = archive.read("CMVL-0.3.0-beta.1/" + name)
                self.assertEqual(entry, {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        for line in Path(result["checksums"]).read_text(encoding="ascii").splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(digest, hashlib.sha256((self.output / name).read_bytes()).hexdigest())

    def test_dirty_tracked_runtime_is_not_silently_packaged(self):
        (self.root / "install.py").write_text("uncommitted edit", encoding="utf-8")
        with self.assertRaisesRegex(release.ReleaseError, "tracked changes"):
            release.build(self.root, self.output)

    def test_mismatched_tag_and_missing_runtime_fail(self):
        with self.assertRaisesRegex(release.ReleaseError, "does not match"):
            release.build(self.root, self.output, expect_version="0.3.0-beta.2")
        self.git("rm", "skills/codemap/scripts/codemap_lib/assets/history.bundle.js")
        self.git("commit", "-qm", "Broken fixture")
        with self.assertRaisesRegex(release.ReleaseError, "missing required files"):
            release.build(self.root, self.output)

    def test_accidentally_tracked_capture_and_symlink_are_rejected(self):
        private = self.root / ".codemap/private.json"
        private.parent.mkdir()
        private.write_text("private fixture", encoding="utf-8")
        self.git("add", ".codemap/private.json")
        self.git("commit", "-qm", "Accidental private fixture")
        with self.assertRaisesRegex(release.ReleaseError, "Private/generated path"):
            release.build(self.root, self.output)
        self.git("rm", ".codemap/private.json")
        blob = self.git("hash-object", "-w", "README.md").decode("ascii").strip()
        link = self.root / "docs/unsafe-link"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.write_bytes((self.root / "README.md").read_bytes())
        self.git("update-index", "--add", "--cacheinfo", "120000", blob, "docs/unsafe-link")
        self.git("commit", "-qm", "Symlink fixture")
        with self.assertRaisesRegex(release.ReleaseError, "Symlinks"):
            release.build(self.root, self.output)

    def test_path_allowlist_rejects_unsafe_and_private_names(self):
        for name in ("../escape", "/absolute", "docs\\escape", "docs/.env", "dashboard/node_modules/a.js"):
            with self.subTest(name=name), self.assertRaises(release.ReleaseError):
                release.approved_path(name)
        self.assertFalse(release.approved_path("benchmarks/private-run/capture.json"))
        self.assertTrue(release.approved_path("benchmarks/cases/routes.json"))
        self.assertTrue(release.approved_path("skills/codemap/scripts/codemap_lib/assets/history.bundle.js"))


if __name__ == "__main__":
    unittest.main()
