"""Routes and clients must originate in code, rather than documentation text."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "codemap" / "scripts"))

from codemap_lib.graph import CodeGraph
from codemap_lib.routes import apply_routes
from codemap_lib.scan import parse_source


class RouteFalsePositiveTest(unittest.TestCase):
    def apply(self, sources):
        files = {path: parse_source(path, text) for path, text in sources.items()}
        count = apply_routes(files, sources)
        return files, count

    def test_routes_module_own_examples_do_not_synthesize_handlers(self):
        path = "skills/codemap/scripts/codemap_lib/routes.py"
        source = (ROOT / path).read_text(encoding="utf-8")
        files, count = self.apply({path: source})
        self.assertEqual(count, 0)
        self.assertFalse(any(s.sub == "route handler" for s in files[path].symbols))

    def test_python_comments_docstrings_and_literals_are_not_routes_or_clients(self):
        source = '''"""Examples: app.get("/module-doc", handler).
requests.get("/module-client")
"""
# app.get("/comment", handler)
EXAMPLE = 'app.get("/string", handler)'
@app.get("/real/{item_id}")
def real(item_id):
    """requests.delete("/doc-client")"""
    # requests.get("/comment-client")
    example = 'requests.get("/string-client")'
    return requests.get(f"/actual/{item_id}")
'''
        files, count = self.apply({"api.py": source})
        self.assertEqual(count, 1)
        symbols = files["api.py"].symbols
        self.assertEqual([s.qual for s in symbols], ["real"])
        self.assertEqual(symbols[0].routes, ["GET /real/{}"])
        self.assertEqual({k for k, _ in symbols[0].refs}, {"api:GET /actual/{}", "route:GET /actual/{}"})
        self.assertEqual(symbols[0].line, 6)

    def test_python_docstring_constants_cannot_override_actual_url_base(self):
        source = '''BASE = "/actual"
def call(item_id):
    """Example configuration:
BASE = "/fake"
"""
    return requests.get(f"{BASE}/{item_id}")
'''
        files, _ = self.apply({"client.py": source})
        self.assertEqual({k for k, _ in files["client.py"].symbols[0].refs},
                         {"api:GET /actual/{}", "route:GET /actual/{}"})

    def test_js_comments_strings_and_templates_cannot_invent_route_or_client_calls(self):
        source = '''// app.get("/line-comment", handler)
/* app.get("/block-comment", handler) */
const example = 'app.get("/string", handler)';
const template = `app.get("/template", handler)`;
app.get("/real", (req, res) => res.send("ok"));
export function call(id) {
  // fetch("/comment-client")
  const text = 'fetch("/string-client")';
  const template = `fetch("/template-client")`;
  return fetch(`/real/${id}`, { method: "DELETE" });
}
'''
        files, count = self.apply({"web.js": source})
        self.assertEqual(count, 1)
        symbols = files["web.js"].symbols
        self.assertEqual([r for s in symbols for r in s.routes], ["GET /real"])
        call = next(s for s in symbols if s.name == "call")
        self.assertEqual({k for k, _ in call.refs}, {"api:DELETE /real/{}", "route:DELETE /real/{}"})

    def test_fake_fetch_method_option_in_comment_or_string_is_ignored(self):
        source = '''export function call() {
  return fetch("/real", { /* method: "DELETE" */ note: 'method: "POST"' });
}
'''
        files, _ = self.apply({"web.js": source})
        self.assertEqual({k for k, _ in files["web.js"].symbols[0].refs},
                         {"api:GET /real", "route:GET /real"})

    def test_real_typescript_decorators_survive_comment_filtering(self):
        source = '''/** @Controller('fake') */
@Controller('real')
export class NotesController {
  // @Get('fake')
  @Example("@Get('string')")
  @Get('actual')
  read() { return []; }
}
'''
        files, count = self.apply({"notes.ts": source})
        self.assertEqual(count, 1)
        method = next(s for s in files["notes.ts"].symbols if s.name == "read")
        self.assertEqual(method.routes, ["GET /real/actual"])

    def test_java_annotations_keep_real_prefix_and_ignore_doc_examples(self):
        source = '''/** @RequestMapping("/fake") */
@RequestMapping("/real")
public class NotesController {
    /** @GetMapping("/doc-example") */
    @GetMapping("/actual")
    public void read() { }
}
'''
        files, count = self.apply({"NotesController.java": source})
        self.assertEqual(count, 1)
        method = next(s for s in files["NotesController.java"].symbols if s.name == "read")
        self.assertEqual(method.routes, ["GET /real/actual"])

    def test_csharp_and_php_real_attributes_are_preserved(self):
        cases = {
            "NotesController.cs": '''public class NotesController {
    // [HttpGet("/fake")]
    [HttpDelete("/real")]
    public void Drop() { }
}
''',
            "NotesController.php": '''<?php
class NotesController {
    // #[Route('/fake', methods: ['GET'])]
    #[Route('/real', methods: ['DELETE'])]
    public function drop() { return null; }
}
''',
        }
        for path, source in cases.items():
            with self.subTest(path=path):
                files, count = self.apply({path: source})
                self.assertEqual(count, 1)
                self.assertEqual([r for s in files[path].symbols for r in s.routes], ["DELETE /real"])

    def test_go_raw_strings_are_not_registrations(self):
        source = '''package main
var example = `r.Get("/fake", handler)`
func handler() { }
func main() {
    // r.Get("/comment", handler)
    r.Get("/real", handler)
}
'''
        files, count = self.apply({"main.go": source})
        self.assertEqual(count, 1)
        handler = next(s for s in files["main.go"].symbols if s.name == "handler")
        self.assertEqual(handler.routes, ["GET /real"])

    def test_real_client_handler_edges_are_preserved(self):
        sources = {"api.py": '@app.get("/real")\ndef real():\n    return []\n',
                   "web.js": 'export function call() { return fetch("/real"); }\n'}
        files, count = self.apply(sources)
        self.assertEqual(count, 1)
        self.assertIn(("f:web.js#call", "f:api.py#real"), CodeGraph(files).build().edges)


if __name__ == "__main__":
    unittest.main()
