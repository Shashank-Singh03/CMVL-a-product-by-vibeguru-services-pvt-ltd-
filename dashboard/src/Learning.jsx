import React, { useEffect, useRef, useState } from "react";
import { completedLessons } from "./learningData";

export function GenerationProgress({ progress, compact = false }) {
  if (!progress?.total) return <p className="cm-progress-empty">Generation progress is not recorded.</p>;
  return <div className={`cm-generation-progress ${compact ? "cm-progress-compact" : ""}`}>
    <div><span>{progress.label || "Generation stages"}</span><strong>{progress.done} / {progress.total}</strong></div>
    <progress value={progress.done} max={progress.total} aria-label="Generation stages completed" />
    {!compact && <small>{progress.done === progress.total ? "All recorded generation stages finished." : `${progress.total - progress.done} stages left${progress.current ? ` · ${progress.current}` : ""}`}</small>}
  </div>;
}

function Concept({ concept, selected, onSelect }) {
  return <button className={`cm-concept-chip ${selected ? "cm-concept-selected" : ""}`} onClick={() => onSelect(concept.id)} aria-pressed={selected}>
    {concept.term}<span aria-hidden="true">?</span>
  </button>;
}

function HighlightedText({ value, concepts, onSelect }) {
  if (!value) return null;
  const names = concepts.map(c => c.term).filter(Boolean).sort((a,b) => b.length-a.length);
  if (!names.length) return <>{value}</>;
  const expression = new RegExp(`(?<![A-Za-z0-9_])(${names.map(s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})(?![A-Za-z0-9_])`, "gi");
  return value.split(expression).map((part, i) => {
    const concept = concepts.find(c => c.term.toLocaleLowerCase() === part.toLocaleLowerCase());
    return concept ? <button key={i} className="cm-inline-definition" onClick={() => onSelect(concept.id)} title={`Define ${concept.term}`}>{part}</button> : <React.Fragment key={i}>{part}</React.Fragment>;
  });
}

function Change({ change, focused, onFocus }) {
  return <article className={`cm-change-example ${focused ? "cm-change-focused" : ""}`}>
    <button className="cm-change-heading" onClick={() => onFocus(change.id)} aria-pressed={focused}>
      <span><b>{change.title || change.file || "Changed item"}</b><small>{change.file || "Captured change"}</small></span>
      <span className={`cm-change-tag cm-change-${change.status}`}>{change.status || "Changed"}</span>
    </button>
    <div className="cm-change-tags">{change.language && <span>{change.language}</span>}{change.domain && <span>{change.domain}</span>}</div>
    {change.summary && <p>{change.summary}</p>}
    {change.mapUrl && <a className="cm-button cm-button-sm cm-change-map-link" href={change.mapUrl}>See this item in the map →</a>}
    {change.lines.length ? <div className="cm-code-change" tabIndex="0" role="region" aria-label={`Highlighted changes for ${change.title || change.file}`}>
      {change.lines.map((line, i) => <div key={i} className={`cm-code-line cm-line-${line.kind}`} data-line-kind={line.kind}><span className="cm-code-line-number">{line.line ?? ""}</span><span className="cm-code-sign" aria-label={line.kind}>{line.kind === "added" ? "+" : line.kind === "removed" ? "−" : " "}</span><code>{line.text}</code></div>)}
    </div> : <p className="cm-muted">No code excerpt was included. Open the map for the available captured context.</p>}
  </article>;
}

