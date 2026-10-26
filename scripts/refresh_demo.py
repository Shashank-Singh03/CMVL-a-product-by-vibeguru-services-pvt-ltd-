"""Rebuild the public example shell, preserving its captured data and audio.

Run after editing the map template, shared studio tokens, or dashboard/example.
This only writes the curated example; it does not execute the Notes application.
"""
import html
import json
import re
from pathlib import Path
from urllib.parse import urlencode
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "examples/demo-output"
ASSETS = ROOT / "skills/codemap/assets"
SOURCE = ROOT / "dashboard/example"
sys.path.insert(0, str(ROOT / "skills/codemap/scripts"))
from codemap_lib import VERSION


def refresh():
    original = (OUT / "codemap.html").read_text(encoding="utf-8")
    match = re.search(r'<script id="codemap-data" type="application/json">(.*?)</script>', original, re.S)
    data = json.loads(match[1])
    data.setdefault("artifacts", {})["dashboard"] = "index.html"
    shared = (ASSETS / "studio.css").read_text(encoding="utf-8")
    template = (ASSETS / "viewer.html").read_text(encoding="utf-8").replace("/*__CODEMAP_STUDIO__*/", shared)
    template = template.replace("__CODEMAP_APP_VERSION__", VERSION)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\!--")
    (OUT / "codemap.html").write_text(template.replace("__CODEMAP_TITLE__", html.escape("codemap · " + data["repo"])).replace('"__CODEMAP_DATA__"', payload), encoding="utf-8")
    hints = [("</>", "Python, JavaScript and Java"), ("{ }", "Endpoints, requests and schemas"), ("⌘", "Containers, deployment and storage"), ("↗", "GitHub Actions and nightly backups"), ("[ ]", "Dependencies in requirements.txt"), ("≡", "Documentation and usage"), ("↶", "Recovery steps in docs/runbook.txt")]
    cards = []
    for index, nid in enumerate(data["views"][0]["children"]):
        node = data["nodes"][nid]
        icon, hint = hints[index] if index < len(hints) else ("{ }", "Explore the recorded source")
        target = "codemap.html#" + urlencode({"v": node["view"]} if node.get("view") else {"v": data["views"][0]["id"], "n": nid})
        tone = ["sky", "violet", "sky", "violet", "sky", "violet", "violet"][index % 7]
        cards.append(f'<a class="area" data-tone="{tone}" href="{html.escape(target)}"><span class="area-icon" aria-hidden="true">{html.escape(icon)}</span><div class="area-body"><h3>{html.escape(node.get("title") or node["name"])}</h3><p>{html.escape(hint)}</p></div><span class="area-arrow" aria-hidden="true">↗</span></a>')
    stats, scope = data["stats"], data["scope"]
    metrics = [
        (scope["files"], "Changed files", "mint", "Start with what changed.", "Follow the files touched by this capture, then open an area below to see the source in context.", "#map", "Explore the changed files"),
        (stats["symbols"], "Mapped symbols", "sky", "Find the pieces behind the feature.", "Functions, classes and modules give the change structure. The map includes surrounding context as well as changed code.", "#map", "Explore the code structure"),
        (stats["edges"], "Connections", "violet", "See how the pieces relate.", "Trace recorded calls and dependencies. Inferred relationships are labeled in the map; a connection is not proof of execution.", "#map", "Follow the connections"),
        (len(stats["languages"]), "Languages & formats", "sky", "Read across the whole stack.", "Application code, contracts, infrastructure and automation all contribute to this change. The reading guide connects them.", "#guide", "Start the reading guide"),
    ]
    risk_items = []
    for risk in data.get("risks", []):
        node = next((n for n in data["nodes"].values() if n.get("file") == risk.get("file") and n.get("kind") == "module"), None)
        target = "codemap.html#" + urlencode({"n": node["id"]}) if node else "codemap.html"
        severity = risk.get("sev", "low") if risk.get("sev") in ("high", "medium", "low") else "low"
        risk_items.append(f'<article class="risk-item glass"><span class="risk-severity {severity}">{severity.upper()}</span><h2>{html.escape(risk.get("msg", "Review finding"))}</h2><p>{html.escape(risk.get("file", ""))}:{risk.get("line", "")}</p><pre>{html.escape(risk.get("text", ""))}</pre><a href="{html.escape(target)}" class="text-link">Inspect source in the map &rarr;</a></article>')
    values = {
        "SHARED_CSS": shared, "OVERVIEW_CSS": (SOURCE / "overview.css").read_text(encoding="utf-8"),
        "APP_VERSION": VERSION,
        "OVERVIEW_JS": (SOURCE / "overview.js").read_text(encoding="utf-8"),
        "WHY": html.escape((data.get("why") or [{}])[0].get("text", "Explore the recorded changes.")),
        "CHANGED": str(scope["files"]), "AREA_COUNT": str(len(cards)), "AREAS": "".join(cards),
        "HIGH": str(stats["risks"]["high"]), "RISKS": str(sum(stats["risks"].values())),
        "METRICS": "".join(f'<article class="metric-slide tone-{tone}" data-metric="{i}" role="group" aria-roledescription="slide" aria-label="{i+1} of {len(metrics)}: {label}" {"hidden" if i else ""}><div class="metric-value"><strong>{value}</strong><h2>{label}</h2><span>Recorded in this capture</span></div><div class="metric-explanation"><h3>{title}</h3><p>{note}</p><a href="{href}" class="button primary">{action} &rarr;</a></div></article>' for i,(value,label,tone,title,note,href,action) in enumerate(metrics)),
        "METRIC_TABS": "".join(f'<button data-metric-to="{i}" aria-pressed="{str(i == 0).lower()}">{label}</button>' for i,(_,label,*_) in enumerate(metrics)),
        "RISK_ITEMS": "".join(risk_items),
    }
    overview = (SOURCE / "index.template.html").read_text(encoding="utf-8")
    for key, value in values.items():
        overview = overview.replace("__" + key + "__", value)
    overview = overview.replace("\u2197", "\u2192")
    (OUT / "index.html").write_text(overview, encoding="utf-8")
    print(f"Refreshed overview and map; preserved {len(data.get('audio', {}))} recorded audio clips.")


if __name__ == "__main__":
    refresh()
