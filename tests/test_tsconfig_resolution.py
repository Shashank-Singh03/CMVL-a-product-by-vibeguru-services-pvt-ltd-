"""Actual-file fixtures for TypeScript configuration-based module links."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "codemap" / "scripts"))

from codemap_lib import pipeline
from codemap_lib.graph import CodeGraph
from codemap_lib.scan import parse_source
from codemap_lib.tsconfig import _jsonc


class TSConfigResolutionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="codemap-tsconfig-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.sources = {}

    def write(self, path, text):
        full = self.root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text, encoding="utf-8")
        if full.suffix in (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts"):
            self.sources[path] = text

    def config(self, options, path="tsconfig.json", **extra):
        self.write(path, json.dumps(dict(extra, compilerOptions=options)))

    def graph(self):
        return CodeGraph({p: parse_source(p, s) for p, s in self.sources.items()}, root=self.root).build()

    def caller(self, spec, path="app/main.ts"):
        self.write(path, f'import {{ performWork }} from "{spec}";\nexport function start() {{ return performWork(); }}\n')

    def target(self, path):
        self.write(path, "export function performWork() { return 1; }\n")

    def assert_target(self, target, importer="app/main.ts"):
        self.assertIn((f"f:{importer}#start", f"f:{target}#performWork"), self.graph().edges)

    def test_exact_alias_reaches_call_graph_through_real_analysis(self):
        self.config({"paths": {"@work": ["src/work.ts"]}})
        self.caller("@work")
        self.target("src/work.ts")
        ir = pipeline.analyze(self.root, repo_mode=True, tier="free", log=lambda *_: None)
        self.assertIn(["f:src/work.ts#performWork", "performWork"], ir["nodes"]["f:app/main.ts#start"]["calls"])
        # Normal repo maps need not include tsconfig as a map node to use it.
        self.assertFalse(any(n.get("file") == "tsconfig.json" for n in ir["nodes"].values()))

    def test_jsonc_wildcard_and_index_target_use_base_url(self):
        self.write("tsconfig.json", '''{
          // JSONC includes comments, strings resembling comments and trailing commas.
          "notes": "https://example.invalid/a/*b*/,}",
          "compilerOptions": {"baseUrl": "src", "paths": {"@work/*": ["jobs/*",],},},
        }''')
        self.caller("@work/queue")
        self.target("src/jobs/queue/index.ts")
        self.assert_target("src/jobs/queue/index.ts")

    def test_base_url_bare_import_does_not_change_relative_imports(self):
        self.config({"baseUrl": "src"})
        self.caller("jobs/work")
        self.target("src/jobs/work.ts")
        self.assert_target("src/jobs/work.ts")
        self.caller("./jobs/work")
        self.target("app/jobs/work.ts")
        self.assert_target("app/jobs/work.ts")

    def test_longest_prefix_and_exact_alias_precedence(self):
        self.config({"paths": {"*": ["fallback/*"], "@work/*": ["broad/*"],
                               "@work/special/*": ["specific/*"], "@work/special/task": ["exact.ts"]}})
        self.caller("@work/special/task")
        for path in ("fallback/@work/special/task.ts", "broad/special/task.ts", "specific/task.ts", "exact.ts"):
            self.target(path)
        self.assert_target("exact.ts")
        self.caller("@work/special/other")
        self.target("specific/other.ts")
        self.target("broad/special/other.ts")
        self.assert_target("specific/other.ts")

    def test_ordered_mapping_fallbacks_keep_first_success(self):
        self.config({"paths": {"@work": ["missing/work", "first/work", "second/work"]}})
        self.caller("@work")
        self.target("first/work.ts")
        self.target("second/work.ts")
        self.assert_target("first/work.ts")

    def test_nearest_config_overrides_parent_mapping(self):
        self.config({"paths": {"@work": ["root/work.ts"]}})
        self.config({"paths": {"@work": ["local/work.ts"]}}, path="app/tsconfig.json")
        self.caller("@work")
        self.target("root/work.ts")
        self.target("app/local/work.ts")
        self.assert_target("app/local/work.ts")

    def test_relative_extends_preserves_origin_without_base_url(self):
        self.config({"paths": {"@work": ["../shared/work.ts"]}}, path="config/base.json")
        self.write("app/tsconfig.json", '{"extends":"../config/base"}')
        self.caller("@work")
        self.target("shared/work.ts")
        self.assert_target("shared/work.ts")

    def test_extended_base_url_keeps_its_declaring_directory(self):
        self.config({"baseUrl": "../shared", "paths": {"@work": ["work"]}}, path="config/base.json")
        self.write("app/tsconfig.json", '{"extends":"../config/base.json"}')
        self.caller("@work")
        self.target("shared/work.ts")
        self.assert_target("shared/work.ts")

    def test_child_paths_replace_parent_paths(self):
        self.config({"paths": {"@work": ["shared/work.ts"]}}, path="base.json")
        self.config({"paths": {"@other": ["local/work.ts"]}}, path="app/tsconfig.json", extends="../base.json")
        self.caller("@work")
        self.target("shared/work.ts")
        self.assertEqual(self.graph().edges, {})

    def test_jsconfig_and_js_extension_substitution(self):
        self.config({"paths": {"@work": ["src/work.js"]}}, path="jsconfig.json")
        self.caller("@work", path="app/main.js")
        self.target("src/work.ts")
        self.assert_target("src/work.ts", importer="app/main.js")

    def test_ambiguous_source_candidates_and_equal_prefix_patterns_remain_unresolved(self):
        self.config({"paths": {"@work": ["src/work", "other/work"]}})
        self.caller("@work")
        for path in ("src/work.ts", "src/work.tsx", "other/work.ts"):
            self.target(path)
        self.assertEqual(self.graph().edges, {})
        self.config({"paths": {"@work/*": ["src/work.ts"], "@work/*/task": ["other/work.ts"]}})
        self.caller("@work/job/task")
        self.assertEqual(self.graph().edges, {})

    def test_explicit_missing_alias_does_not_use_conventional_src_fallback(self):
        self.config({"paths": {"@/*": ["missing/*"]}})
        self.caller("@/work")
        self.target("src/work.ts")
        self.assertEqual(self.graph().edges, {})

    def test_invalid_or_unsupported_config_fails_closed(self):
        self.caller("@/work")
        self.target("src/work.ts")
        for config in (
            '{"compilerOptions": {',
            '{"extends":"./tsconfig.json"}',
            '{"extends":"@tsconfig/node20/tsconfig.json"}',
            '{"extends":["./base.json"]}',
            '{"compilerOptions":{"paths":{"@/*":["../outside/*"]}}}',
            '{"compilerOptions":{"paths":{"@/*":["src/*"],"@/*":["other/*"]}}}',
        ):
            with self.subTest(config=config):
                self.write("tsconfig.json", config)
                self.assertEqual(self.graph().edges, {})

    def test_reexported_aliases_resolve_through_barrel(self):
        self.config({"paths": {"@work": ["src/barrel"], "@impl": ["src/work"]}})
        self.caller("@work")
        self.write("src/barrel.ts", 'export { performWork } from "@impl";\n')
        self.target("src/work.ts")
        self.assert_target("src/work.ts")

    def test_jsonc_does_not_accept_unterminated_comments(self):
        with self.assertRaises(ValueError):
            _jsonc("{/*/}")


if __name__ == "__main__":
    unittest.main()
