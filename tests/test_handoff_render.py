"""Portable review links are opt-in metadata, without absolute checkout paths."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "codemap" / "scripts"))

from codemap_lib.render import html_payload, render_html, render_markdown  # noqa: E402


def sample_ir(artifacts=None):
    ir = {
        "root": "C:/Users/private-owner/checkout",
        "repo": "sample", "generated": "now", "version": "test", "tier": "free",
        "scope": {"mode": "diff", "label": "HEAD", "files": 1, "add": 2, "del": 1},
        "stats": {"files": 1, "symbols": 1}, "views": [], "beats": [], "risks": [],
        "nodes": {
            "function": {"name": "serve", "kind": "function", "status": "modified",
                         "file": "src/server.py", "line": 3, "qual": "serve", "rank": 1},
        },
    }
    if artifacts is not None:
        ir["artifacts"] = artifacts
    return ir


class HandoffRenderTest(unittest.TestCase):
    def test_relative_metadata_is_available_in_html_and_markdown(self):
        ir = sample_ir({"diff": "codemap.diff", "source_base": "../my repo/"})
        payload = html_payload(ir)
        self.assertEqual(payload["artifacts"], ir["artifacts"])
        self.assertNotIn("private-owner", json.dumps(payload))
        self.assertIn("[Open the full unified diff](codemap.diff)", render_markdown(ir))
        self.assertIn("recognized credentials redacted", render_markdown(ir))
        self.assertIn("For review only; not an apply-ready patch.", render_markdown(ir))
        self.assertIn('"source_base":"../my repo/"', render_html(ir))

    def test_older_maps_do_not_advertise_a_missing_diff(self):
        ir = sample_ir()
        self.assertEqual(html_payload(ir)["artifacts"], {})
        self.assertNotIn("[Open the full unified diff]", render_markdown(ir))

    def test_absolute_or_external_metadata_is_not_published(self):
        for source_base in ("C:/Users/private-owner/", "file:///private/", "/private/",
                            "\\\\server\\private\\", "https://example.test/", "../bad\npath/"):
            with self.subTest(source_base=source_base):
                ir = sample_ir({"source_base": source_base, "diff": "https://example.test/diff",
                                "absolute_root": "C:/Users/private-owner/"})
                self.assertEqual(html_payload(ir)["artifacts"], {})
                self.assertNotIn("private-owner", json.dumps(html_payload(ir)))


if __name__ == "__main__":
    unittest.main()
