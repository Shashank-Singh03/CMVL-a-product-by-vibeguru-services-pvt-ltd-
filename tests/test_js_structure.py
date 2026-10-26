"""Small original fixtures for React wrappers and explicit Express registrations."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "codemap" / "scripts"))

from codemap_lib.graph import CodeGraph
from codemap_lib.parse_js import parse_js
from codemap_lib.routes import apply_routes


class ReactWrapperTest(unittest.TestCase):
    def test_generic_forward_ref_components_have_exports_spans_and_jsx_links(self):
        text = '''import * as React from "react";
import { joinClasses } from "./style";
const Field = React.forwardRef<
  HTMLInputElement, React.ComponentProps<"input">
>(({ className, ...props }, ref) => (
  <input ref={ref} className={joinClasses(className)} {...props} />
));
export { Field };
export function Form() { return <Field />; }
'''
        fi = parse_js("form.tsx", text)
        field = next(s for s in fi.symbols if s.name == "Field")
        self.assertTrue(field.exported)
        self.assertEqual((field.line, field.end), (3, 7))
        self.assertIn(("joinClasses",), [chain for chain, _ in field.calls])
        graph = CodeGraph({
            "form.tsx": fi,
            "style.ts": parse_js("style.ts", "export function joinClasses(x) { return x; }"),
            "page.tsx": parse_js("page.tsx", 'import { Field } from "./form"; export function Screen() { return <Field />; }'),
        }).build()
        self.assertIn(("f:form.tsx#Form", "f:form.tsx#Field"), graph.edges)
        self.assertIn(("f:form.tsx#Field", "f:style.ts#joinClasses"), graph.edges)
        self.assertIn(("f:page.tsx#Screen", "f:form.tsx#Field"), graph.edges)

    def test_imported_alias_and_block_callback_are_supported(self):
        fi = parse_js("panel.tsx", '''import { forwardRef as attachRef } from "react";
export const Panel = attachRef<HTMLElement, { check: () => boolean }>((props, ref) => {
  return <section ref={ref}>{props.check()}</section>;
});
''')
        panel = next(s for s in fi.symbols if s.name == "Panel")
        self.assertTrue(panel.exported)
        self.assertEqual((panel.line, panel.end), (2, 4))

    def test_comments_literals_and_unproven_wrapper_names_are_not_components(self):
        text = '''import * as React from "react";
// const Ghost = React.forwardRef((p, r) => <div />);
const example = "const Fake = React.forwardRef((p, r) => <div />)";
const Other = widgets.forwardRef((p, r) => <div />);
'''
        self.assertEqual(parse_js("example.tsx", text).symbols, [])

    def test_import_examples_in_strings_do_not_prove_a_react_binding(self):
        fi = parse_js("example.tsx", '''const docs = "import * as React from 'react'";
const example = "const React = require('react')";
const Unknown = React.forwardRef((props, ref) => <div ref={ref} />);
''')
        self.assertEqual(fi.imports, [])
        self.assertEqual(fi.symbols, [])


class ExpressStructureTest(unittest.TestCase):
    def graph(self, sources):
        files = {path: parse_js(path, text) for path, text in sources.items()}
        count = apply_routes(files, sources)
        return files, CodeGraph(files).build(), count

    def test_inline_async_handler_retains_its_imported_service_call(self):
        sources = {
            "app.ts": '''import express from "express";
import { loadGreeting } from "./service";
const app = express();
app.get("/greetings/:name", async (req, res) => {
  const greeting = await loadGreeting(req.params.name);
  return res.json(greeting);
});
''',
            "service.ts": "export function loadGreeting(name) { return name; }",
        }
        files, graph, count = self.graph(sources)
        route = next(s for s in files["app.ts"].symbols if s.sub == "route handler")
        self.assertEqual(count, 1)
        self.assertEqual(route.routes, ["GET /greetings/{}"])
        self.assertEqual((route.line, route.end), (4, 7))
        self.assertIn((route.id, "f:service.ts#loadGreeting"), graph.edges)

    def test_inline_function_inside_setup_gets_its_own_service_link(self):
        files, graph, count = self.graph({
            "app.js": '''import { storeLetter } from "./service.js";
export function setup(app) {
  app.post("/letters", function (req, res) {
    return res.send(storeLetter(req.body));
  });
}
''',
            "service.js": "export function storeLetter(value) { return value; }",
        })
        route = next(s for s in files["app.js"].symbols if s.sub == "route handler")
        self.assertEqual(count, 1)
        self.assertIn((route.id, "f:service.js#storeLetter"), graph.edges)
        self.assertFalse(next(s for s in files["app.js"].symbols if s.name == "setup").routes)

    def test_imported_named_callback_links_real_handler_and_service(self):
        files, graph, count = self.graph({
            "app.js": '''import { showPage as servePage } from "./controller.js";
app.get("/pages/:id", servePage);
''',
            "controller.js": '''import { readPage } from "./service.js";
export function showPage(req, res) { return res.json(readPage(req.params.id)); }
''',
            "service.js": "export function readPage(id) { return id; }",
            "unrelated.js": "export function servePage() { return 0; }",
        })
        route = next(s for s in files["app.js"].symbols if s.sub == "route handler")
        self.assertEqual(count, 1)
        self.assertIn((route.id, "f:controller.js#showPage"), graph.edges)
        self.assertIn(("f:controller.js#showPage", "f:service.js#readPage"), graph.edges)
        self.assertNotIn((route.id, "f:unrelated.js#servePage"), graph.edges)

    def test_local_named_callback_keeps_route_on_the_actual_function(self):
        files, _, count = self.graph({"app.js": '''function show(req, res) { return res.send("ok"); }
app.get("/ready", show);
'''})
        self.assertEqual(count, 1)
        self.assertEqual(files["app.js"].symbols[0].routes, ["GET /ready"])
        self.assertFalse(any(s.sub == "route handler" for s in files["app.js"].symbols))

    def test_imported_router_has_explicit_mount_prefix_and_client_link(self):
        files, graph, count = self.graph({
            "app.ts": '''import express from "express";
import reports from "./reports.js";
const app = express();
app.use("/api/v3", reports);
''',
            "reports.ts": '''import { Router } from "express";
import { listReports } from "./controller";
const reports = Router();
reports.get("/reports", listReports);
export default reports;
''',
            "controller.ts": "export function listReports(req, res) { return res.json([]); }",
            "client.ts": 'export function load() { return fetch("/api/v3/reports"); }',
        })
        route = next(s for s in files["reports.ts"].symbols if s.sub == "route handler")
        self.assertEqual(count, 1)
        self.assertEqual(route.routes, ["GET /api/v3/reports"])
        self.assertIn((route.id, "f:controller.ts#listReports"), graph.edges)
        self.assertIn(("f:client.ts#load", route.id), graph.edges)

    def test_local_nested_mounts_compose_once(self):
        files, _, count = self.graph({"app.js": '''import express from "express";
const app = express();
const outer = express.Router();
const inner = express.Router();
app.use("/api", outer);
outer.use("/v2", inner);
inner.get("/items", (req, res) => res.send("ok"));
'''})
        self.assertEqual(count, 1)
        self.assertEqual([route for s in files["app.js"].symbols for route in s.routes], ["GET /api/v2/items"])

    def test_multiple_or_dynamic_mounts_do_not_choose_a_prefix(self):
        files, _, count = self.graph({"app.js": '''import express from "express";
const app = express();
const pages = express.Router();
app.use("/first", pages);
app.use("/second", pages);
pages.get("/pages", (req, res) => res.send("ok"));
const files = express.Router();
app.use(dynamicPrefix, files);
files.get("/files", (req, res) => res.send("ok"));
'''})
        self.assertEqual(count, 2)
        self.assertEqual([route for s in files["app.js"].symbols for route in s.routes], ["GET /pages", "GET /files"])

    def test_mixed_dynamic_mounts_and_mount_cycles_remain_unprefixed(self):
        files, _, count = self.graph({"app.js": '''import express from "express";
const app = express();
const pages = express.Router();
app.use("/known", pages);
app.use(dynamicPrefix, pages);
pages.get("/pages", (req, res) => res.send("ok"));
const first = express.Router();
const second = express.Router();
first.use("/a", second);
second.use("/b", first);
first.get("/cycle", (req, res) => res.send("ok"));
'''})
        self.assertEqual(count, 2)
        self.assertEqual([route for s in files["app.js"].symbols for route in s.routes], ["GET /pages", "GET /cycle"])

    def test_comment_and_string_mounts_and_client_options_are_ignored(self):
        files, graph, count = self.graph({"app.js": '''import express from "express";
const app = express();
const pages = express.Router();
// app.use("/comment", pages);
const text = 'app.use("/string", pages)';
const example = `pages.get("/ghost", handler)`;
pages.get("/pages", (req, res) => {
  // imaginaryService();
  const example = "fakeService()";
  return res.send(example);
});
export function call(api) { return api.get("/remote", { headers: {} }); }
'''})
        self.assertEqual(count, 1)
        self.assertEqual([route for s in files["app.js"].symbols for route in s.routes], ["GET /pages"])
        route = next(s for s in files["app.js"].symbols if s.sub == "route handler")
        chains = [chain for chain, _ in route.calls]
        self.assertNotIn(("imaginaryService",), chains)
        self.assertNotIn(("fakeService",), chains)


if __name__ == "__main__":
    unittest.main()
