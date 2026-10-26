"""Graph algorithms in pure Python: PageRank, Louvain communities, SCCs, workflow order."""
from __future__ import annotations

import heapq
from collections import defaultdict


def pagerank(nodes: list, edges: list, personalization: dict | None = None,
             damping: float = 0.85, iterations: int = 60, tol: float = 1e-9) -> dict:
    """Weighted PageRank. `edges` is [(src, dst, weight)]. Importance flows src -> dst."""
    if not nodes:
        return {}
    out_w: dict = defaultdict(float)
    for u, _, w in edges:
        out_w[u] += w
    n = len(nodes)
    if personalization:
        total = sum(personalization.get(x, 0.0) for x in nodes) or 1.0
        p = {x: personalization.get(x, 0.0) / total for x in nodes}
    else:
        p = {x: 1.0 / n for x in nodes}
    pr = dict(p)
    for _ in range(iterations):
        dangling = sum(pr[x] for x in nodes if out_w[x] == 0)
        new = {x: (1 - damping) * p[x] + damping * dangling * p[x] for x in nodes}
        for u, v, w in edges:
            if u in pr and v in new:
                new[v] += damping * pr[u] * w / out_w[u]
        delta = sum(abs(new[x] - pr[x]) for x in nodes)
        pr = new
        if delta < tol:
            break
    return pr


def louvain(nodes: list, weights: dict, resolution: float = 1.0, max_levels: int = 8) -> list:
    """Louvain modularity communities on an undirected weighted graph.

    `weights` maps (a, b) -> w. Deterministic: nodes are visited in the given order.
    Returns a list of communities (lists of original nodes).
    """
    adj: dict = defaultdict(lambda: defaultdict(float))
    for (a, b), w in weights.items():
        if a != b and w > 0:
            adj[a][b] += w
            adj[b][a] += w
    level_nodes = list(nodes)
    members = {x: [x] for x in nodes}
    for _ in range(max_levels):
        m2 = sum(sum(nb.values()) for nb in adj.values())
        if m2 == 0:
            break
        deg = {x: sum(adj[x].values()) for x in level_nodes}
        com = {x: x for x in level_nodes}
        tot = dict(deg)
        moved_any, improved = False, True
        while improved:
            improved = False
            for x in level_nodes:
                old = com[x]
                links: dict = defaultdict(float)
                for y, w in adj[x].items():
                    if y != x:
                        links[com[y]] += w
                tot[old] -= deg[x]
                best = old
                best_gain = links.get(old, 0.0) - resolution * tot[old] * deg[x] / m2
                for c in sorted(links, key=str):
                    gain = links[c] - resolution * tot[c] * deg[x] / m2
                    if gain > best_gain + 1e-12:
                        best, best_gain = c, gain
                tot[best] += deg[x]
                if best != old:
                    com[x] = best
                    improved = moved_any = True
        if not moved_any:
            break
        new_members: dict = defaultdict(list)
        for x in level_nodes:
            new_members[com[x]].extend(members[x])
        new_adj: dict = defaultdict(lambda: defaultdict(float))
        for a in level_nodes:
            for b, w in adj[a].items():
                new_adj[com[a]][com[b]] += w
        level_nodes = [c for c in dict.fromkeys(com[x] for x in level_nodes)]
        members = dict(new_members)
        adj = new_adj
    return [members[x] for x in level_nodes]


def merge_to_cap(groups: list, weight, size, cap: int) -> list:
    """Merge the smallest group into its best-connected neighbour until at most `cap` remain."""
    groups = [list(g) for g in groups]
    while len(groups) > cap:
        groups.sort(key=lambda g: (size(g), str(g[0])))
        small = groups.pop(0)
        best, best_w = None, 0.0
        for g in groups:
            w = weight(small, g)
            if w > best_w:
                best, best_w = g, w
        if best is None:          # unconnected: merge with the next smallest
            best = groups[0]
        best.extend(small)
    return groups


def sccs(nodes: list, succ: dict) -> list:
    """Tarjan's strongly connected components (iterative)."""
    index: dict = {}
    low: dict = {}
    on_stack: set = set()
    stack: list = []
    out: list = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(succ.get(root, ())))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(succ.get(w, ()))))
                    advanced = True
                    break
                if w in on_stack:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                out.append(comp)
    return out


def workflow_order(nodes: list, edges: list, priority) -> list:
    """Order nodes so callers come before callees (cycles kept together).

    `edges` is [(a, b)] meaning a uses b. `priority(node)` returns a sort key;
    smaller keys go first among nodes that are ready.
    """
    node_set = set(nodes)
    succ: dict = defaultdict(list)
    for a, b in edges:
        if a in node_set and b in node_set and a != b:
            succ[a].append(b)
    comps = sccs(nodes, succ)
    comp_of = {x: i for i, comp in enumerate(comps) for x in comp}
    indeg: dict = defaultdict(int)
    csucc: dict = defaultdict(set)
    for a in nodes:
        for b in succ[a]:
            ca, cb = comp_of[a], comp_of[b]
            if ca != cb and cb not in csucc[ca]:
                csucc[ca].add(cb)
                indeg[cb] += 1
    def comp_key(i):
        return min(priority(x) for x in comps[i])
    ready = [(comp_key(i), i) for i in range(len(comps)) if indeg[i] == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        _, i = heapq.heappop(ready)
        order.extend(sorted(comps[i], key=priority))
        for j in csucc[i]:
            indeg[j] -= 1
            if indeg[j] == 0:
                heapq.heappush(ready, (comp_key(j), j))
    return order
