import React, { useEffect, useRef, useState } from "react";
import "./controls.css";

const STAGES = ["analysis", "review", "render", "video"];
const isServer = () => /^https?:$/.test(location.protocol) &&
  window.__CODEMAP_SERVER__?.version === 1 && window.__CODEMAP_SERVER__?.apiBase === "/api";
const message = (value, fallback) => typeof value === "string" && value ? value.slice(0, 500) : fallback;

function safeSettings(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const result = {};
  for (const key of ["enabled", "video", "webm"]) {
    if (typeof value[key] !== "boolean") return null;
    result[key] = value[key];
  }
  for (const [key, min, max] of [["minutes", 0.1, 10], ["budget", 256, 32000], ["timeout", 10, 3600], ["jobs", 1, 4]]) {
    const number = value[key];
    if (typeof number !== "number" || !Number.isFinite(number) || number < min || number > max ||
      (key !== "minutes" && !Number.isInteger(number))) return null;
    result[key] = number;
  }
  if (![720, 1080].includes(value.res) || !["auto", "os", "piper", "none"].includes(value.tts)) return null;
  result.res = value.res;
  result.tts = value.tts;
  return result;
}

// The session token stays in this hook's memory. Static pages never probe /api.
export function useLocalControls(onHistory) {
  const [status, setStatus] = useState(() => isServer() ? "connecting" : "readonly");
  const [settings, setSettings] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const supported = useRef(isServer()).current;
  const token = useRef("");
  const statusRef = useRef(status);
  const callback = useRef(onHistory);
  const alive = useRef(true);
  const mutation = useRef(false);
  callback.current = onHistory;

  function updateStatus(next) {
    statusRef.current = next;
    if (alive.current) setStatus(next);
  }
  function applyResponse(value) {
    if (!value || typeof value !== "object") throw new Error("The local server returned an invalid response.");
    if (value.history && alive.current) callback.current(value.history);
    if (value.settings) {
      const checked = safeSettings(value.settings);
      if (!checked) throw new Error("The local server returned unsupported settings.");
      if (alive.current) setSettings(checked);
    }
  }
  async function request(path, body) {
    if (!supported) throw new Error("Start the Codemap local server to use project controls.");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(`/api${path}`, {
        method: body === undefined ? "GET" : "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: body === undefined ? { Accept: "application/json" } : {
          Accept: "application/json", "Content-Type": "application/json", "X-Codemap-Token": token.current,
        },
        body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal,
      });
      const raw = await response.text();
      if (raw.length > 12000000) throw new Error("The local history response is too large.");
      let value;
      try { value = JSON.parse(raw); } catch { throw new Error("The local server returned an unreadable response."); }
      if (!response.ok) {
        const failure = new Error(message(value?.error, "The action could not be completed. Refresh and try again."));
        failure.status = response.status;
        throw failure;
      }
      return value;
    } finally { clearTimeout(timeout); }
  }
  async function connect() {
    if (!supported) return false;
    updateStatus("connecting");
    if (alive.current) setError("");
    try {
      const value = await request("/session");
      if (value.version !== 1 || typeof value.token !== "string" || value.token.length < 16 || value.token.length > 256)
        throw new Error("The local server session could not be verified.");
      applyResponse(value);
      token.current = value.token;
      updateStatus("connected");
      return true;
    } catch (failure) {
      token.current = "";
      updateStatus("lost");
      if (alive.current) setError(message(failure.message, "Could not connect to the Codemap local server."));
      return false;
    }
  }
  async function refresh() {
    if (statusRef.current !== "connected") {
      if (!await connect()) throw new Error("Could not connect to the local server. Saved history remains available.");
      return;
    }
    try {
      applyResponse(await request("/history"));
      if (alive.current) setError("");
    } catch (failure) {
      updateStatus("lost");
      if (alive.current) setError(message(failure.message, "Could not refresh the local server."));
      throw failure;
    }
  }
  async function mutate(key, path, body) {
    if (mutation.current) throw new Error("Another action is still finishing. Try again shortly.");
    if (statusRef.current !== "connected" || !token.current) throw new Error("Reconnect to the local server before changing this project.");
    mutation.current = true;
    if (alive.current) { setBusy(key); setError(""); }
    try {
      const value = await request(path, body);
      applyResponse(value);
      return message(value.message, "Changes saved.");
    } catch (failure) {
      if (failure.status === 409) {
        // Reconcile a stale view without repeating the user's mutation.
        try { applyResponse(await request("/history")); } catch { /* The original action error stays visible. */ }
      }
      if (!failure.status || [401, 403].includes(failure.status)) updateStatus("lost");
      if (alive.current) setError(message(failure.message, "The action could not be completed."));
      throw failure;
    } finally {
      mutation.current = false;
      if (alive.current) setBusy("");
    }
  }
  async function taskAction(task, action) {
    if (!/^[a-f0-9]{32}$/.test(task.id) || !["cancel", "retry"].includes(action) || task.controls?.[action] !== true)
      throw new Error("This action is not available for the current task state.");
    const body = {};
    if (Number.isInteger(task.controls.revision) && task.controls.revision > 0) body.revision = task.controls.revision;
    if (action === "retry") {
      if (!STAGES.includes(task.controls.stage)) throw new Error("No failed stage is available to retry.");
      body.stage = task.controls.stage;
    }
    return mutate(`task:${task.id}`, `/tasks/${task.id}/${action}`, body);
  }
  useEffect(() => {
    alive.current = true;
    if (supported) connect();
    return () => { alive.current = false; token.current = ""; };
  }, []);
  return { supported, status, settings, error, busy, connected: status === "connected", connect, refresh, taskAction,
    saveSettings: (value) => mutate("settings", "/settings", { settings: value }) };
}

