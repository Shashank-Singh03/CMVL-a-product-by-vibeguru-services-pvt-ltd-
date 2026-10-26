"""A skipped source read must never be reported as a source deletion."""
from __future__ import annotations

import sys
import stat
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "codemap" / "scripts"))

from codemap_lib.changes import collect_changes, mark, resolve_scope
from codemap_lib.scan import MAX_BYTES


class SnapshotReadFailureTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="codemap-snapshot-read-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.out = self.root / ".codemap"
        self.source = self.root / "sample.py"
        self.source.write_text("def sample():\n    return 1\n", encoding="utf-8")
        mark(self.root, self.out, is_git=False)
        self.scope = resolve_scope(self.root, False, None, False, self.out)

    def collect(self, current=None):
        return collect_changes(self.root, self.scope, ["sample.py"] if current is None else current, self.out)

    def assert_skipped(self, changes, reason):
        self.assertNotIn("sample.py", changes.files)
        self.assertIn({"path": "sample.py", "reason": reason}, changes.skipped)

    def test_existing_file_growing_past_read_limit_is_not_deleted(self):
        self.source.write_bytes(b"x" * (MAX_BYTES + 1))
        self.assert_skipped(self.collect(), "too_large")

    def test_existing_file_becoming_binary_is_not_deleted(self):
        self.source.write_bytes(b"binary\x00source")
        self.assert_skipped(self.collect(), "binary")

    def test_permission_failure_during_current_read_is_not_deleted(self):
        original = Path.read_bytes
        def read(path):
            if path == self.source:
                raise PermissionError("simulated source read failure")
            return original(path)
        with mock.patch.object(Path, "read_bytes", read):
            self.assert_skipped(self.collect(), "unreadable")

    def test_existing_path_omitted_from_discovery_is_not_deleted(self):
        self.source.write_text("def sample():\n    return 2\n", encoding="utf-8")
        self.assert_skipped(self.collect(current=[]), "not_scanned")

    def test_directory_replacing_file_is_reported_as_skipped(self):
        self.source.unlink()
        self.source.mkdir()
        self.assert_skipped(self.collect(current=[]), "not_file")

    def test_broken_symlink_entry_is_not_reported_as_deleted(self):
        original_stat, original_lstat = Path.stat, Path.lstat
        def source_stat(path, *args, **kwargs):
            if path == self.source:
                raise FileNotFoundError("simulated dangling symlink target")
            return original_stat(path, *args, **kwargs)
        def source_lstat(path):
            return mock.Mock(st_mode=stat.S_IFLNK) if path == self.source else original_lstat(path)
        with mock.patch.object(Path, "stat", source_stat), mock.patch.object(Path, "lstat", source_lstat):
            self.assert_skipped(self.collect(current=[]), "not_file")

    def test_unavailable_baseline_does_not_make_existing_file_added(self):
        snapshot = self.out / "baseline" / "sample.py"
        snapshot.unlink()
        self.source.write_text("def sample():\n    return 2\n", encoding="utf-8")
        self.assert_skipped(self.collect(), "baseline_unavailable")

    def test_actual_absence_remains_a_deletion(self):
        self.source.unlink()
        changes = self.collect(current=[])
        self.assertEqual(changes.files["sample.py"].status, "deleted")
        self.assertEqual(changes.files["sample.py"].dele, 2)
        self.assertEqual(changes.skipped, [])

    def test_dangling_symlink_entry_is_not_mistaken_for_deletion(self):
        # No Windows symlink privilege is needed to exercise this distinction.
        with mock.patch.object(Path, "stat", side_effect=FileNotFoundError), \
                mock.patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFLNK)):
            self.assert_skipped(self.collect(current=[]), "not_file")

    def test_unchanged_crlf_source_stays_unchanged(self):
        self.source.write_bytes(b"def sample():\r\n    return 1\r\n")
        changes = self.collect()
        self.assertEqual(changes.files, {})
        self.assertEqual(changes.skipped, [])


if __name__ == "__main__":
    unittest.main()
