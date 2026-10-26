import React, { useEffect, useRef, useState } from "react";

export default function Snapshot({ tasks, counts, onMap, onLearn, onAnalysis }) {
  const reduced = useRef(matchMedia("(prefers-reduced-motion: reduce)"));
  const [index,setIndex] = useState(0), [paused,setPaused] = useState(() => reduced.current.matches);
  const hover = useRef(false), pointerAction = useRef(null);
  const latest = tasks.find(t => t.mapUrl), learner = tasks.find(t => t.learning.available);
  const recorded = field => tasks.filter(t => Number.isInteger(t[field]));
  const total = field => recorded(field).length ? recorded(field).reduce((sum,t) => sum+t[field],0) : null;
  const slides = [
    {label:"Changed files",value:total("changedFiles"),tone:"mint",title:"Start with what changed.",body:"Files recorded across your saved captures. Open a map to see the change and its surrounding source context.",action:"Explore the latest map",run:() => onMap(latest),disabled:!latest},
    {label:"Mapped symbols",value:total("symbols"),tone:"sky",title:"Find the pieces behind the feature.",body:"Functions, classes and modules give the changes structure. Counts span captures, so a symbol can appear more than once.",action:"Explore code structure",run:() => onMap(latest),disabled:!latest},
    {label:"Connections",value:total("edges"),tone:"violet",title:"Follow the relationships.",body:"Recorded calls and dependencies connect the code. Parsed and inferred relationships are distinguished in each map.",action:"Learn the connections",run:() => onLearn(learner.id,"connections"),disabled:!learner},
    {label:"Maps available",value:counts.maps,tone:"mint",title:"Keep the context close.",body:"Your captured maps stay alongside lessons and walkthroughs. Review saved activity and see which generation stages remain.",action:"View project analysis",run:() => onAnalysis(),disabled:false},
  ];
  useEffect(() => {
    const stop = e => { if(e.matches) setPaused(true); };
    reduced.current.addEventListener("change",stop);
    return () => reduced.current.removeEventListener("change",stop);
  },[]);
  useEffect(() => {
    if(paused) return;
    const timer = setInterval(() => { if(!hover.current && !document.hidden) setIndex(i => (i+1)%4); },7000);
    return () => clearInterval(timer);
  },[paused]);
  const slide = slides[index];
  const contributors = index < 3 ? recorded(["changedFiles","symbols","edges"][index]).length : null;
  const formattedValue = slide.value === null ? "—" : new Intl.NumberFormat(undefined,{notation:slide.value >= 10000 ? "compact" : "standard",maximumFractionDigits:1}).format(slide.value);
  return <section className="snapshot glass" aria-label="Change snapshot" aria-roledescription="carousel" data-tone={slide.tone} onPointerEnter={() => hover.current=true} onPointerLeave={() => hover.current=false} onFocusCapture={() => setPaused(true)}>
    <div className="snapshot-top"><span className="cm-eyebrow">CHANGE SNAPSHOT</span><button className="cm-button cm-button-subtle" onPointerDown={() => pointerAction.current=!paused} onClick={() => { setPaused(pointerAction.current ?? !paused); pointerAction.current=null; }}>{paused ? "Play rotation" : "Pause rotation"}</button></div>
    <article className={`metric-slide tone-${slide.tone}`} role="group" aria-roledescription="slide" aria-label={`${index+1} of 4: ${slide.label}`}>
      <div className="metric-value"><strong aria-label={slide.value?.toLocaleString()}>{formattedValue}</strong><h2>{slide.label}</h2><span>{slide.value === null ? "Not recorded yet" : index < 3 ? `${contributors} of ${tasks.length} ${tasks.length === 1 ? "capture has" : "captures have"} recorded counts` : "Across saved task captures"}</span></div>
      <div className="metric-explanation"><h3>{slide.title}</h3><p>{slide.body}</p><button className="cm-button cm-button-primary" disabled={slide.disabled} onClick={slide.run}>{slide.action} <span aria-hidden="true">→</span></button></div>
    </article>
    <div className="metric-tabs" role="group" aria-label="Choose snapshot topic">{slides.map((s,i) => <button key={s.label} aria-pressed={index===i} onClick={() => {setPaused(true);setIndex(i);}}>{s.label}</button>)}</div>
  </section>;
}
