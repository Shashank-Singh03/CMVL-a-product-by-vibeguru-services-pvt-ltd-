import React, { useEffect, useMemo, useState } from "react";
import "./compare.css";

const METRICS = [
  ["changedFiles", "Changed files"], ["added", "Added lines"], ["removed", "Removed lines"],
  ["symbols", "Graph symbols"], ["edges", "Graph connections"],
];
const numeric = value => Number.isInteger(value) && value >= 0 ? value : null;
const display = value => numeric(value) === null ? "Not recorded" : value.toLocaleString();
const signed = value => value === 0 ? "No difference" : `${value > 0 ? "+" : "−"}${Math.abs(value).toLocaleString()}`;
const date = value => {
  const parsed = new Date(value || "invalid");
  return Number.isFinite(parsed.getTime()) ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(parsed) : "Date not recorded";
};
const short = task => `${task.id.slice(0, 8)} · ${task.title}`;
const changes = task => (task.learning?.steps || []).flatMap(step => step.changes || []).slice(0, 30);
const identity = change => `${change.file}\u0000${change.id}`;
const buckets = (left, right, field) => {
  const all = new Map();
  for (const [side, task] of [["left", left], ["right", right]]) {
    for (const bucket of task[field] || []) {
      if (!bucket || !bucket.name || numeric(bucket.count) === null) continue;
      const entry = all.get(bucket.name) || { name: bucket.name, label: bucket.label || bucket.name, left: null, right: null };
      entry[side] = bucket.count;
      all.set(bucket.name, entry);
    }
  }
  return [...all.values()].sort((a, b) => a.label.localeCompare(b.label));
};

