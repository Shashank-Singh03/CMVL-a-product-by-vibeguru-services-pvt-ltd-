"""A tagged beta identifies the generated UI without changing capture schemas."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/codemap/scripts"))
from codemap_lib import VERSION, history
from codemap_lib.render import render_html


class BetaVersionTest(unittest.TestCase):
    def test_install_and_dashboard_sources_use_one_application_version(self):
        package = json.loads((ROOT / "dashboard/package.json").read_text(encoding="utf-8"))
        lock = json.loads((ROOT / "dashboard/package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(package["version"], VERSION)
        self.assertEqual(lock["version"], VERSION)
        self.assertEqual(lock["packages"][""]["version"], VERSION)
        skill = (ROOT / "skills/codemap/SKILL.md").read_text(encoding="utf-8")
        self.assertIn(f'version: "{VERSION}"', skill)

    def test_new_history_exposes_generator_version_without_changing_schema(self):
        with tempfile.TemporaryDirectory(prefix="cmvl-version-") as directory:
            root = Path(directory)
            page = history.write_history(root).read_text(encoding="utf-8")
            island = re.search(r'<script id="codemap-history-data" type="application/json">(.*?)</script>', page, re.S)
            data = json.loads(island[1])
            self.assertEqual(data["version"], 1)
            self.assertEqual(data["appVersion"], VERSION)
            self.assertEqual(data["tasks"], [])
            self.assertIn(VERSION, page)

    def test_render_preserves_historical_analysis_version_and_stamps_current_ui(self):
        ir = {"version": "historical-capture", "repo": "fixture", "nodes": {}, "views": [], "beats": []}
        page = render_html(ir)
        self.assertIn("CMVL " + VERSION, page)
        self.assertIn('"version":"historical-capture"', page)
        self.assertEqual(ir["version"], "historical-capture")

    def test_project_install_includes_working_versioned_offline_runtime(self):
        with tempfile.TemporaryDirectory(prefix="cmvl-install-version-") as directory:
            project = Path(directory)
            result = subprocess.run([sys.executable, str(ROOT / "install.py"), "--agents", "agents", "--project", str(project)],
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            skill = project / ".agents/skills/codemap"
            version = subprocess.run([sys.executable, str(skill / "scripts/codemap.py"), "--version"],
                                     capture_output=True, text=True, encoding="utf-8", cwd=project)
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertIn(VERSION, version.stdout)
            self.assertTrue((skill / "assets/studio.css").is_file())
            self.assertTrue((skill / "assets/Manrope-OFL.txt").is_file())
            self.assertIn(VERSION, (skill / "scripts/codemap_lib/assets/history.bundle.js").read_text(encoding="utf-8"))
            self.assertFalse((project / ".claude/settings.json").exists(), "Ordinary install must not enable hooks")
