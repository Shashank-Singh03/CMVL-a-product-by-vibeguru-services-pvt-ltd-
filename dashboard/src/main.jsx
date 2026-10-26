import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import "./learning.css";
import "../../skills/codemap/assets/studio.css";
import "./studio.css";
import "./glass.css";
import Snapshot from "./Snapshot";
import MapWorkspace from "./MapWorkspace";
const APP_VERSION = __CODEMAP_APP_VERSION__;
import Learning, { GenerationProgress } from "./Learning";
import Analysis from "./Analysis";
import Settings, { ConnectionStatus, TaskControls, useLocalControls } from "./Controls";
import Compare from "./Compare";
import { safeBuckets, safeLearning, safeProgress, readUserState, writeUserState, completedLessons } from "./learningData";
import { routeHash, useDashboardNavigation } from "./navigation";

const ICONS = {
  sidebar: <><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16m5-11-3 3 3 3"/></>,
  menu: <path d="M4 6h16M4 12h16M4 18h16"/>,
  settings: <><path d="m9 3-.8 2.3-2.4.5-1.5 2.6 1.2 2.1L5 13l1.5 2.6 2.4.5L10 19h3l.9-2.9 2.4-.5 1.5-2.6-1.2-2.1 1.2-2.1-1.5-2.6-2.4-.5L13 3Z" /><circle cx="11.5" cy="11" r="3" /></>,
  compare: <><path d="M3 5h7v14H3zM14 5h7v14h-7zM10 12h4" /></>,
  learn: <><path d="m2 8 10-5 10 5-10 5Z" /><path d="M6 10v7c4 3 8 3 12 0v-7M22 8v9" /></>,
  analysis: <><path d="M4 3v18h18M8 16v-5m5 5V7m5 9V4" /></>,
  grid: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </>
  ),
  play: <path d="m9 5 11 7-11 7z" />,
  video: (
    <>
      <rect x="3" y="5" width="14" height="14" rx="3" />
      <path d="m17 9 4-3v12l-4-3" />
    </>
  ),
  alert: (
    <>
      <path d="m10.3 4.8-8 13.8A1.6 1.6 0 0 0 3.7 21h16.6a1.6 1.6 0 0 0 1.4-2.4l-8-13.8a2 2 0 0 0-3.4 0Z" />
      <path d="M12 9v4m0 4h.01" />
    </>
  ),
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </>
  ),
  refresh: (
    <>
      <path d="M20 8a8.1 8.1 0 0 0-14-3L3 8m0-5v5h5M4 16a8.1 8.1 0 0 0 14 3l3-3m0 5v-5h-5" />
    </>
  ),
  moon: <path d="M20.5 13.4A8.5 8.5 0 0 1 10.6 3.5a8.6 8.6 0 1 0 9.9 9.9Z" />,
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" />
    </>
  ),
  arrow: (
    <>
      <path d="M5 12h14m-5-5 5 5-5 5" />
    </>
  ),
  external: (
    <>
      <path d="M14 3h7v7m0-7L10 14" />
      <path d="M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5" />
    </>
  ),
  close: <path d="m6 6 12 12M6 18 18 6" />,
  check: <path d="m5 12 4 4L19 6" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  file: (
    <>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" />
      <path d="M14 2v6h6M8 13h8m-8 4h5" />
    </>
  ),
  nodes: (
    <>
      <rect x="9" y="2" width="6" height="6" rx="1.5" />
      <rect x="2" y="16" width="6" height="6" rx="1.5" />
      <rect x="16" y="16" width="6" height="6" rx="1.5" />
      <path d="M12 8v4M5 16v-4h14v4" />
    </>
  ),
  shield: (
    <>
      <path d="m12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6Z" />
      <path d="m8 12 3 3 5-6" />
    </>
  ),
  copy: (
    <>
      <rect x="8" y="8" width="13" height="13" rx="2" />
      <path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" />
    </>
  ),
  download: (
    <>
      <path d="M12 3v12m-5-5 5 5 5-5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" />
    </>
  ),
  more: (
    <>
      <circle cx="5" cy="12" r="1" />
      <circle cx="12" cy="12" r="1" />
      <circle cx="19" cy="12" r="1" />
    </>
  ),
  chevron: <path d="m9 5 7 7-7 7" />,
  folder: (
    <path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
  ),
};
function Icon({ name, size = 18, ...props }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {ICONS[name] || ICONS.file}
    </svg>
  );
}
const text = (value, fallback = "", limit = 1200) =>
  typeof value === "string" ? value.slice(0, limit) : fallback;
const number = (value) =>
  Number.isInteger(value) && value >= 0 && value <= 10000000 ? value : null;
