"""Layering: slice the code horizontally into human-sized views.

    root (system)  ->  groups (folders / clusters)  ->  units (classes, modules)  ->  functions

Rules:
- A view shows at most MAX_VIEW boxes (+ up to MAX_GHOSTS dimmed neighbours).
- Folders carry human intent, so groups follow the directory tree first; when a
  folder is too flat, files are clustered by who-calls-whom (Louvain).
- Inside a view, boxes are ordered as a workflow: callers before callees.
- In diff mode only changed symbols and their closest neighbours are mapped.
"""
from __future__ import annotations

from collections import defaultdict

from . import MAX_GHOSTS, MAX_VIEW
from .algo import louvain, merge_to_cap, pagerank, workflow_order
from .diffmap import CHANGED
from .formats import DOMAIN_NAMES, DOMAIN_ORDER
from .util import sha1

NEIGHBOURS_PER_CHANGE = 4
MAX_CONTEXT = 40
MAX_DEPTH = 8
FORMAT_LABEL = {
    "dockerfile": "Dockerfile", "compose": "Compose file", "kubernetes": "Kubernetes manifest", "terraform": "Terraform",
    "bicep": "Bicep", "cloudformation": "CloudFormation", "github-actions": "GitHub Actions workflow",
    "gitlab-ci": "GitLab CI", "azure-pipelines": "Azure Pipelines", "circleci": "CircleCI", "jenkins": "Jenkinsfile",
    "make": "Makefile", "openapi": "OpenAPI contract", "graphql": "GraphQL schema", "proto": "Protobuf",
    "sql": "SQL", "prisma": "Prisma schema", "notebook": "Jupyter notebook", "markdown": "document",
    "env": "environment file", "yaml": "YAML settings", "json": "JSON settings", "toml": "TOML settings",
    "ini": "settings", "text": "text file", "helm-values": "Helm values",
    "deps-npm": "npm dependencies", "deps-pip": "Python dependencies", "deps-pyproject": "Python project",
    "deps-pipfile": "Python dependencies", "deps-gomod": "Go modules", "deps-cargo": "Rust crates",
    "deps-maven": "Maven dependencies", "deps-gradle": "Gradle build", "deps-gem": "Ruby gems",
    "deps-composer": "PHP packages", "deps-nuget": "NuGet packages",
}


def _edge_kind(kinds: set) -> str:
    if kinds == {"inherits"}:
        return "inherits"
    if kinds == {"refs"}:
        return "refs"
    return "calls"


def _combine(statuses: list, mode: str) -> str:
    changed = [s for s in statuses if s in CHANGED]
    if not changed:
        return "context" if mode == "diff" else "same"
    if all(s == "added" for s in statuses):
        return "added"
    if all(s == "deleted" for s in statuses):
        return "deleted"
    return "modified"