function publicText(value) {
  return typeof value === "string" ? value.slice(0, 2000)
    .replace(/[A-Za-z]:[\\/][^\s<>"'`]+/g, "[local path]")
    .replace(/\\\\[^\s<>"'`]+/g, "[local path]")
    .replace(/\/(?:home|Users|private|tmp|var|mnt|workspace|workspaces)\/[^\s<>"'`]+/g, "[local path]")
    .replace(/[\r\n]+/g, " ") : "";
}
function markdown(value) { return publicText(value).replace(/[|`*_\[\]<>]/g, "\\$&"); }
function exportMarkdown(left, right) {
  const lines = ["# Captured task comparison", "", "Side-by-side summaries of two saved captures. This is not a source diff between their final snapshots.", "",
    `## A: ${markdown(left.title)}`, `Task: ${left.id}`, `Captured: ${date(left.createdAt)}`, "", markdown(left.summary), "",
    `## B: ${markdown(right.title)}`, `Task: ${right.id}`, `Captured: ${date(right.createdAt)}`, "", markdown(right.summary), "",
    "## Recorded metrics", "", "| Metric | A | B | B minus A |", "| --- | ---: | ---: | ---: |"];
  for (const [key, label] of METRICS) {
    const a = numeric(left[key]), b = numeric(right[key]);
    lines.push(`| ${label} | ${display(a)} | ${display(b)} | ${a === null || b === null ? "Not comparable" : signed(b - a)} |`);
  }
  lines.push("", "Added and removed lines describe each task's own baseline. Their count difference is not a code diff between A and B.");
  for (const [label, field] of [["Languages and formats", "languages"], ["System areas", "domains"]]) {
    lines.push("", `## ${label}`, "", "| Category | A parsed files | B parsed files |", "| --- | ---: | ---: |");
    for (const bucket of buckets(left, right, field)) lines.push(`| ${markdown(bucket.label)} | ${display(bucket.left)} | ${display(bucket.right)} |`);
  }
  lines.push("", "## Selected change examples", "", "These lists are capped learning selections, not complete inventories of source changes.");
  for (const [label, task] of [["A", left], ["B", right]]) {
    lines.push("", `### ${label}`, "");
    const selected = changes(task);
    if (!selected.length) lines.push("No selected learning examples are available.");
    for (const change of selected) lines.push(`- ${markdown(change.title || change.file)} — ${markdown(change.file)} (${markdown(change.status)})`);
  }
  lines.push("", "File counts include parsed context and can repeat across captures. Source-review coverage is not an accuracy or runtime-test score. This export includes public summaries and selected example labels, not raw snapshots, access tokens or code excerpts.", "");
  return lines.join("\n");
}

function TaskSummary({ task, label, onLearn, onDetails }) {
  const progress = task.progress;
  const review = task.reviewCoverage;
  return <article className="cm-compare-task" data-compare-side={label}>
    <div className="cm-compare-task-top"><span>CAPTURE {label}</span><span className={`cm-badge cm-badge-${task.category}`}><span />{task.statusLabel}</span></div>
    <h3>{task.title}</h3><p>{task.summary}</p>
    <div className="cm-compare-capture"><code>{task.id.slice(0, 8)}</code><time dateTime={task.createdAt || undefined}>{date(task.createdAt)}</time></div>
    <dl className="cm-compare-stages"><div><dt>Generation stages</dt><dd>{progress?.available && progress.total ? `${progress.done} / ${progress.total}` : "Not recorded"}</dd></div><div><dt>Reviewed / parsed files</dt><dd>{review?.total !== null && review?.total !== undefined ? `${review.reviewed ?? "Not recorded"} / ${review.total}` : "Not recorded"}</dd></div></dl>
    <div className="cm-compare-actions">{task.mapUrl && <a className="cm-button cm-button-primary cm-button-sm" href={task.mapUrl}>Open map <span aria-hidden="true">→</span></a>}{task.learning?.available && onLearn && <button className="cm-button cm-button-sm" onClick={() => onLearn(task.id)}>Learn changes</button>}{onDetails && <button className="cm-button cm-button-subtle cm-button-sm" onClick={() => onDetails(task.id)}>Task details</button>}</div>
  </article>;
}

function BucketTable({ title, rows }) {
  return <section className="cm-compare-panel"><h3>{title}</h3><p>Recorded parsed-file counts, including unchanged context.</p>{rows.length ? <div className="cm-compare-table-scroll"><table><caption className="cm-sr">{title} by capture</caption><thead><tr><th scope="col">Category</th><th scope="col">Capture A</th><th scope="col">Capture B</th></tr></thead><tbody>{rows.map(row => <tr key={row.name}><th scope="row">{row.label}</th><td>{display(row.left)}</td><td>{display(row.right)}</td></tr>)}</tbody></table></div> : <p className="cm-compare-unavailable">No recorded breakdown is available.</p>}</section>;
}

function Selection({ task, other, label }) {
  const selected = changes(task);
  const shared = new Set(changes(other).map(identity));
  return <section className="cm-compare-panel cm-compare-selection"><h3>Capture {label} selection</h3><p>{selected.length ? `${selected.length} selected example${selected.length === 1 ? "" : "s"}. Expand an item to inspect its recorded excerpt.` : "No learning excerpts are available for this capture."}</p>{selected.map(change => <details key={identity(change)} className="cm-compare-example"><summary><span><b>{change.title || "Mapped item"}</b><small>{change.file || "File not recorded"}</small></span><em className={shared.has(identity(change)) ? "cm-compare-shared" : ""}>{shared.has(identity(change)) ? "In both selections" : "Only in this selection"}</em></summary>{change.summary && <p>{change.summary}</p>}{change.lines?.length ? <div className="cm-compare-code" tabIndex="0" role="region" aria-label={`Capture ${label} excerpt for ${change.title || change.file}`}>{change.lines.map((line, index) => <div key={index} className={`cm-compare-line cm-compare-line-${line.kind}`}><span aria-hidden="true">{line.kind === "added" ? "+" : line.kind === "removed" ? "−" : " "}</span><code>{line.text}</code></div>)}</div> : <p>No code excerpt was included.</p>}</details>)}</section>;
}

export default function Compare({ tasks = [], onLearn, onDetails }) {
  const ordered = useMemo(() => [...tasks].sort((a, b) => (Date.parse(a.createdAt) || 0) - (Date.parse(b.createdAt) || 0) || a.id.localeCompare(b.id)), [tasks]);
  const [leftId, setLeftId] = useState("");
  const [rightId, setRightId] = useState("");
  const [exportError, setExportError] = useState(false);
  const left = ordered.find(task => task.id === leftId) || ordered[ordered.length - 2] || ordered[0];
  const right = ordered.find(task => task.id === rightId && task.id !== left?.id) || [...ordered].reverse().find(task => task.id !== left?.id);
  // Pin the chosen pair so a newly captured task does not replace the work
  // someone is currently comparing during a background history refresh.
  useEffect(() => {
    if (leftId !== (left?.id || "")) setLeftId(left?.id || "");
    if (rightId !== (right?.id || "")) setRightId(right?.id || "");
  }, [left?.id, right?.id, leftId, rightId]);
  if (!left || !right) return <section className="cm-compare cm-compare-empty" aria-label="Compare captured tasks" data-testid="compare-view"><span className="cm-eyebrow">PUT TWO CHANGES IN CONTEXT</span><h2>{left ? "One more capture unlocks comparison" : "Compare your captured work"}</h2><p>Capture at least two tasks in this project to compare their saved summaries, metrics and selected change examples.</p>{left && <TaskSummary task={left} label="A" onLearn={onLearn} onDetails={onDetails} />}</section>;
  function swap() { setLeftId(right.id); setRightId(left.id); }
  function download() {
    try {
      const url = URL.createObjectURL(new Blob([exportMarkdown(left, right)], { type: "text/markdown;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url; link.download = `codemap-compare-${left.id.slice(0, 8)}-${right.id.slice(0, 8)}.md`;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000); setExportError(false);
    } catch { setExportError(true); }
  }
  return <section className="cm-compare" aria-label="Compare captured tasks" data-testid="compare-view">
    <div className="cm-compare-intro"><div><span className="cm-eyebrow">PUT TWO CHANGES IN CONTEXT</span><h2>Compare captured tasks</h2><p>See each task's recorded goal, scope and generation state together.</p></div><button className="cm-button cm-button-sm" onClick={download}>Download comparison <span aria-hidden="true">↓</span></button></div>
    {exportError && <p className="cm-compare-error" role="alert">The comparison could not be downloaded. Your saved task outputs remain available.</p>}
    <div className="cm-compare-picker"><label>Capture A<select aria-label="Capture A" value={left.id} onChange={event => setLeftId(event.target.value)}>{ordered.map(task => <option key={task.id} value={task.id} disabled={task.id === right.id}>{short(task)}</option>)}</select></label><button className="cm-icon-button cm-compare-swap" onClick={swap} aria-label="Swap captures"><span aria-hidden="true">⇄</span></button><label>Capture B<select aria-label="Capture B" value={right.id} onChange={event => setRightId(event.target.value)}>{ordered.map(task => <option key={task.id} value={task.id} disabled={task.id === left.id}>{short(task)}</option>)}</select></label></div>
    <div className="cm-compare-note" role="note"><b>Two saved summaries, with their own baselines</b><p>This is not a source diff between the final snapshots. Added and removed lines belong to each task's own change window. Parsed context may appear in both captures.</p></div>
    <div className="cm-compare-grid"><TaskSummary task={left} label="A" onLearn={onLearn} onDetails={onDetails} /><TaskSummary task={right} label="B" onLearn={onLearn} onDetails={onDetails} /></div>
    <section className="cm-compare-panel cm-compare-metrics"><h3>Recorded metrics</h3><p>The difference column is B minus A for these counts; it does not describe edits between the captures.</p><div className="cm-compare-table-scroll"><table><caption className="cm-sr">Recorded metrics by capture</caption><thead><tr><th scope="col">Metric</th><th scope="col">A</th><th scope="col">B</th><th scope="col">Count difference</th></tr></thead><tbody>{METRICS.map(([key, label]) => { const a = numeric(left[key]), b = numeric(right[key]); return <tr key={key} data-metric={key}><th scope="row">{label}</th><td>{display(a)}</td><td>{display(b)}</td><td className="cm-compare-delta">{a === null || b === null ? "Not comparable" : signed(b - a)}</td></tr>; })}</tbody></table></div></section>
    <div className="cm-compare-grid"><BucketTable title="Languages and formats" rows={buckets(left, right, "languages")} /><BucketTable title="System areas" rows={buckets(left, right, "domains")} /></div>
    <section className="cm-compare-excerpts"><div className="cm-compare-section-heading"><h2>Selected change examples</h2><p>These are limited learning selections. “Only in this selection” does not mean an entity was added or deleted from the project. Open the saved maps for wider context.</p></div><div className="cm-compare-grid"><Selection task={left} other={right} label="A" /><Selection task={right} other={left} label="B" /></div></section>
  </section>;
}