export default function Learning({ tasks, selectedId, selectedStep, selectedItem, onSelect, onNavigate, onAnalysis, userState, onComplete }) {
  const task = tasks.find(t => t.id === selectedId) || tasks.find(t => t.learning.available) || tasks[0];
  const [conceptId, setConceptId] = useState(null);
  const [moreTerms, setMoreTerms] = useState(false);
  const [glossaryQuery, setGlossaryQuery] = useState("");
  const heading = useRef(null);
  useEffect(() => { setConceptId(null); setMoreTerms(false); setGlossaryQuery(""); }, [task?.id, task?.learning.revision, selectedStep]);
  if (!task) return <div className="cm-empty"><h2>Your learning library starts with a captured change</h2><p>After a task has a map, its available explanations and concepts will appear here.</p></div>;
  const learning = task.learning;
  const done = completedLessons(task, userState);
  const firstUnread = learning.steps.findIndex(s => !done.includes(s.id));
  const index = Math.max(0, selectedStep ? learning.steps.findIndex(s => s.id === selectedStep) : firstUnread >= 0 ? firstUnread : 0);
  const step = learning.steps[index];
  const concepts = learning.concepts.filter(c => step?.conceptIds.includes(c.id));
  const shownConcepts = moreTerms ? concepts : concepts.slice(0, 4);
  const changes = step?.changes || [];
  const changeIndex = Math.max(0, changes.findIndex(c => c.id === selectedItem));
  const focusedChange = changes[changeIndex];
  const selectedConcept = learning.concepts.find(c => c.id === conceptId);
  const searchableConcepts = learning.concepts.filter(c => `${c.term} ${c.definition}`.toLocaleLowerCase().includes(glossaryQuery.trim().toLocaleLowerCase()));
  function go(next) { onNavigate(task.id, learning.steps[next]?.id); setConceptId(null); requestAnimationFrame(() => heading.current?.focus()); }
  function focusChange(id) { onNavigate(task.id, "changes", id); }
  function related(c, side) {
    const id = c[side + "Id"], label = c[side];
    const exists = learning.steps.some(s => s.changes.some(change => change.id === id));
    return exists ? <button className="cm-text-link" onClick={() => focusChange(id)}>{label}</button>
      : c[side + "MapUrl"] ? <a className="cm-text-link" href={c[side + "MapUrl"]}>{label} →</a> : <strong>{label}</strong>;
  }
  return <section className="cm-learning" aria-label="Step-by-step learning" data-testid="learning-view">
    <div className="cm-learning-toolbar">
      <label>Learn a captured task<select aria-label="Select learning task" value={task.id} onChange={e => onSelect(e.target.value)}>{tasks.map(t => <option key={t.id} value={t.id}>{t.title}</option>)}</select></label>
      <div className="cm-context-links">{onAnalysis && <button className="cm-button cm-button-subtle" onClick={() => onAnalysis(task.id)}>Analyze this task →</button>}{task.mapUrl && <a className="cm-button cm-button-subtle" href={task.mapUrl}>Open full map <span aria-hidden="true">→</span></a>}</div>
    </div>
    {!learning.available ? <div className="cm-empty"><h2>Learning material is not ready yet</h2><p>{learning.reason || "The captured map and its explanations need to be available before a lesson can be assembled."}</p><GenerationProgress progress={task.progress} /></div> : <>
      <div className="cm-learning-banner"><div><span className="cm-eyebrow">LEARN THIS CHANGE</span><h2>{task.title}</h2><p>Follow the change, unpack the vocabulary, and check what you understand.</p></div><div className="cm-learning-score"><strong>{done.length}<span> / {learning.steps.length}</span></strong><span>lessons completed</span><progress value={done.length} max={learning.steps.length} aria-label="Lessons completed" /></div></div>
      <div className="cm-learning-layout">
        <label className="cm-mobile-lesson-picker">Choose a lesson<select aria-label="Choose a lesson" value={index} onChange={e => go(Number(e.target.value))}>{learning.steps.map((s,i) => <option key={s.id} value={i}>{i + 1}. {s.title}{done.includes(s.id) ? " (understood)" : ""}</option>)}</select><small>Your checklist tracks reading, not a test score.</small></label>
        <nav className="cm-lesson-nav" aria-label="Lesson steps"><span className="cm-eyebrow">YOUR LEARNING PATH</span>{learning.steps.map((s, i) => <button key={s.id} data-lesson-step={s.id} className={s.id === step?.id ? "cm-lesson-active" : ""} onClick={() => go(i)} aria-current={s.id === step?.id ? "step" : undefined}><span className={done.includes(s.id) ? "cm-lesson-done" : ""}>{done.includes(s.id) ? "✓" : String(i + 1).padStart(2,"0")}</span><b>{s.title}</b><span aria-hidden="true">›</span></button>)}<p>Progress is your reading checklist. It is not a score or a verification of understanding.</p></nav>
        <article className="cm-lesson-content">
          <div className="cm-lesson-kicker"><span>STEP {index + 1} OF {learning.steps.length}</span>{done.includes(step?.id) && <span className="cm-lesson-complete-tag">Marked understood</span>}</div>
          <h2 tabIndex="-1" ref={heading}>{step?.title}</h2>
          {step?.body && <div className="cm-lesson-body"><HighlightedText value={step.body} concepts={concepts} onSelect={setConceptId} /></div>}
          {concepts.length > 0 && <div className="cm-lesson-concepts"><h3>Terms for this step</h3><p>Select a term when you need its definition.</p><div>{shownConcepts.map(c => <Concept key={c.id} concept={c} selected={conceptId === c.id} onSelect={setConceptId} />)}</div>{concepts.length > 4 && <button className="cm-text-link" onClick={() => setMoreTerms(!moreTerms)}>{moreTerms ? "Show fewer terms" : `Show ${concepts.length - 4} more terms`}</button>}</div>}
          {selectedConcept && <aside className="cm-definition" aria-live="polite" data-testid="concept-definition"><div><span className="cm-eyebrow">DEFINITION</span><button onClick={() => setConceptId(null)} aria-label="Close definition">×</button></div><h3><mark>{selectedConcept.term}</mark></h3><p>{selectedConcept.definition}</p>{selectedConcept.example && <p className="cm-definition-example"><b>Example</b> {selectedConcept.example}</p>}</aside>}
          {changes.length > 0 && <section className="cm-lesson-changes" aria-label="Highlighted changes"><div className="cm-change-picker"><label>Change {changeIndex + 1} of {changes.length}<select aria-label="Select change example" value={focusedChange.id} onChange={e => focusChange(e.target.value)}>{changes.map(c => <option key={c.id} value={c.id}>{c.title} · {c.file}</option>)}</select></label><div><button className="cm-button cm-button-sm" disabled={changeIndex === 0} onClick={() => focusChange(changes[changeIndex - 1].id)}>← Previous change</button><button className="cm-button cm-button-sm" disabled={changeIndex === changes.length - 1} onClick={() => focusChange(changes[changeIndex + 1].id)}>Next change →</button></div></div><div className="cm-change-legend"><span>+ Added</span><span>− Removed</span><small>Selected redacted excerpt</small></div><Change change={focusedChange} focused onFocus={focusChange} /></section>}
          {step?.connections.length > 0 && <section className="cm-connections"><h3>Follow the connections</h3><p>Select an item to inspect its change or locate it in the map.</p>{step.connections.map((c,i) => <div key={i}>{related(c,"from")}<span aria-label={c.kind}>→<small>{c.kind}</small></span>{related(c,"to")}<em>{c.confidence === "model" ? "Model inference" : "Parsed relationship"}</em></div>)}</section>}
          {step?.checks.length > 0 && <section className="cm-learning-checks"><h3>Pause and check your understanding</h3><p>Try answering in your own words before revealing the explanation.</p>{step.checks.map((check,i) => <details key={i}><summary>{check.question}<span>Reveal explanation</span></summary><p>{check.answer}</p></details>)}</section>}
          <div className="cm-lesson-actions"><button className="cm-button" disabled={index === 0} onClick={() => go(index - 1)}>← Previous</button><button className="cm-button cm-button-primary" onClick={() => { onComplete(task, step.id); if (index < learning.steps.length - 1) go(index + 1); }}>{done.includes(step?.id) ? index === learning.steps.length - 1 ? "Keep marked understood" : "Next lesson" : index === learning.steps.length - 1 ? "Mark understood" : "Mark understood & continue"}<span aria-hidden="true">✓</span></button></div>
          {done.length === learning.steps.length && <p className="cm-learning-finished" role="status">You completed your reading checklist for this change. Revisit any lesson whenever you need the context.</p>}
        </article>
      </div>
      <details className="cm-glossary cm-glossary-disclosure"><summary>Browse all {learning.concepts.length} definitions</summary><label><span className="cm-sr">Search definitions</span><input type="search" aria-label="Search definitions" value={glossaryQuery} onChange={e => setGlossaryQuery(e.target.value)} placeholder="Find a term or definition…" /></label><div className="cm-glossary-grid">{searchableConcepts.map(c => <details key={c.id}><summary>{c.term}<span>+</span></summary><p>{c.definition}</p>{c.example && <p><b>Example:</b> {c.example}</p>}</details>)}{!searchableConcepts.length && <p className="cm-muted">No matching definitions.</p>}</div></details>
    </>}
  </section>;
}