function safeData(raw) {
  if (!raw || raw.version !== 1 || !Array.isArray(raw.tasks))
    throw new Error("Invalid history metadata");
  const tasks = raw.tasks
    .slice(0, 1000)
    .filter((t) => t && typeof t.id === "string" && /^[a-f0-9]{32}$/.test(t.id))
    .map((t) => {
      const url = (value, file) =>
        value === `tasks/${t.id}/output/${file}` ? value : null;
      const category = ["complete", "active", "attention", "skipped"].includes(
        t.category,
      )
        ? t.category
        : "attention";
      return {
        ...t,
        id: t.id,
        category,
        status: text(t.status, "state_unavailable", 100),
        statusLabel: text(t.statusLabel, "Status unavailable", 100),
        title: text(t.title, `Task ${t.id.slice(0, 8)}`, 200),
        summary: text(t.summary),
        createdAt: typeof t.createdAt === "string" ? t.createdAt : null,
        updatedAt: typeof t.updatedAt === "string" ? t.updatedAt : null,
        reviewedFiles: number(t.reviewedFiles),
        changedFiles: number(t.changedFiles),
        symbols: number(t.symbols),
        edges: number(t.edges),
        added: number(t.added),
        removed: number(t.removed),
        attribution: text(t.attribution, "", 50),
        omittedCount: number(t.omittedCount) || 0,
        controls: { cancel: t.controls?.cancel === true, retry: t.controls?.retry === true,
          stage: ["analysis", "review", "render", "video"].includes(t.controls?.stage) ? t.controls.stage : null,
          revision: number(t.controls?.revision) },
        languages: safeBuckets(t.languages),
        domains: safeBuckets(t.domains),
        learning: safeLearning(t.learning, t.id),
        progress: safeProgress(["skipped", "cancelled", "state_unavailable", "interrupted"].includes(t.status) ? { available: false } : t.progress, Array.isArray(t.steps) ? t.steps.filter(s => s && typeof s === "object") : []),
        risks: { high: number(t.risks?.high) || 0, medium: number(t.risks?.medium) || 0, low: number(t.risks?.low) || 0 },
        reviewCoverage: { reviewed: number(t.reviewCoverage?.reviewed), total: number(t.reviewCoverage?.total), percent: typeof t.reviewCoverage?.percent === "number" && t.reviewCoverage.percent >= 0 && t.reviewCoverage.percent <= 100 ? t.reviewCoverage.percent : null },
        mapUrl: url(t.mapUrl, "codemap.html"),
        videoUrl:
          url(t.videoUrl, "codemap.mp4") || url(t.videoUrl, "codemap.webm"),
        downloadUrl: url(t.downloadUrl, "codemap.mp4"),
        webmUrl: url(t.webmUrl, "codemap.webm"),
        warnings: Array.isArray(t.warnings)
          ? t.warnings.slice(0, 20).map((v) => text(v))
          : [],
        steps: Array.isArray(t.steps)
          ? t.steps
              .slice(0, 10)
              .filter((s) => s && typeof s === "object")
              .map((s) => ({
                key: text(s.key, "", 50),
                label: text(s.label, "", 100),
                state: ["done", "current", "pending", "failed"].includes(
                  s.state,
                )
                  ? s.state
                  : "pending",
              }))
          : [],
      };
    });
  return {
    version: 1,
    appVersion: text(raw.appVersion, "", 80),
    project: text(raw.project, "Your project", 240),
    generatedAt: text(raw.generatedAt, "", 100),
    problem: raw.problem === true,
    projectProgress: safeProgress(raw.projectProgress || { done: tasks.reduce((s,t) => s + t.progress.done,0), total: tasks.reduce((s,t) => s+t.progress.total,0) }),
    supportedLanguages: Array.isArray(raw.supportedLanguages) ? raw.supportedLanguages.slice(0,80).filter(v => v && typeof v === "object").map(v => ({ name: text(v.name, "", 80), label: text(v.label || v.name, "", 100) })) : [],
    supportedDomains: Array.isArray(raw.supportedDomains) ? raw.supportedDomains.slice(0,30).filter(v => v && typeof v === "object").map(v => ({ name: text(v.name, "", 80), label: text(v.label || v.name, "", 100) })) : [],
    parserNotes: text(raw.parserNotes, "", 2000),
    tasks,
  };
}
function formatDate(value, compact = false) {
  const date = new Date(value || "invalid");
  if (!Number.isFinite(date.getTime())) return "Not recorded";
  return new Intl.DateTimeFormat(
    undefined,
    compact
      ? { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }
      : { dateStyle: "medium", timeStyle: "short" },
  ).format(date);
}
function initialTheme() {
  try {
    const stored = localStorage.getItem("codemap-theme");
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* Local files can disable storage. */
  }
  return matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}
