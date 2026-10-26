const string = (value, limit = 1200) => typeof value === "string" ? value.slice(0, limit) : "";
const count = (value) => Number.isInteger(value) && value >= 0 && value <= 1000000000 ? value : null;
export function safeBuckets(value) {
  return Array.isArray(value) ? value.slice(0, 60).filter(v => v && typeof v === "object" && count(v.count) !== null)
    .map(v => ({ name: string(v.name, 80), label: string(v.label || v.name, 100), count: v.count })).filter(v => v.name) : [];
}
export function safeProgress(value, steps = []) {
  if (value?.available === false) return { done: 0, total: 0, percent: null, current: "", remaining: [], label: "Generation stages", available: false };
  const total = count(value?.total) ?? steps.length;
  const done = Math.min(count(value?.done) ?? steps.filter(s => s.state === "done").length, total);
  return { done, total, percent: total ? Math.round(done / total * 100) : null,
    current: string(value?.current, 100), remaining: Array.isArray(value?.remaining) ? value.remaining.slice(0, 10).map(v => string(v, 100)) : steps.filter(s => s.state !== "done").map(s => s.label),
    label: "Generation stages", available: total > 0 };
}
export function safeLearning(value, taskId) {
  const mapLink = value => {
    const prefix = `tasks/${taskId}/output/codemap.html#`;
    if (typeof value !== "string" || value.length > 2400 || !value.startsWith(prefix)) return null;
    const params = new URLSearchParams(value.slice(prefix.length));
    if (!params.get("n") || [...params.keys()].some(key => !["v", "n"].includes(key))) return null;
    return prefix + params.toString();
  };
  const concepts = Array.isArray(value?.concepts) ? value.concepts.slice(0, 60).filter(v => v && typeof v === "object")
    .map(v => ({ id: string(v.id, 100), term: string(v.term, 120), definition: string(v.definition, 1800), example: string(v.example, 600), language: string(v.language, 80) })).filter(v => v.id && v.term) : [];
  const conceptIds = new Set(concepts.map(v => v.id));
  const steps = Array.isArray(value?.steps) ? value.steps.slice(0, 20).filter(v => v && typeof v === "object")
    .map(v => ({ id: string(v.id, 100), title: string(v.title, 180), kind: string(v.kind, 50), body: string(v.body, 8000),
      conceptIds: Array.isArray(v.conceptIds) ? v.conceptIds.filter(id => conceptIds.has(id)).slice(0, 60) : [],
      changes: Array.isArray(v.changes) ? v.changes.slice(0, 30).filter(c => c && typeof c === "object").map(c => ({
        id: string(c.id, 240), title: string(c.title, 240), summary: string(c.summary, 2000), file: string(c.file, 300), language: string(c.language, 80), domain: string(c.domain, 80), status: string(c.status, 50), mapUrl: mapLink(c.mapUrl),
        lines: Array.isArray(c.lines) ? c.lines.slice(0, 80).filter(l => l && typeof l === "object").map(l => ({ kind: ["added", "removed", "context"].includes(l.kind) ? l.kind : "context", text: string(l.text, 1500), line: count(l.line) })) : [] })) : [],
      connections: Array.isArray(v.connections) ? v.connections.slice(0, 40).filter(c => c && typeof c === "object").map(c => ({ from: string(c.from, 250), to: string(c.to, 250), fromId: string(c.fromId,100), toId:string(c.toId,100), fromMapUrl:mapLink(c.fromMapUrl),toMapUrl:mapLink(c.toMapUrl), kind: string(c.kind, 80), confidence: c.confidence === "model" ? "model" : "static" })) : [],
      checks: Array.isArray(v.checks) ? v.checks.slice(0, 12).filter(c => c && typeof c === "object").map(c => ({ question: string(c.question, 1200), answer: string(c.answer, 3000) })) : [],
    })).filter(v => v.id && v.title) : [];
  return { version: 1, revision: string(value?.revision, 160), available: value?.available === true && steps.length > 0,
    reason: string(value?.reason, 1200), steps, concepts };
}

const STORAGE_KEY = "codemap-learning-v1";
export function readUserState() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
    return Object.fromEntries(Object.entries(raw).slice(-200).filter(([id, v]) => /^[a-f0-9]{32}$/.test(id) && v && typeof v === "object")
      .map(([id, v]) => [id, { revision: string(v.revision, 160), viewed: v.viewed === true,
        done: Array.isArray(v.done) ? v.done.slice(0, 20).filter(x => typeof x === "string").map(x => x.slice(0, 100)) : [] }]));
  } catch { return {}; }
}
export function writeUserState(value) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(Object.entries(value).slice(-200)))); } catch { /* Learning remains usable without browser storage. */ }
}
export function completedLessons(task, userState) {
  const entry = userState[task.id];
  if (!entry || entry.revision !== task.learning.revision) return [];
  const valid = new Set(task.learning.steps.map(s => s.id));
  return [...new Set(entry.done)].filter(id => valid.has(id));
}