class MapBuilder:
    def __init__(self, graph, files: dict, mode: str, repo_name: str):
        self.g = graph
        self.files = files
        self.mode = mode
        self.repo_name = repo_name
        self.nodes: dict = {}
        self.views: list = []
        self.units_by_file: dict = {}

    # ----------------------------------------------------------- public
    def build(self) -> tuple:
        relevant = self._relevant() if self.mode == "diff" else None
        self._units(relevant)
        mapped = sorted(self.units_by_file)
        self.nodes["root"] = {"id": "root", "kind": "root", "name": self.repo_name, "parent": None, "children": []}
        self._fill_group("root", mapped, "", 0)
        self._flatten("root")
        self._aggregate("root")
        self._views()
        self._disambiguate()
        return self.nodes, self.views

    def _disambiguate(self) -> None:
        """Two boxes in one view must not look identical (e.g. three `shallow.ts` from different folders)."""
        for v in self.views:
            ids = v["children"] + v["ghosts"]
            names: dict = defaultdict(list)
            for i in ids:
                names[self.nodes[i]["name"]].append(i)
            for same in names.values():
                if len(same) < 2:
                    continue
                for i in same:
                    n = self.nodes[i]
                    path = n.get("file") or n.get("path") or ""
                    folder = path.rsplit("/", 2)[-2] if path.count("/") >= 1 else ""
                    if n["kind"] == "module" and folder:
                        n["name"] = f"{folder}/{n['name']}"
                    elif n["kind"] in ("class", "function", "method") and path:
                        n["name"] = f"{n['name']} · {path.rsplit('/', 1)[-1]}"

    # ----------------------------------------------------------- relevance (diff mode)
    def _relevant(self) -> set:
        g = self.g
        seeds = {sid for sid, s in g.syms.items() if s.status in CHANGED or s.provenance}
        for a, b in getattr(g, "review_edge_provenance", {}):
            seeds.update((a, b))
        und = []
        for (a, b), (_kind, w, conf) in g.edges.items():
            if conf >= 0.5:
                und.append((a, b, w))
                und.append((b, a, w))
        ppr = pagerank(list(g.syms), und, personalization={s: 1.0 for s in seeds}) if seeds else {}
        context: list = []
        for sid in sorted(seeds):
            near = [n for n in (g.callers.get(sid, set()) | g.callees.get(sid, set()))
                    if n not in seeds and g.edges.get((sid, n), g.edges.get((n, sid), [0, 0, 0]))[2] >= 0.5]
            near.sort(key=lambda n: (-ppr.get(n, 0), n))       # id breaks ties: sets iterate in random order
            context.extend(near[:NEIGHBOURS_PER_CHANGE])
        ranked = sorted(dict.fromkeys(context), key=lambda n: (-ppr.get(n, 0), n))[:MAX_CONTEXT]
        return seeds | set(ranked)

    # ----------------------------------------------------------- units
    def _structural_hash(self, s) -> str:
        links = sorted((other, self.g.edges[(s.id, other)]) for other in self.g.callees.get(s.id, ())
                       if (s.id, other) in self.g.edges)
        callers = sorted((other, self.g.edges[(other, s.id)]) for other in self.g.callers.get(s.id, ())
                         if (other, s.id) in self.g.edges)
        return sha1(repr((s.hash, s.provenance, links, callers)))

    def _sym_node(self, s, kind: str, parent: str) -> dict:
        g = self.g
        fi = self.files.get(s.file)
        domain = fi.domain if fi is not None else "code"
        status = s.status if s.status in CHANGED else ("context" if self.mode == "diff" else "same")
        callable_ = kind in ("function", "method") and domain == "code" and s.sub not in ("route handler", "script")
        node = {
            "id": s.id, "kind": kind, "name": s.name + ("()" if callable_ else ""),
            "qual": s.qual, "parent": parent, "children": [], "file": s.file, "line": s.line, "end": s.end,
            "sig": s.signature, "doc": s.doc, "status": status, "add": s.add, "del": s.dele,
            "rank": g.rank.get(s.id, 0.0), "entry": g.entry.get(s.id, 0), "hash": self._structural_hash(s),
            "sub": s.sub, "domain": domain, "lang": fi.lang if fi is not None else "",
        }
        if s.lexical_parent:
            node["lexical_parent"] = s.lexical_parent
        if s.provenance:
            node["provenance"] = s.provenance
        reviewed_links = [{"to": other, **getattr(g, "review_edge_provenance", {})[(s.id, other)]}
                          for other in sorted(g.callees.get(s.id, ()))
                          if (s.id, other) in getattr(g, "review_edge_provenance", {})]
        if reviewed_links:
            node["review_links"] = reviewed_links
        if s.diff:
            node["diff"] = s.diff
        if s.routes:
            node["routes"] = list(s.routes)
        if s.flags:
            node["flags"] = [f["id"] for f in s.flags]
        if kind != "class":
            def top(ids):
                ids = sorted(ids, key=lambda x: (-g.rank.get(x, 0), x))[:8]
                return [[x, g.syms[x].qual] for x in ids if x in g.syms]
            node["calls"] = top(g.callees.get(s.id, ()))
            node["callers"] = top(g.callers.get(s.id, ()))
        return node

    def _units(self, relevant) -> None:
        for path, fi in sorted(self.files.items()):
            units = []
            methods = defaultdict(list)
            for s in fi.symbols:
                if s.kind == "method":
                    methods[s.parent].append(s)
            classes = [s for s in fi.symbols if s.kind == "class"]
            funcs = [s for s in fi.symbols if s.kind == "function"]
            for c in classes:
                members = methods[c.id]
                shown = members if relevant is None else [m for m in members if m.id in relevant]
                if relevant is not None and c.id not in relevant and not shown:
                    continue
                node = self._sym_node(c, "class", "")
                node["own_status"] = node["status"]
                node["children"] = [m.id for m in shown]
                paren = "()" if fi.domain == "code" else ""
                node["hidden"] = [[m.id, m.name + paren] for m in members if m not in shown]
                node["rank"] += sum(self.g.rank.get(m.id, 0.0) for m in members)
                node["methods"] = len(members)
                node["bases"] = list(c.bases)
                # A class explanation can describe method behavior, including methods
                # hidden in a focused diff. Their bodies must invalidate that label.
                node["hash"] = sha1(repr((node["hash"], sorted((m.id, self._structural_hash(m)) for m in members))))
                self.nodes[c.id] = node
                for m in shown:
                    self.nodes[m.id] = self._sym_node(m, "method", c.id)
                units.append(c.id)
            shown_f = funcs if relevant is None else [f for f in funcs if f.id in relevant]
            module_changed = bool(fi.module_add or fi.module_del) or fi.status in ("added", "deleted")
            # Imports/constants have their own changes even when this file also
            # contributes a class. Keep a module box for those lines alongside it.
            want_module = bool(shown_f) or (relevant is not None and
                (fi.module_add or fi.module_del or (module_changed and not units))) \
                or (relevant is None and not classes and not funcs)
            if want_module:
                mid = f"m:{path}"
                own = fi.status if fi.status in ("added", "deleted") else (
                    "modified" if (fi.module_add or fi.module_del) else ("context" if self.mode == "diff" else "same"))
                paren = "()" if fi.domain == "code" else ""
                node = {
                    "id": mid, "kind": "module", "name": path.rsplit("/", 1)[-1], "qual": path, "parent": "",
                    "children": [f.id for f in shown_f], "file": path, "line": 1, "end": 0, "sig": "",
                    "doc": fi.doc, "status": own, "own_status": own, "add": fi.module_add, "del": fi.module_del,
                    "rank": sum(self.g.rank.get(f.id, 0.0) for f in funcs), "entry": 0,
                    "hidden": [[f.id, f.name + paren] for f in funcs if f not in shown_f],
                    "functions": len(funcs), "hash": sha1(repr((fi.source_hash, fi.doc,
                        sorted((s.id, self._structural_hash(s)) for s in fi.symbols)))),
                    "domain": fi.domain, "lang": fi.lang,
                    "sub": FORMAT_LABEL.get(fi.lang, fi.lang if fi.domain != "code" else "module"),
                    "opaque": not fi.symbols,          # nothing parsed: the agent describes it from the diff
                }
                if fi.module_diff:
                    node["diff"] = fi.module_diff
                if not fi.symbols and fi.status in ("added", "deleted"):
                    node["add"], node["del"] = fi.add, fi.dele
                if fi.flags:
                    node["flags"] = [f["id"] for f in fi.flags]
                self.nodes[mid] = node
                for f in shown_f:
                    self.nodes[f.id] = self._sym_node(f, "function", mid)
                units.append(mid)
            if units:
                self.units_by_file[path] = units

    # ----------------------------------------------------------- grouping
    def _file_weight(self) -> dict:
        if not hasattr(self, "_fw"):
            self._fw = dict(self.g.file_edges)
        return self._fw

    def _partition(self, files: list, prefix: str) -> tuple:
        """Split files into at most MAX_VIEW parts: by folder first, then by call affinity."""
        while True:
            firsts = {f[len(prefix):].split("/", 1)[0] + ("/" if "/" in f[len(prefix):] else "") for f in files}
            if len(firsts) == 1 and next(iter(firsts)).endswith("/"):
                prefix += next(iter(firsts))
                continue
            break
        items: dict = defaultdict(list)
        for f in files:
            rest = f[len(prefix):]
            items[rest.split("/", 1)[0] + ("/" if "/" in rest else "")].append(f)
        keys = sorted(items, key=lambda k: (not k.endswith("/"), k))
        if len(keys) <= MAX_VIEW:
            return prefix, [[k] for k in keys], items
        key_of = {f: k for k, fs in items.items() for f in fs}
        weights: dict = defaultdict(float)
        for (a, b), w in self._file_weight().items():
            ka, kb = key_of.get(a), key_of.get(b)
            if ka and kb and ka != kb:
                weights[tuple(sorted((ka, kb)))] += w
        # Dense folders collapse into a few huge communities at the default resolution;
        # raise it until the folder splits into enough parts to be worth a view.
        want = min(6, max(2, len(keys) // 4))
        for resolution in (1.0, 1.5, 2.0, 3.0, 5.0):
            comms = louvain(keys, weights, resolution=resolution)
            if len(comms) >= want:
                break

        def size(c):
            return sum(len(self.units_by_file[f]) for k in c for f in items[k])

        def weight(c1, c2):
            s1, s2 = set(c1), set(c2)
            return sum(w for (a, b), w in weights.items() if (a in s1 and b in s2) or (a in s2 and b in s1))
        comms = merge_to_cap(comms, weight, size, MAX_VIEW)
        comms = [sorted(c, key=lambda k: (not k.endswith("/"), k)) for c in comms]
        comms.sort(key=lambda c: c[0])
        return prefix, comms, items

    def _item_rank(self, files: list) -> float:
        return sum(self.nodes[u]["rank"] for f in files for u in self.units_by_file[f])

    def _fill_group(self, gid: str, files: list, prefix: str, depth: int) -> None:
        units = [u for f in files for u in self.units_by_file[f]]
        node = self.nodes[gid]
        node["files"] = len(files)
        domains = sorted({self.files[f].domain for f in files}, key=lambda d: DOMAIN_ORDER.index(d) if d in DOMAIN_ORDER else 99)
        if depth == 0 and len(domains) > 1 and len(units) > 2:
            # "one for all": the top level says *what kind* of thing changed (code, infra, CI, API, ...)
            children = []
            for domain in domains:
                dfiles = [f for f in files if self.files[f].domain == domain]
                dunits = [u for f in dfiles for u in self.units_by_file[f]]
                if len(dunits) == 1:
                    children.append(dunits[0])
                    self.nodes[dunits[0]]["parent"] = gid
                    continue
                cid = f"g:@{domain}"
                self.nodes[cid] = {"id": cid, "kind": "group", "name": DOMAIN_NAMES.get(domain, domain), "path": "",
                                   "parent": gid, "children": [], "domain": domain,
                                   "sub": f"{len(dfiles)} file{'s' if len(dfiles) != 1 else ''}"}
                children.append(cid)
                self._fill_group(cid, dfiles, prefix, depth + 1)
            node["children"] = children
            return
        split = len(units) > MAX_VIEW or (depth == 0 and len(units) > 4)
        if not split or depth >= MAX_DEPTH:
            self._adopt(gid, units)
            return
        prefix2, comms, items = self._partition(files, prefix)
        if len(comms) <= 1 or (depth == 0 and len(units) <= MAX_VIEW and len(comms) >= len(units)):
            self._adopt(gid, units)
            return
        children = []
        for comm in comms:
            cfiles = [f for k in comm for f in items[k]]
            cunits = [u for f in cfiles for u in self.units_by_file[f]]
            if len(cunits) == 1:
                children.append(cunits[0])
                self.nodes[cunits[0]]["parent"] = gid
                continue
            if len(comm) == 1:
                key = comm[0]
                cid, name = "g:" + prefix2 + key, key
                cprefix = prefix2 + key if key.endswith("/") else prefix2
            else:
                lead = max(comm, key=lambda k: self._item_rank(items[k]))
                cid = f"g:{prefix2}{lead}+{len(comm) - 1}"
                name = f"{lead} +{len(comm) - 1}"
                cprefix = prefix2
            while cid in self.nodes:
                cid += "'"
            group_domains = {self.files[f].domain for f in cfiles}
            self.nodes[cid] = {"id": cid, "kind": "group", "name": name, "path": prefix2 + (comm[0] if len(comm) == 1 else ""),
                               "parent": gid, "children": [],
                               "domain": next(iter(group_domains)) if len(group_domains) == 1 else ""}
            children.append(cid)
            self._fill_group(cid, cfiles, cprefix, depth + 1)
        node["children"] = children

    def _adopt(self, gid: str, units: list) -> None:
        self.nodes[gid]["children"] = list(units)
        for u in units:
            self.nodes[u]["parent"] = gid

    def _flatten(self, nid: str) -> None:
        """Skip levels that would show a single box."""
        node = self.nodes[nid]
        while len(node["children"]) == 1 and self.nodes[node["children"][0]]["kind"] == "group":
            only = self.nodes.pop(node["children"][0])
            node["children"] = only["children"]
            for c in node["children"]:
                self.nodes[c]["parent"] = nid
            if nid != "root":
                node["name"], node["path"] = only["name"], only.get("path", "")
        for c in node["children"]:
            if self.nodes[c]["kind"] == "group":
                self._flatten(c)

    def _aggregate(self, nid: str) -> None:
        node = self.nodes[nid]
        for c in node["children"]:
            self._aggregate(c)
        kids = [self.nodes[c] for c in node["children"]]
        kind = node["kind"]
        own_flags = len(node.get("flags", []))
        node["risk"] = own_flags + sum(k.get("risk", 0) for k in kids)
        if kind in ("function", "method"):
            node["changed"] = 1 if node["status"] in CHANGED else 0
            return
        node["changed"] = sum(k["changed"] for k in kids)
        if kind in ("group", "root"):
            # Content hashes flow upwards before visual folding, so a body edit
            # invalidates every containing explanation without depending on labels.
            node["hash"] = sha1(repr((node["id"], sorted((k["id"], k["hash"]) for k in kids))))
            node["add"] = sum(k["add"] for k in kids)
            node["del"] = sum(k["del"] for k in kids)
            node["rank"] = sum(k["rank"] for k in kids)
            node["entry"] = max([k["entry"] for k in kids] or [0])
            node["status"] = _combine([k["status"] for k in kids], self.mode)
            return
        own = node.get("own_status", node["status"])
        statuses = [own] + [k["status"] for k in kids]
        if own in CHANGED:
            node["changed"] += 1
        node["add"] += sum(k["add"] for k in kids)
        node["del"] += sum(k["del"] for k in kids)
        node["entry"] = max([node["entry"]] + [k["entry"] for k in kids])
        node["status"] = _combine(statuses, self.mode)

    # ----------------------------------------------------------- views
    def _chain(self, x: str) -> list:
        chain = []
        while x:
            chain.append(x)
            x = self.nodes[x]["parent"] if x in self.nodes else None
        return chain

    def _views(self) -> None:
        nodes = self.nodes
        order_ids = []
        queue = ["root"]
        while queue:
            nid = queue.pop(0)
            n = nodes[nid]
            if n["children"] or n.get("hidden"):
                order_ids.append(nid)
            queue.extend(n["children"])
        view_of = {nid: f"v{i}" for i, nid in enumerate(order_ids)}
        visible: dict = {}
        folded_into: dict = {}
        for nid in order_ids:
            kids = nodes[nid]["children"]
            hidden = nodes[nid].get("hidden", [])
            slots = MAX_VIEW - (1 if hidden else 0)
            if len(kids) > slots:
                prio = sorted(kids, key=lambda c: (-nodes[c]["changed"], -nodes[c].get("risk", 0), -nodes[c]["entry"],
                                                   -nodes[c]["rank"], c))
                keep = set(prio[: MAX_VIEW - 1])
                shown = [c for c in kids if c in keep]
                folded = [c for c in kids if c not in keep]
            else:
                shown, folded = list(kids), []
            if folded or hidden:
                more_id = nid + "~more"
                count = len(folded) + len(hidden)
                nodes[more_id] = {
                    "id": more_id, "kind": "more", "parent": nid, "children": [],
                    "name": f"+{count} more" if folded else f"+{count} unchanged",
                    "members": folded, "hidden": hidden, "status": "context" if self.mode == "diff" else "same",
                    "add": 0, "del": 0, "rank": 0.0, "entry": 0, "changed": sum(nodes[c]["changed"] for c in folded),
                    "risk": sum(nodes[c].get("risk", 0) for c in folded),
                }
                shown.append(more_id)
                for c in folded:
                    folded_into[c] = more_id
            visible[nid] = shown
        for nid in order_ids:
            nodes[nid]["view"] = view_of[nid]

        def at(view_node: str, chain: list):
            """The box in `view_node`'s view that represents the first element of `chain`."""
            idx = chain.index(view_node)
            child = chain[idx - 1]
            return folded_into.get(child, child)

        internal: dict = defaultdict(lambda: defaultdict(lambda: [0, 0.0, set()]))
        ghosts: dict = defaultdict(lambda: defaultdict(lambda: [0, 0.0, set()]))
        internal_review, ghost_review = defaultdict(lambda: defaultdict(int)), defaultdict(lambda: defaultdict(int))
        for (a, b), (kind, w, conf) in self.g.edges.items():
            if a not in nodes or b not in nodes:
                continue
            ca, cb = self._chain(a), self._chain(b)
            set_b = set(cb)
            lca = next((x for x in ca if x in set_b), None)
            if lca is None or lca == a or lca == b:
                continue
            ia, ib = ca.index(lca), cb.index(lca)
            child_a, child_b = ca[ia - 1], cb[ib - 1]
            reviewed = (a, b) in getattr(self.g, "review_edge_provenance", {})
            if lca in view_of:
                e = internal[lca][(at(lca, ca), at(lca, cb))]
                e[0] += w
                e[1] = max(e[1], conf)
                e[2].add(kind)
                if reviewed:
                    internal_review[lca][(at(lca, ca), at(lca, cb))] += w
            # outgoing ghost edges for views below the LCA on a's side, incoming on b's side
            for view_node in ca[1:ia]:
                if view_node in view_of:
                    e = ghosts[view_node][(at(view_node, ca), child_b, "out")]
                    e[0] += w
                    e[1] = max(e[1], conf)
                    e[2].add(kind)
                    if reviewed:
                        ghost_review[view_node][(at(view_node, ca), child_b)] += w
            for view_node in cb[1:ib]:
                if view_node in view_of:
                    e = ghosts[view_node][(child_a, at(view_node, cb), "in")]
                    e[0] += w
                    e[1] = max(e[1], conf)
                    e[2].add(kind)
                    if reviewed:
                        ghost_review[view_node][(child_a, at(view_node, cb))] += w

        for nid in order_ids:
            shown = visible[nid]
            edges = []
            for (x, y), (w, conf, kinds) in internal[nid].items():
                if x != y and x in shown and y in shown:
                    edges.append([x, y, w, round(conf, 2), _edge_kind(kinds)])
            weight_by_ghost: dict = defaultdict(int)
            for (x, y, direction), (w, _c, _k) in ghosts[nid].items():
                weight_by_ghost[y if direction == "out" else x] += w
            chosen = sorted(weight_by_ghost, key=lambda gh: (-nodes[gh]["changed"], -weight_by_ghost[gh], gh))[:MAX_GHOSTS]
            for (x, y, direction), (w, conf, kinds) in ghosts[nid].items():
                ghost = y if direction == "out" else x
                if ghost in chosen and (x in shown or y in shown):
                    edges.append([x, y, w, round(conf, 2), _edge_kind(kinds)])
            node_index = {c: i for i, c in enumerate(shown)}
            unit_view = nodes[nid]["kind"] in ("class", "module")
            domain_split = nid == "root" and any(c.startswith("g:@") for c in shown)

            def priority(c):
                n = nodes[c]
                if domain_split:
                    d = n.get("domain") or ""
                    return (1 if n["kind"] == "more" else 0, DOMAIN_ORDER.index(d) if d in DOMAIN_ORDER else 99, node_index[c])
                if unit_view:   # functions: constructor first, then the author's order
                    ctor = n["name"] in ("__init__()", "constructor()")
                    return (1 if n["kind"] == "more" else 0, 0 if ctor else 1, -n["entry"], n.get("line", 0), node_index[c])
                return (1 if n["kind"] == "more" else 0, -n["entry"], -n["rank"], node_index[c])
            order = sorted(shown, key=priority) if domain_split else workflow_order(
                shown, [(e[0], e[1]) for e in edges if e[0] in node_index and e[1] in node_index], priority)
            self.views.append({
                "id": view_of[nid], "node": nid, "level": len(self._chain(nid)) - 1,
                "children": shown, "ghosts": chosen, "edges": edges, "order": order,
                "review_edges": [[a, b, internal_review[nid].get((a, b), 0) + ghost_review[nid].get((a, b), 0)]
                                 for a, b, *_ in edges
                                 if internal_review[nid].get((a, b), 0) + ghost_review[nid].get((a, b), 0)],
            })