function Badge({ task }) {
  return (
    <span className={`cm-badge cm-badge-${task.category}`}>
      <span />
      {task.statusLabel}
    </span>
  );
}
function Logo() {
  return (
    <span className="cm-logo">
      <svg
        width="25"
        height="25"
        viewBox="0 0 28 28"
        fill="none"
        aria-hidden="true"
      >
        <path
          d="m14 3 10 6v11l-10 6-10-6V9Z"
          stroke="currentColor"
          strokeWidth="1.6"
        />
        <path
          d="m4 9 10 6 10-6M14 15v11M9 6l10 6v5"
          stroke="currentColor"
          strokeWidth="1.6"
        />
        <circle cx="19" cy="17" r="2.3" fill="currentColor" />
      </svg>
    </span>
  );
}
function Modal({ className = "", children, onClose, labelledBy }) {
  const ref = useRef(null);
  useEffect(() => {
    const previous = document.activeElement;
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
    return () => {
      if (dialog?.open) dialog.close();
      previous?.focus?.();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className={className}
      aria-labelledby={labelledBy}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          const r = e.currentTarget.getBoundingClientRect();
          if (
            e.clientX < r.left ||
            e.clientX > r.right ||
            e.clientY < r.top ||
            e.clientY > r.bottom
          )
            onClose();
        }
      }}
    >
      {children}
    </dialog>
  );
}
function TaskCard({ task, onDetails, onVideo, onLearn, onSeen, isNew, lessonsDone }) {
  const current = task.steps.find((s) => s.state === "current");
  const normalized = (value) => value.trim().replace(/[.!?。…\s]+$/u, "");
  const description =
    normalized(task.summary) === normalized(task.title)
      ? ""
      : task.summary ||
        "Task details and available outputs are collected here.";
  return (
    <article className={`cm-task cm-task-${task.category}`}>
      <div className="cm-task-top">
        <span className="cm-task-mark">
          <Icon name="nodes" size={23} />
        </span>
        <Badge task={task} />
        <button
          className="cm-icon-button cm-more"
          aria-label={`Details for ${task.title}`}
          onClick={() => onDetails(task.id)}
        >
          <Icon name="more" />
        </button>
      </div>
      <div className="cm-task-heading">
        <span className="cm-task-id">
          TASK {task.id.slice(0, 8).toUpperCase()}
          {isNew && <span className="cm-new-badge">NEW</span>}
        </span>
        <h3>
          <button onClick={() => onDetails(task.id)}>{task.title}</button>
        </h3>
        {description && <p>{description}</p>}
      </div>
      {(task.languages.length > 0 || task.domains.length > 0) && <div className="cm-stack-tags">{task.languages.slice(0,4).map(v => <span key={v.name}>{v.label}</span>)}{task.domains.filter(v => v.name !== "code").slice(0,2).map(v => <span key={v.name}>{v.label}</span>)}</div>}
      <div className="cm-task-metrics">
        {task.changedFiles !== null && (
          <span title="Files changed during the captured task">
            <Icon name="file" size={15} />
            {task.changedFiles} changed{" "}
            {task.changedFiles === 1 ? "file" : "files"}
          </span>
        )}
        {task.reviewedFiles !== null && (
          <span title="Files with a recorded source review">
            <Icon name="shield" size={15} />
            {task.reviewedFiles} reviewed
          </span>
        )}
        {task.symbols !== null && (
          <span>
            <Icon name="nodes" size={15} />
            {task.symbols} symbols
          </span>
        )}
        {task.added !== null && task.removed !== null && (
          <span className="cm-diff">
            <b>+{task.added}</b>
            <b>−{task.removed}</b>
            <span className="cm-sr">lines</span>
          </span>
        )}
      </div>
      <GenerationProgress compact progress={task.progress} />
      {task.learning.available && <div className="cm-card-learning"><span>{lessonsDone} / {task.learning.steps.length} lessons completed</span><progress value={lessonsDone} max={task.learning.steps.length} aria-label={`Learning progress for ${task.title}`} /></div>}
      {task.category === "active" && (
        <div className="cm-active-line">
          <span className="cm-loader" />
          <span>{current?.label || task.statusLabel}</span>
          <div className="cm-mini-progress">
            {task.steps.map((step) => (
              <i key={step.key} className={step.state} />
            ))}
          </div>
        </div>
      )}
      {task.warnings.length > 0 && (
        <div className="cm-card-warning">
          <Icon name="alert" size={15} />
          <span>{task.warnings[0]}</span>
        </div>
      )}
      <div className="cm-task-footer">
        <div className="cm-task-actions">
          {task.learning.available && <button className="cm-button cm-button-learn cm-button-sm" onClick={() => onLearn(task.id)} aria-label={`Learn changes: ${task.title}`}><Icon name="learn" size={16} />Learn changes</button>}
          {task.mapUrl ? (
            <a
              className="cm-button cm-button-primary cm-button-sm"
              href={task.mapUrl}
              onClick={() => onSeen(task.id)}
            >
              Open map
              <Icon name="arrow" size={16} />
            </a>
          ) : (
            <button
              className="cm-button cm-button-subtle cm-button-sm"
              onClick={() => onDetails(task.id)}
            >
              {task.category === "active" ? "View progress" : "View details"}
              <Icon name="arrow" size={16} />
            </button>
          )}
          {task.videoUrl && (
            <button
              className="cm-button cm-button-subtle cm-button-sm"
              onClick={() => onVideo(task)}
            >
              <Icon name="play" size={15} />
              Preview video
            </button>
          )}
        </div>
        <time
          dateTime={task.createdAt || undefined}
          title={formatDate(task.createdAt)}
        >
          {formatDate(task.createdAt, true)}
        </time>
      </div>
    </article>
  );
}
function Detail({ task, onClose, onVideo, control, onLearn, onAnalysis }) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const idRef = useRef(null);
  async function copyId() {
    try {
      await navigator.clipboard.writeText(task.id);
      setCopied(true);
      setCopyError(false);
    } catch {
      idRef.current?.focus();
      idRef.current?.select();
      setCopyError(true);
    }
  }
  return (
    <Modal
      className="cm-dialog cm-detail-dialog"
      onClose={onClose}
      labelledBy="cm-detail-title"
    >
      <div className="cm-dialog-top">
        <span className="cm-eyebrow">TASK DETAILS</span>
        <button
          className="cm-icon-button"
          onClick={onClose}
          aria-label="Close task details"
        >
          <Icon name="close" size={20} />
        </button>
      </div>
      <div className="cm-detail-content">
        <Badge task={task} />
        <h2 id="cm-detail-title">{task.title}</h2>
        <p className="cm-detail-summary">{task.summary}</p>
        <div className="cm-detail-actions">
          <button className="cm-button" onClick={() => onLearn(task.id)}>Learn this change</button>
          <button className="cm-button" onClick={() => onAnalysis(task.id)}>Analyze this task</button>
          {task.mapUrl && (
            <a className="cm-button cm-button-primary" href={task.mapUrl}>
              Open map
              <Icon name="arrow" size={16} />
            </a>
          )}
          {task.videoUrl && (
            <button className="cm-button" onClick={() => onVideo(task)}>
              <Icon name="play" size={16} />
              Preview video
            </button>
          )}
        </div>
        <TaskControls task={task} control={control} />
        <section className="cm-detail-section">
          <h3>Task timeline</h3>
          <GenerationProgress progress={task.progress} />
          <ol className="cm-timeline">
            {task.steps.map((step) => (
              <li key={step.key} className={`cm-step-${step.state}`}>
                <span className="cm-step-dot">
                  {step.state === "done" ? (
                    <Icon name="check" size={13} />
                  ) : step.state === "failed" ? (
                    <Icon name="close" size={13} />
                  ) : null}
                </span>
                <span>{step.label}</span>
                <small>
                  {
                    {
                      done: "Done",
                      current: "In progress",
                      pending: "Pending",
                      failed: "Needs attention",
                    }[step.state]
                  }
                </small>
              </li>
            ))}
          </ol>
          {task.steps.length === 0 && (
            <p className="cm-muted">
              Timeline data is unavailable for this task.
            </p>
          )}
        </section>
        <section className="cm-detail-section">
          <h3>Snapshot information</h3>
          <dl className="cm-metadata">
            <div>
              <dt>Started</dt>
              <dd>{formatDate(task.createdAt)}</dd>
            </div>
            <div>
              <dt>Last updated</dt>
              <dd>{formatDate(task.updatedAt)}</dd>
            </div>
            {task.changedFiles !== null && (
              <div>
                <dt>Changed files</dt>
                <dd>{task.changedFiles}</dd>
              </div>
            )}
            {task.reviewedFiles !== null && (
              <div>
                <dt>Files source reviewed</dt>
                <dd>{task.reviewedFiles}</dd>
              </div>
            )}
            {task.reviewCoverage.total !== null && <div><dt>Source review / parsed files</dt><dd>{task.reviewCoverage.reviewed ?? "Not recorded"} / {task.reviewCoverage.total}</dd></div>}
            {task.symbols !== null && (
              <div>
                <dt>Graph symbols</dt>
                <dd>{task.symbols}</dd>
              </div>
            )}
            {task.edges !== null && (
              <div>
                <dt>Graph connections</dt>
                <dd>{task.edges}</dd>
              </div>
            )}
          </dl>
          <label className="cm-id-label" htmlFor="cm-full-task-id">
            Task ID
          </label>
          <div className="cm-copy-field">
            <input id="cm-full-task-id" ref={idRef} readOnly value={task.id} />
            <button
              className="cm-icon-button"
              aria-label="Copy task ID"
              onClick={copyId}
            >
              <Icon name={copied ? "check" : "copy"} size={16} />
            </button>
          </div>
          <p className="cm-copy-message" role="status">
            {copied
              ? "Task ID copied."
              : copyError
                ? "Select the task ID and copy it with your keyboard."
                : ""}
          </p>
        </section>
        {task.warnings.length > 0 && (
          <section className="cm-detail-section cm-warning-panel">
            <h3>
              <Icon name="alert" size={17} />
              Capture notes
            </h3>
            {task.warnings.map((warning, i) => (
              <p key={i}>{warning}</p>
            ))}
          </section>
        )}
        <section className="cm-detail-section cm-privacy-note">
          <Icon name="shield" size={20} />
          <p>
            Maps describe the captured task window. They do not establish who
            authored each edit. Private source snapshots are not linked from
            this dashboard.
          </p>
        </section>
        {(task.downloadUrl || task.webmUrl) && (
          <section className="cm-detail-section">
            <h3>Downloads</h3>
            <div className="cm-detail-actions">
              {task.downloadUrl && (
                <a
                  className="cm-button"
                  href={task.downloadUrl}
                  download={`codemap-${task.id.slice(0, 8)}.mp4`}
                >
                  <Icon name="download" size={16} />
                  MP4 video
                </a>
              )}
              {task.webmUrl && (
                <a
                  className="cm-button"
                  href={task.webmUrl}
                  download={`codemap-${task.id.slice(0, 8)}.webm`}
                >
                  <Icon name="download" size={16} />
                  WebM video
                </a>
              )}
            </div>
          </section>
        )}
      </div>
    </Modal>
  );
}
function Video({ task, onClose }) {
  return (
    <Modal
      className="cm-dialog cm-video-dialog"
      onClose={onClose}
      labelledBy="cm-video-title"
    >
      <div className="cm-video-header">
        <div>
          <span className="cm-eyebrow">VIDEO WALKTHROUGH</span>
          <h2 id="cm-video-title">{task.title}</h2>
        </div>
        <button
          className="cm-icon-button"
          onClick={onClose}
          aria-label="Close video preview"
        >
          <Icon name="close" size={21} />
        </button>
      </div>
      <video controls playsInline preload="metadata" src={task.videoUrl}>
        Your browser cannot play this video.{" "}
        <a href={task.videoUrl}>Open the video file</a>.
      </video>
      <div className="cm-video-footer">
        <span>
          <Icon name="shield" size={16} />
          Based on captured task sources
        </span>
        {task.downloadUrl && (
          <a
            className="cm-button cm-button-sm"
            href={task.downloadUrl}
            download={`codemap-${task.id.slice(0, 8)}.mp4`}
          >
            <Icon name="download" size={16} />
            Download MP4
          </a>
        )}
      </div>
    </Modal>
  );
}
function App() {
  const [data, setData] = useState(() => safeData(window.__CODEMAP_HISTORY__));
  const [theme, setTheme] = useState(initialTheme);
  const [collapsed, setCollapsed] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => {
    const close = e => { if (e.key === "Escape") { setMenuOpen(false); setCollapsed(true); document.getElementById(matchMedia("(max-width:760px)").matches ? "cm-menu-toggle" : "cm-nav-trigger")?.focus(); } };
    if (menuOpen || !collapsed) document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [menuOpen, collapsed]);
  const navigation = useDashboardNavigation();
  const { route, navigate } = navigation;
  const lastMap = useRef(null);
  if (route.view === "map") lastMap.current=route;
  const view = route.view;
  const learningId = route.task || null;
  const [userState, setUserState] = useState(readUserState);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [sort, setSort] = useState("newest");
  const detailId = ["tasks", "videos", "attention"].includes(view) ? route.task : null;
  const [video, setVideo] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState("");
  const search = useRef(null);
  const busy = useRef(false);
  const mounted = useRef(true);
  const control = useLocalControls(value => setData(safeData(value)));
  const tasks = data.tasks;
  const specialized = ["learning", "analysis", "settings", "compare", "map"].includes(view);
  const learnable = tasks.filter(t => t.learning.available).length;
  const counts = useMemo(
    () => ({
      total: tasks.length,
      complete: tasks.filter((t) => t.category === "complete").length,
      active: tasks.filter((t) => t.category === "active").length,
      attention: tasks.filter((t) => t.category === "attention").length,
      videos: tasks.filter((t) => t.videoUrl).length,
      maps: tasks.filter((t) => t.mapUrl).length,
    }),
    [tasks],
  );
  const detail = tasks.find((t) => t.id === detailId);
  const routeTask = tasks.find(t => t.id === route.task);
  const routeStep = routeTask?.learning.steps.find(s => s.id === route.step);
  const filtered = useMemo(() => {
    const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    return tasks
      .filter(
        (t) =>
          (view !== "videos" || t.videoUrl) &&
          (view !== "attention" || t.category === "attention") &&
          (status === "all" || t.category === status) &&
          words.every((word) =>
            `${t.title} ${t.summary} ${t.id} ${t.statusLabel}`
              .toLocaleLowerCase()
              .includes(word),
          ),
      )
      .sort((a, b) => {
        const diff =
          (Date.parse(b.createdAt) || 0) - (Date.parse(a.createdAt) || 0);
        return (sort === "oldest" ? -diff : diff) || a.id.localeCompare(b.id);
      });
  }, [tasks, query, view, status, sort]);
  useEffect(() => {
    writeUserState(userState);
  }, [userState]);
  useEffect(() => {
    document.querySelectorAll("meta[http-equiv]").forEach((meta) => {
      if (meta.httpEquiv.toLowerCase() === "refresh") meta.remove();
    });
    document.getElementById("codemap-fallback-styles")?.remove();
    document.documentElement.dataset.dashboard = "ready";
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("codemap-theme", theme);
    } catch {
      /* Theme still works when storage is disabled. */
    }
  }, [theme]);
  useEffect(() => {
    function keys(e) {
      if (
        e.key === "/" &&
        !e.ctrlKey &&
        !e.metaKey &&
        !e.altKey &&
        !["INPUT", "TEXTAREA", "SELECT"].includes(
          document.activeElement?.tagName,
        ) &&
        !document.activeElement?.isContentEditable &&
        !detailId &&
        !video
      ) {
        e.preventDefault();
        search.current?.focus();
      }
    }
    document.addEventListener("keydown", keys);
    return () => document.removeEventListener("keydown", keys);
  }, [detailId, video]);
  async function refresh(manual = true) {
    if (busy.current) return;
    if (control.supported) {
      busy.current = true;
      if (manual) setRefreshing(true);
      try {
        await control.refresh();
        if (mounted.current) setRefreshMessage(manual ? "History is up to date." : "");
      } catch {
        if (mounted.current) setRefreshMessage("Could not refresh the local server. Your saved task history remains available.");
      } finally {
        busy.current = false;
        if (mounted.current) setRefreshing(false);
      }
      return;
    }
    if (!/^https?:$/.test(location.protocol)) {
      if (manual) location.reload();
      return;
    }
    busy.current = true;
    if (manual) setRefreshing(true);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(location.href.split("#")[0], {
        cache: "no-store",
        credentials: "same-origin",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("History unavailable");
      const html = await response.text();
      if (html.length > 12000000) throw new Error("History too large");
      const doc = new DOMParser().parseFromString(html, "text/html");
      const raw = doc.getElementById("codemap-history-data")?.textContent;
      if (!raw || raw.length > 4000000)
        throw new Error("History metadata unavailable");
      const next = safeData(JSON.parse(raw));
      if (mounted.current) {
        setData(next);
        setRefreshMessage(manual ? "History is up to date." : "");
      }
    } catch {
      if (mounted.current)
        setRefreshMessage(
          "Could not refresh. Your saved task history is still available.",
        );
    } finally {
      clearTimeout(timer);
      busy.current = false;
      if (mounted.current) setRefreshing(false);
    }
  }
  useEffect(() => {
    if ((!counts.active && !control.connected) || !/^https?:$/.test(location.protocol)) return;
    const timer = setInterval(() => refresh(false), 5000);
    return () => clearInterval(timer);
  }, [counts.active, control.connected]);
  function dismissNavigation() { setCollapsed(true); setMenuOpen(false); }
  function openMap(task, params = {}) { if (!task?.mapUrl) return; markViewed(task.id); dismissNavigation(); setVideo(null); navigate({ ...(lastMap.current?.task === task.id ? lastMap.current : {}), view: "map", task: task.id, ...params }); }
  function followWorkspaceLink(event) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const href = event.target.closest?.("a")?.getAttribute("href");
    if (!href) return;
    const task = tasks.find(t => t.mapUrl && (href === t.mapUrl || href.startsWith(t.mapUrl + "#")));
    if (!task) return;
    const params = Object.fromEntries(new URLSearchParams(href.split("#")[1] || ""));
    event.preventDefault(); openMap(task, params.v || params.n ? { v: params.v, n: params.n } : {});
  }
  function changeView(next) {
    setCollapsed(true);
    setMenuOpen(false);
    setVideo(null);
    navigate({ view: next });
  }
  function openVideo(task) {
    markViewed(task.id);
    if (detailId) navigate({ view });
    setVideo(task);
  }
  function markViewed(id) {
    const task = tasks.find(t => t.id === id);
    if (!task) return;
    setUserState(previous => ({ ...previous, [id]: { viewed: true, revision: task.learning.revision, done: completedLessons(task, previous) } }));
  }
  function openDetails(id) { markViewed(id); navigate({ view: "tasks", task: id }); }
  function openLearning(id, step, item) {
    setCollapsed(true);
    setMenuOpen(false);
    markViewed(id);
    setVideo(null);
    navigate({ view: "learning", task: id, step, item });
  }
  function openAnalysis(id) { setVideo(null); navigate({ view: "analysis", task: id }); }
  function markLesson(task, id, understood = true) {
    if (!task.learning.steps.some(s => s.id === id)) return;
    setUserState(previous => {
      const done = new Set(completedLessons(task, previous));
      if (understood) done.add(id); else done.delete(id);
      return { ...previous, [task.id]: { viewed: true, revision: task.learning.revision, done: [...done] } };
    });
  }
  function clearFilters() {
    setQuery("");
    setStatus("all");
  }
  const viewTitle =
    view === "map" ? "Explore the change" :
    view === "settings" ? "Project settings" : view === "compare" ? "Compare captures" : view === "learning" ? "Learning path" : view === "analysis" ? "Project analysis" : view === "videos"
      ? "Video library"
      : view === "attention"
        ? "Needs attention"
        : "Task library";
  return (
    <div data-app-version={APP_VERSION} className={`cm-app ${collapsed ? "cm-is-collapsed" : ""} ${menuOpen ? "cm-menu-open" : ""}`} onClickCapture={followWorkspaceLink} onPointerDownCapture={e => { if (!e.target.closest(".cm-sidebar,.cm-mobile-bar")) dismissNavigation(); }} onFocusCapture={e => { if (!e.target.closest(".cm-sidebar,.cm-mobile-bar")) dismissNavigation(); }}>
      <div className="cm-mobile-bar"><a href={routeHash({ view: "tasks" })} onClick={e => { e.preventDefault(); changeView("tasks"); }}>codemap<span>.</span></a><button id="cm-menu-toggle" className="cm-button" aria-controls="cm-sidebar" aria-expanded={menuOpen} onClick={() => { setMenuOpen(true); setCollapsed(false); requestAnimationFrame(() => document.querySelector(".cm-nav button")?.focus()); }}><Icon name="menu" />Menu</button></div>
      {menuOpen && <div className="cm-nav-backdrop" onPointerDown={dismissNavigation} />}
      <aside className="cm-sidebar" id="cm-sidebar" aria-label="Workspace navigation">
        <div className="cm-brand-row">
        <button className="cm-brand cm-brand-trigger" id="cm-nav-trigger" title="Open navigation" aria-label="Open navigation" aria-expanded={!collapsed} aria-controls="cm-sidebar" onClick={() => { setCollapsed(false); setMenuOpen(true); }}>{collapsed ? <Icon name="menu" size={23} /> : <Logo />}<span>codemap<span className="cm-brand-dot">.</span></span></button>
        </div>
        <div className="cm-workspace">
          <span className="cm-workspace-icon">
            <Icon name="folder" size={19} />
          </span>
          <div>
            <span>WORKSPACE</span>
            <strong title={data.project}>{data.project}</strong>
          </div>
          <span className="cm-local-dot" title="Local workspace" />
        </div>
        <div className="cm-workspace-progress"><GenerationProgress compact progress={data.projectProgress} /></div>
        <div className="cm-nav-label">LIBRARY</div>
        <nav className="cm-nav" aria-label="Library views">
          {[
            {
              key: "tasks",
              label: "All tasks",
              icon: "grid",
              count: counts.total,
            },
            {
              key: "videos",
              label: "Videos",
              icon: "video",
              count: counts.videos,
            },
            {
              key: "attention",
              label: "Needs attention",
              icon: "alert",
              count: counts.attention,
            },
            { key: "map", label: "Explore map", icon: "nodes", count: null },
            { key: "learning", label: "Learning", icon: "learn", count: learnable },
            { key: "analysis", label: "Analysis", icon: "analysis", count: counts.total },
            { key: "compare", label: "Compare", icon: "compare", count: counts.total },
            { key: "settings", label: "Settings", icon: "settings", count: null },
          ].map((item) => (
            <button
              key={item.key}
              title={item.label}
              className={view === item.key ? "cm-nav-active" : ""}
              onClick={() => item.key === "map" ? openMap(routeTask?.mapUrl ? routeTask : tasks.find(t => t.mapUrl)) : item.key === "learning" ? openLearning(learningId || tasks.find(t => t.learning.available)?.id || tasks[0]?.id) : changeView(item.key)}
              aria-current={view === item.key ? "page" : undefined}
            >
              <Icon name={item.icon} size={18} />
              <span>{item.label}</span>
              {item.count !== null && <b>{item.count}</b>}
            </button>
          ))}
        </nav>
        <div className="cm-sidebar-bottom">
          <ConnectionStatus control={control} />
          <div className="cm-local-card">
            <span>
              <Icon name="shield" size={18} />
              Local by design
            </span>
            <p>No cloud connection. Snapshot files are not linked here.</p>
          </div>
          <button
            className="cm-theme-button"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          >
            <Icon name={theme === "dark" ? "sun" : "moon"} size={17} />
            <span>
              {theme === "dark" ? "Light appearance" : "Dark appearance"}
            </span>
            <span className="cm-theme-switch" aria-hidden="true">
              <i />
            </span>
          </button>
          <div className="cm-creator"><span className="cm-avatar" aria-hidden="true">S</span><div><strong>Made by Shashank</strong><span>Code. Explore. Understand.</span></div></div>
        </div>
      </aside>
      <main className="cm-main" id="main-content" tabIndex={-1}>
        <header className="cm-page-header">
          <div className="cm-page-navigation">
            <button className="cm-button cm-back-button" onClick={() => { setVideo(null); navigation.back(); }} disabled={!navigation.canBack} aria-label="Back">← Back</button>
            <nav className="cm-breadcrumb" aria-label="Breadcrumb">
              <a href={routeHash({ view: "tasks" })} onClick={e => { e.preventDefault(); changeView("tasks"); }}>Home</a>
              <Icon name="chevron" size={13} />
              <span title={data.project}>{data.project}</span>
              {view !== "tasks" && <><Icon name="chevron" size={13} /><a href={routeHash({ view })} onClick={e => { e.preventDefault(); changeView(view); }}>{viewTitle}</a></>}
              {routeTask && <><Icon name="chevron" size={13} /><button onClick={() => openDetails(routeTask.id)} title={routeTask.title}>Task {routeTask.id.slice(0, 8)}</button></>}
              {routeStep && <><Icon name="chevron" size={13} /><span aria-current="page">{routeStep.title}</span></>}
            </nav>
          </div>
          <div className="cm-heading-row">
            <div>
              <span className="cm-eyebrow">WORKSPACE / {data.project}</span>
              <h1>
                {viewTitle}
                {view !== "settings" && view !== "map" && <span className="cm-heading-count">
                  {view === "videos"
                    ? counts.videos
                    : view === "attention"
                      ? counts.attention
                      : view === "learning" ? learnable : counts.total}
                </span>}
              </h1>
              <p>
                {view === "map" ? "Follow the source and relationships without leaving your workspace." : view === "settings" ? "Control how future tasks create their maps and walkthroughs." : view === "compare" ? "Put two captured tasks side by side to understand their scope and recorded changes." : view === "learning" ? "Understand each change, one concept and one step at a time." : view === "analysis" ? "See your stack, your recorded activity, and what still needs attention." : view === "videos"
                  ? "Watch the story behind each captured change."
                  : view === "attention"
                    ? "Tasks that need a closer look before you move on."
                    : "Explore your changes. Understand the connections. Keep the context."}
              </p>
            </div>
            <div className="cm-header-actions">
              <button
                className="cm-icon-button cm-mobile-theme"
                onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
                aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
              >
                <Icon name={theme === "dark" ? "sun" : "moon"} size={18} />
              </button>
              <button
                className="cm-button cm-refresh-button"
                onClick={() => refresh(true)}
                disabled={refreshing}
              >
                <Icon
                  name="refresh"
                  size={16}
                  className={refreshing ? "cm-spinning" : ""}
                />
                {refreshing ? "Refreshing" : "Refresh"}
              </button>
            </div>
          </div>
        </header>
        {specialized && refreshMessage && <p className="cm-refresh-message" role="status">{refreshMessage}</p>}
        {!specialized && <Snapshot tasks={tasks} counts={counts} onMap={openMap} onLearn={openLearning} onAnalysis={openAnalysis} />}
        {!specialized && <div className="cm-workspace-status"><span>{counts.videos} {counts.videos === 1 ? "walkthrough" : "walkthroughs"} available</span><span>{counts.active} {counts.active === 1 ? "task" : "tasks"} in progress</span><button className="cm-text-link" onClick={() => changeView("attention")}>{counts.attention} {counts.attention === 1 ? "task needs" : "tasks need"} attention</button></div>}
        {data.problem && (
          <div className="cm-notice">
            <Icon name="alert" size={18} />
            <p>
              Some task records could not be read or the history limit was
              reached. Available outputs are shown below; missing records are
              not assumed complete.
            </p>
          </div>
        )}
        {!specialized && <section className="cm-library" aria-labelledby="cm-library-title">
          <div className="cm-section-heading">
            <div>
              <h2 id="cm-library-title">
                {view === "videos"
                  ? "Ready to watch"
                  : view === "attention"
                    ? "Tasks to review"
                    : "Recent activity"}
              </h2>
              <span>
                {filtered.length} {filtered.length === 1 ? "task" : "tasks"}
                {query || status !== "all"
                  ? " matching your filters"
                  : " in this view"}
              </span>
            </div>
            <span
              className={`cm-live-indicator ${counts.active ? "cm-is-live" : ""}`}
            >
              <i />
              {counts.active && /^https?:$/.test(location.protocol)
                ? "Updates every 5s"
                : "Local task history"}
            </span>
          </div>
          <div className="cm-toolbar">
            <div className="cm-search">
              <Icon name="search" size={18} />
              <input
                ref={search}
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search tasks, changes, or IDs…"
                aria-label="Search tasks"
              />
              <kbd>/</kbd>
            </div>
            <div className="cm-filter-controls">
              <label>
                <span className="cm-sr">Filter by status</span>
                <select
                  value={status}
                  onChange={(e) => setStatus(e.target.value)}
                >
                  <option value="all">All statuses</option>
                  <option value="complete">Complete</option>
                  <option value="active">In progress</option>
                  <option value="attention">Needs attention</option>
                  <option value="skipped">Skipped</option>
                </select>
              </label>
              <label>
                <span className="cm-sr">Sort tasks</span>
                <select value={sort} onChange={(e) => setSort(e.target.value)}>
                  <option value="newest">Newest first</option>
                  <option value="oldest">Oldest first</option>
                </select>
              </label>
            </div>
          </div>
          <div className="cm-refresh-status" role="status">
            {refreshMessage}
          </div>
          {filtered.length ? (
            <div className="cm-task-grid">
              {filtered.map((task) => (
                <TaskCard
                  key={task.id}
                  task={task}
                  onDetails={openDetails}
                  onVideo={openVideo}
                  onLearn={openLearning}
                  onSeen={markViewed}
                  isNew={!userState[task.id]?.viewed}
                  lessonsDone={completedLessons(task, userState).length}
                />
              ))}
            </div>
          ) : (
            <div className="cm-empty">
              <span className="cm-empty-icon">
                <Icon
                  name={
                    view === "videos"
                      ? "video"
                      : view === "attention"
                        ? "check"
                        : "search"
                  }
                  size={28}
                />
              </span>
              <h3>
                {query || status !== "all"
                  ? "No matching tasks"
                  : view === "videos"
                    ? "Your walkthroughs will appear here"
                    : view === "attention"
                      ? "You’re all caught up"
                      : "A clear view of your next change"}
              </h3>
              <p>
                {query || status !== "all"
                  ? "Try a different search or clear your filters to see more tasks."
                  : view === "videos"
                    ? "Completed tasks with a generated video will be collected in this library."
                    : view === "attention"
                      ? "No tasks currently need attention. Completed maps and videos are in All tasks."
                      : "After your agent captures a task, its map, review, and video will be collected here."}
              </p>
              {query || status !== "all" ? (
                <button className="cm-button" onClick={clearFilters}>
                  Clear filters
                </button>
              ) : view !== "tasks" ? (
                <button
                  className="cm-button"
                  onClick={() => changeView("tasks")}
                >
                  View all tasks
                  <Icon name="arrow" size={16} />
                </button>
              ) : null}
            </div>
          )}
        </section>}
        {view === "map" && <MapWorkspace task={routeTask} route={route} theme={theme} onNavigate={navigate} onInteraction={dismissNavigation} />}
        {view === "learning" && <Learning tasks={tasks} selectedId={learningId} selectedStep={route.step} selectedItem={route.item} onSelect={openLearning} onNavigate={openLearning} onAnalysis={openAnalysis} userState={userState} onComplete={markLesson} />}
        {view === "analysis" && <Analysis tasks={tasks} data={data} theme={theme} userState={userState} onLearn={openLearning} onDetails={openDetails} selectedId={route.task} onScope={openAnalysis} />}
        {view === "settings" && <Settings control={control} />}
        {view === "compare" && <Compare tasks={tasks} onLearn={openLearning} onDetails={openDetails} />}
        <footer className="cm-footer">
          <span>
            <Icon name="shield" size={15} />
            CMVL {APP_VERSION} &middot; Made by Shashank
          </span>
          <span>
            {data.generatedAt
              ? `History saved ${formatDate(data.generatedAt, true)}`
              : "Local workspace"}
            <span className="cm-footer-divider">·</span>Times shown in your
            timezone
          </span>
        </footer>
      </main>
      {detail && (
        <Detail
          task={detail}
          onClose={() => navigation.back()}
          onVideo={openVideo}
          control={control}
          onLearn={openLearning}
          onAnalysis={openAnalysis}
        />
      )}{" "}
      {video && <Video task={video} onClose={() => setVideo(null)} />}
    </div>
  );
}

const fallbackStyles = document.getElementById("codemap-fallback-styles");
class DashboardBoundary extends React.Component {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    delete document.documentElement.dataset.dashboard;
    if (fallbackStyles && !fallbackStyles.isConnected)
      document.head.append(fallbackStyles);
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}
const root = document.getElementById("codemap-dashboard");
if (root) {
  try {
    createRoot(root).render(
      <DashboardBoundary>
        <App />
      </DashboardBoundary>,
    );
  } catch {
    /* The Python-rendered history remains usable if the UI cannot mount. */
  }
}
