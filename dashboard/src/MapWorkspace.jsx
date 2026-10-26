import React, {useEffect,useRef,useState} from "react";

export default function MapWorkspace({task,route,theme,onNavigate,onInteraction}) {
  const frame = useRef(null);
  const hash = new URLSearchParams(); if(route.v) hash.set("v",route.v); if(route.n) hash.set("n",route.n);
  const src = task?.mapUrl ? task.mapUrl + "?embedded=1" + (hash.size ? "#"+hash.toString() : "") : null;
  const [frameSrc,setFrameSrc] = useState(src), fromMap = useRef(null), leaving = useRef(false);
  useEffect(() => { if(fromMap.current !== src) setFrameSrc(src); fromMap.current=null; },[src]);
  const syncTheme = () => frame.current?.contentWindow?.postMessage({type:"codemap:theme",theme},"*");
  useEffect(syncTheme,[theme]);
  useEffect(() => {
    const receive = event => {
      if(event.source !== frame.current?.contentWindow || !event.data || typeof event.data !== "object") return;
      if(event.data.type === "codemap:interaction") onInteraction();
      if(event.data.type === "codemap:ready") syncTheme();
      if(event.data.type === "codemap:map-state" && !leaving.current && new URLSearchParams(location.hash.slice(1)).get("view") === "map" && event.data.task === task?.id && /^v\d+$/.test(event.data.v || "") && typeof event.data.n === "string" && event.data.n.length <= 1200) {
        const params = new URLSearchParams({v:event.data.v}); if(event.data.n) params.set("n",event.data.n);
        fromMap.current=task.mapUrl+"?embedded=1#"+params.toString();
        onNavigate({view:"map",task:task.id,v:event.data.v,n:event.data.n || undefined},true);
      }
      if(event.data.type !== "codemap:navigate" || event.data.task !== task?.id) return;
      if(!["tasks","learning","analysis"].includes(event.data.view)) return;
      leaving.current=true;
      onNavigate({view:event.data.view,task:event.data.view === "tasks" ? undefined : task.id,step:event.data.view === "learning" ? "changes" : undefined,item:event.data.item});
    };
    addEventListener("message",receive);return () => removeEventListener("message",receive);
  },[task?.id,theme,onNavigate,onInteraction]);
  if(!src) return <div className="cm-empty"><h2>No map is available for this capture yet</h2><p>Choose a task with a generated map from All tasks.</p></div>;
  return <section className="cm-map-workspace" aria-label="Map workspace"><div className="cm-map-context"><strong>{task.title}</strong><span>Source captured with this task</span></div><iframe ref={frame} src={frameSrc} onLoad={syncTheme} title="Interactive captured code map" sandbox="allow-scripts allow-downloads" className="map-frame" /></section>;
}