export function ConnectionStatus({ control }) {
  const labels = { readonly: "Read-only dashboard", connecting: "Connecting controls", connected: "Local controls connected", lost: "Local controls disconnected" };
  return <div className={`cm-connection cm-connection-${control.status}`} role="status">
    <span className="cm-connection-dot" />
    <span>{labels[control.status]}</span>
    {control.status === "lost" && <button onClick={() => control.connect()} disabled={!!control.busy}>Reconnect</button>}
  </div>;
}

export function TaskControls({ task, control }) {
  const [confirm, setConfirm] = useState(false);
  const [notice, setNotice] = useState("");
  const [failure, setFailure] = useState("");
  useEffect(() => { setConfirm(false); setNotice(""); setFailure(""); }, [task.id, task.status]);
  async function act(action) {
    setNotice(""); setFailure("");
    try { setNotice(await control.taskAction(task, action)); setConfirm(false); }
    catch (error) { setFailure(message(error.message, "The action could not be completed.")); }
  }
  const canRetry = task.controls?.retry === true;
  const canCancel = task.controls?.cancel === true;
  return <section className="cm-detail-section cm-task-controls" aria-label="Task generation controls">
    <h3>Generation controls</h3>
    {!control.connected ? <p className="cm-muted">{control.status === "readonly" ? "Open this project with the Codemap local server to retry or cancel generation here. Maps, learning and videos remain available in this dashboard." : "Reconnect to the local server before changing this task."}</p>
      : <>
        {!canRetry && !canCancel && <p className="cm-muted">No generation action is available for this task state. Completed outputs and this task's capture are retained.</p>}
        <div className="cm-control-actions">
          {canRetry && <button className="cm-button cm-button-primary" disabled={!!control.busy} onClick={() => act("retry")}>
            {control.busy === `task:${task.id}` ? "Retrying…" : `Retry ${task.controls.stage}`}
          </button>}
          {canCancel && !confirm && <button className="cm-button cm-button-danger" disabled={!!control.busy} onClick={() => setConfirm(true)}>Cancel generation</button>}
        </div>
        {confirm && canCancel && <div className="cm-cancel-confirm" role="group" aria-label="Confirm cancellation">
          <strong>Cancel generation for this task?</strong>
          <p>Any completed map is kept. This capture cannot be resumed after cancellation; a new coding task creates a new capture.</p>
          <div className="cm-control-actions"><button className="cm-button" disabled={!!control.busy} onClick={() => setConfirm(false)}>Keep running</button>
            <button className="cm-button cm-button-danger" disabled={!!control.busy} onClick={() => act("cancel")}>{control.busy === `task:${task.id}` ? "Cancelling…" : "Confirm cancel"}</button></div>
        </div>}
      </>}
    {notice && <p className="cm-control-notice" role="status">{notice}</p>}
    {failure && <p className="cm-control-error" role="alert">{failure}</p>}
  </section>;
}

const FIELDS = [
  { key: "minutes", label: "Target video length", unit: "minutes", min: 0.1, max: 10, step: 0.1 },
  { key: "budget", label: "Label worksheet budget", unit: "tokens", min: 256, max: 32000, step: 1 },
  { key: "timeout", label: "Background stage timeout", unit: "seconds", min: 10, max: 3600, step: 1 },
  { key: "jobs", label: "Parallel video workers", unit: "workers", min: 1, max: 4, step: 1 },
];

export default function Settings({ control }) {
  const [draft, setDraft] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { if (!dirty && control.settings) setDraft({ ...control.settings }); }, [control.settings, dirty]);
  function update(key, value) { setDraft(previous => ({ ...previous, [key]: value })); setDirty(true); setSaved(""); setError(""); }
  async function submit(event) {
    event.preventDefault(); setSaved(""); setError("");
    const value = { ...draft };
    for (const field of FIELDS) value[field.key] = Number(value[field.key]);
    value.res = Number(value.res);
    const checked = safeSettings(value);
    if (!checked) { setError("Check the field ranges before saving. All numbers must be finite and worker, token and timeout values must be whole numbers."); return; }
    try { setSaved(await control.saveSettings(checked)); setDirty(false); }
    catch (failure) { setError(message(failure.message, "Project settings could not be saved.")); }
  }
  return <section className="cm-settings" aria-label="Project settings" data-testid="settings-view">
    <div className="cm-settings-intro"><span className="cm-eyebrow">MAKE THE WORKFLOW YOURS</span><h2>Project preferences</h2>
      <p>Choose how future coding tasks produce their walkthroughs. Existing captures keep the settings they started with.</p></div>
    <ConnectionStatus control={control} />
    {!control.connected ? <div className="cm-settings-offline">
      <h3>{control.status === "readonly" ? "Enable local project controls" : control.status === "connecting" ? "Connecting to this project" : "The local server is unavailable"}</h3>
      <p>The saved dashboard works on its own. Retry, cancel and settings use the Codemap server running on your computer.</p>
      <code>python &lt;codemap.py&gt; serve &lt;project&gt; --open</code>
      <p>Replace the placeholders with the installed script and your project folder. The command opens a server for that project only.</p>
      {control.error && <p className="cm-control-error" role="alert">{control.error}</p>}
    </div> : draft ? <form onSubmit={submit} className="cm-settings-form">
      <section className="cm-settings-card"><div><h3>Automatic walkthroughs</h3><p>These switches configure future tasks. Claude hooks must also be installed for automatic capture.</p></div>
        {[
          ["enabled", "Enable automatic mode", "Capture and prepare walkthroughs for future tasks using the installed hooks."],
          ["video", "Generate videos", "Create a walkthrough video after the reviewed map and explanations are ready."],
          ["webm", "Include a WebM copy", "Add an alternative video format for players such as VS Code previews."],
        ].map(([key, title, detail]) => <label className="cm-setting-toggle" key={key}><span><strong>{title}</strong><small>{detail}</small></span>
          <input type="checkbox" checked={draft[key]} onChange={event => update(key, event.target.checked)} disabled={!!control.busy} /></label>)}
      </section>
      <section className="cm-settings-card"><div><h3>Output and processing</h3><p>The worksheet budget is not a cap on the coding model's total token usage.</p></div>
        <div className="cm-settings-fields">{FIELDS.map(field => <label key={field.key}><span>{field.label}</span><div><input type="number" min={field.min} max={field.max} step={field.step} required value={draft[field.key]} onChange={event => update(field.key, event.target.value)} disabled={!!control.busy} /><small>{field.unit}</small></div><small>{field.min}–{field.max}</small></label>)}
          <label><span>Narration voice</span><select value={draft.tts} onChange={event => update("tts", event.target.value)} disabled={!!control.busy}><option value="auto">Automatic</option><option value="os">System voice</option><option value="piper">Piper</option><option value="none">No narration</option></select><small>Piper needs a local installation and voice model.</small></label>
          <label><span>Video resolution</span><select value={draft.res} onChange={event => update("res", Number(event.target.value))} disabled={!!control.busy}><option value={720}>720p</option><option value={1080}>1080p</option></select><small>Higher resolution can take longer to encode.</small></label>
        </div>
      </section>
      <div className="cm-settings-save"><div><strong>{dirty ? "You have unsaved changes" : "Project settings are up to date"}</strong><p>Saving updates this project's configuration for future tasks.</p></div>
        <div className="cm-control-actions"><button type="button" className="cm-button" disabled={!dirty || !!control.busy} onClick={() => { setDraft({ ...control.settings }); setDirty(false); setSaved(""); setError(""); }}>Discard changes</button>
          <button className="cm-button cm-button-primary" type="submit" disabled={!dirty || !!control.busy}>{control.busy === "settings" ? "Saving…" : "Save settings"}</button></div>
      </div>
      {saved && <p className="cm-control-notice" role="status">{saved}</p>}
      {error && <p className="cm-control-error" role="alert">{error}</p>}
    </form> : <p className="cm-muted">Loading project settings…</p>}
  </section>;
}
