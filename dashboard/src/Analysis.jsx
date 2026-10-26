import React, { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, PieChart } from "echarts/charts";
import { GridComponent, TooltipComponent, LegendComponent, AriaComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { completedLessons } from "./learningData";
import { GenerationProgress } from "./Learning";

echarts.use([BarChart, PieChart, GridComponent, TooltipComponent, LegendComponent, AriaComponent, CanvasRenderer]);

function Chart({ title, description, option, theme, rows, columns = ["Category", "Files"] }) {
  const host = useRef(null);
  useEffect(() => {
    const node = host.current;
    if (!node) return;
    const chart = echarts.init(node, null, { renderer: "canvas" });
    const styles = getComputedStyle(document.documentElement);
    const muted = styles.getPropertyValue("--cm-muted").trim();
    const axis = value => ({ ...value, axisLabel: { color: muted, ...value.axisLabel }, nameTextStyle: { color: muted, ...value.nameTextStyle } });
    const readable = { ...option,
      ...(option.xAxis ? { xAxis: axis(option.xAxis) } : {}),
      ...(option.yAxis ? { yAxis: axis(option.yAxis) } : {}),
      series: option.series?.map(series => ({ ...series, label: { color: muted, ...series.label },
        emphasis: { ...series.emphasis, label: { color: muted, ...series.emphasis?.label } } })),
    };
    chart.setOption({ animation: !matchMedia("(prefers-reduced-motion: reduce)").matches,
      textStyle: { color: muted, fontFamily: "Manrope, system-ui, sans-serif", fontSize: 12 },
      aria: { enabled: true, label: { description } },
      color: theme === "light" ? ["#216c55", "#658673", "#9b885d", "#527987", "#a66d56", "#7f758d", "#71795b"] : ["#a0d9ba", "#82ada0", "#dcc18f", "#a0becd", "#dba58c", "#b9adc4", "#b2bc90"],
      tooltip: { trigger: "item", renderMode: "richText", confine: true }, ...readable });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(node);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [option, theme, description]);
  return <section className="cm-chart-card" data-chart={title.toLocaleLowerCase().replace(/[^a-z0-9]+/g,"-")}><div className="cm-chart-heading"><h2>{title}</h2><span>Apache ECharts</span></div><p>{description}</p>
    {rows.length ? <div ref={host} className="cm-chart" role="img" aria-label={`${title} chart`} /> : <div className="cm-chart-empty">No recorded data for this chart yet.</div>}
    <details className="cm-chart-table"><summary>View {title.toLocaleLowerCase()} data</summary><div><table><caption>{title}</caption><thead><tr>{columns.map(c => <th key={c} scope="col">{c}</th>)}</tr></thead><tbody>{rows.map((row,i) => <tr key={i}>{row.map((v,j) => j === 0 ? <th key={j} scope="row">{v}</th> : <td key={j}>{v}</td>)}</tr>)}</tbody></table></div></details>
  </section>;
}

function combine(tasks, field) {
  const totals = new Map();
  tasks.forEach(t => t[field].forEach(b => { const current = totals.get(b.name); totals.set(b.name, { name: b.name, label: b.label, count: (current?.count || 0) + b.count }); }));
  return [...totals.values()].sort((a,b) => b.count-a.count || a.label.localeCompare(b.label));
}

export default function Analysis({ tasks: allTasks, data, theme, userState, onLearn, onDetails, selectedId, onScope }) {
  const tasks = useMemo(() => selectedId ? allTasks.filter(t => t.id === selectedId) : allTasks, [allTasks, selectedId]);
  const languages = useMemo(() => combine(tasks, "languages"), [tasks]);
  const domains = useMemo(() => combine(tasks, "domains"), [tasks]);
  const eligible = tasks.filter(t => t.learning.available);
  const lessonsTotal = eligible.reduce((sum,t) => sum + t.learning.steps.length, 0);
  const lessonsDone = eligible.reduce((sum,t) => sum + completedLessons(t,userState).length, 0);
  const riskTotal = tasks.reduce((sum,t) => sum + t.risks.high+t.risks.medium+t.risks.low,0);
  const activity = [...tasks].filter(t => t.added !== null && t.removed !== null).sort((a,b) => (Date.parse(a.createdAt)||0)-(Date.parse(b.createdAt)||0)).slice(-12);
  const statuses = [
    { name: "Complete", count: tasks.filter(t => t.category === "complete").length },
    { name: "In progress", count: tasks.filter(t => t.category === "active").length },
    { name: "Needs attention", count: tasks.filter(t => t.category === "attention").length },
    { name: "Skipped", count: tasks.filter(t => t.category === "skipped").length },
  ];
  const languageOption = useMemo(() => ({ grid: { left: 12, right: 32, top: 15, bottom: 15, containLabel: true }, xAxis: { type: "value", minInterval: 1, axisLine: { show: false }, splitLine: { lineStyle: { color: theme === "light" ? "#e4e9e4" : "#37443a" } } }, yAxis: { type: "category", inverse: true, data: languages.slice(0,10).map(l => l.label), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { width: 105, overflow: "truncate" } }, series: [{ type: "bar", name: "Parsed files across captures", data: languages.slice(0,10).map(l => l.count), barMaxWidth: 23, itemStyle: { borderRadius: [0,5,5,0] }, label: { show: true, position: "right" } }] }), [languages, theme]);
  const domainOption = useMemo(() => ({ legend: { bottom: 4, type: "scroll", textStyle: { color: theme === "light" ? "#5a6981" : "#9caac0", fontSize: 10 } }, series: [{ type: "pie", radius: ["42%", "70%"], center: ["50%", "43%"], avoidLabelOverlap: true, label: { show: false }, emphasis: { label: { show: true, formatter: "{b}\n{c} files" } }, data: domains.map(d => ({ name: d.label, value: d.count })) }] }), [domains, theme]);
  const activityOption = useMemo(() => ({ grid: { left: 12, right: 12, top: 32, bottom: 12, containLabel: true }, legend: { top: 0, textStyle: { color: theme === "light" ? "#5a6981" : "#9caac0", fontSize: 10 } }, xAxis: { type: "category", data: activity.map(t => t.id.slice(0,8)), axisLabel: { rotate: activity.length > 6 ? 30 : 0, fontSize: 9 }, axisLine: { lineStyle: { color: "#697a94" } }, axisTick: { show: false } }, yAxis: { type: "value", minInterval: 1, splitLine: { lineStyle: { color: theme === "light" ? "#e4e9e4" : "#37443a" } } }, series: [{ name: "Added lines", type: "bar", data: activity.map(t => t.added), itemStyle: { color: theme === "light" ? "#26704f" : "#9bcca6", borderRadius: [3,3,0,0] }, barMaxWidth: 22 }, { name: "Removed lines", type: "bar", data: activity.map(t => t.removed), itemStyle: { color: theme === "light" ? "#a55353" : "#dca0a0", borderRadius: [3,3,0,0] }, barMaxWidth: 22 }] }), [tasks, theme]);
  const statusOption = useMemo(() => ({ grid: { left: 12, right: 20, top: 12, bottom: 12, containLabel: true }, xAxis: { type: "value", minInterval: 1, splitLine: { lineStyle: { color: theme === "light" ? "#e4e9e4" : "#37443a" } } }, yAxis: { type: "category", inverse: true, data: statuses.map(s => s.name), axisLine: { show: false }, axisTick: { show: false } }, series: [{ type: "bar", name: "Saved tasks", barMaxWidth: 25, data: statuses.map((s,i) => ({ value: s.count, itemStyle: { color: (theme === "light" ? ["#26704f","#416d78","#8b5a1c","#63716b"] : ["#9bcca6","#a3c2c8","#e3bd7f","#99a99d"])[i], borderRadius: [0,5,5,0] } })), label: { show: true, position: "right" } }] }), [tasks, theme]);
  return <section className="cm-analysis" aria-label="Project analysis" data-testid="analysis-view">
    <div className="cm-analysis-scope-picker"><label>Analysis scope<select aria-label="Analysis scope" value={selectedId || ""} onChange={e => onScope?.(e.target.value || undefined)}><option value="">All captured tasks</option>{allTasks.map(t => <option key={t.id} value={t.id}>{t.title}</option>)}</select></label>{selectedId && <button className="cm-button" onClick={() => onLearn(selectedId)}>Learn this change →</button>}</div>
    <div className="cm-analysis-intro"><div><span className="cm-eyebrow">SEE THE SHAPE OF YOUR CHANGES</span><h2>From activity to understanding</h2><p>Explore recorded task data, see what remains, and decide where to learn next.</p></div><span className="cm-analysis-engine">Powered by Apache ECharts</span></div>
    <div className="cm-analysis-metrics"><div><span>Captured tasks</span><strong>{tasks.length}</strong><small>available in this history</small></div><div><span>Languages & formats</span><strong>{languages.length}</strong><small>observed in saved analyses</small></div><div><span>Your learning checklist</span><strong>{lessonsDone}<small> / {lessonsTotal}</small></strong><small>lessons you marked understood</small></div><div><span>Recorded risk flags</span><strong>{riskTotal}</strong><small>rule findings, not confirmed defects</small></div></div>
    <div className="cm-analysis-scope"><b>Reading these charts</b><p>File counts are summed across saved task captures. A file can appear in more than one capture. These charts do not measure code correctness, productivity, or whole-project completion.</p></div>
    <div className="cm-chart-grid">
      <Chart title="Languages and formats" description="Parsed file counts across saved captures; the ten largest groups are charted." option={languageOption} theme={theme} rows={languages.map(l => [l.label,l.count])} />
      <Chart title="Areas of the system" description="Code, APIs, infrastructure, CI/CD, data and other areas found in the saved analyses." option={domainOption} theme={theme} rows={domains.map(d => [d.label,d.count])} />
      <Chart title="Change activity" description="Added and removed text lines for up to twelve recent captured tasks." option={activityOption} theme={theme} rows={activity.map(t => [t.id.slice(0,8),t.added,t.removed])} columns={["Task", "Added lines", "Removed lines"]} />
      <Chart title="Generation status" description="Saved task states, including unfinished or unavailable outputs that need attention." option={statusOption} theme={theme} rows={statuses.map(s => [s.name,s.count])} columns={["Status", "Tasks"]} />
    </div>
    <section className="cm-project-progress"><div><span className="cm-eyebrow">WHAT IS LEFT</span><h2>Generation and learning progress</h2><p>Generation tracks pipeline stages. Learning tracks the lessons you choose to mark understood.</p></div><GenerationProgress progress={selectedId ? tasks[0]?.progress : data.projectProgress} />
      <div className="cm-project-task-list">{tasks.map(t => { const done = completedLessons(t,userState).length; return <article key={t.id}><div><h3><button className="cm-text-link" onClick={() => onDetails?.(t.id)}>{t.title}</button></h3><span>{t.statusLabel}</span></div><div><GenerationProgress compact progress={t.progress} /><p>{t.category === "skipped" ? "No map or video needed." : !t.progress.available ? "Generation progress is not measured for this task state." : t.progress.remaining.length ? `Remaining: ${t.progress.remaining.join(" → ")}` : "No generation stages remain."}</p></div><div><span className="cm-task-learning-count">{t.learning.available ? `${done} / ${t.learning.steps.length} lessons completed` : "Lessons not ready"}</span><button className="cm-button cm-button-sm" onClick={() => onLearn(t.id)}>{t.learning.available ? "Learn changes" : "View learning status"}<span aria-hidden="true">→</span></button></div></article>; })}</div>
    </section>
    {(data.supportedLanguages.length > 0 || data.supportedDomains.length > 0) && <details className="cm-capabilities"><summary>Supported languages, system areas and parsing limits</summary><p>{data.parserNotes || "Structural extraction and inference quality vary by language."}</p><div><article><h3>Supported languages and formats</h3><div>{data.supportedLanguages.map(c => <span key={c.name}>{c.label}</span>)}</div></article><article><h3>System areas</h3><div>{data.supportedDomains.map(c => <span key={c.name}>{c.label}</span>)}</div></article></div></details>}
  </section>;
}
