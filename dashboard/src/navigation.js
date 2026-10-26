import { useEffect, useRef, useState } from "react";

const VIEWS = new Set(["tasks", "videos", "attention", "learning", "analysis", "compare", "settings", "map"]);
const STEPS = new Set(["overview", "concepts", "changes", "connections", "review"]);
export function normalizedRoute(value = {}) {
  const route = { view: VIEWS.has(value.view) ? value.view : "tasks" };
  if (typeof value.task === "string" && /^[a-f0-9]{32}$/.test(value.task)) route.task = value.task;
  if (route.view === "learning" && STEPS.has(value.step)) route.step = value.step;
  if (route.view === "learning" && typeof value.item === "string" && /^change-[a-f0-9]{12}$/.test(value.item)) {
    route.item = value.item;
    route.step = "changes";
  }
  if (route.view === "map") {
    if (typeof value.v === "string" && /^v\d+$/.test(value.v)) route.v = value.v;
    if (typeof value.n === "string" && value.n.length <= 1200 && !/[\x00-\x1f]/.test(value.n)) route.n = value.n;
  }
  return route;
}
export function readRoute() {
  try {
    if (location.hash.length > 2048) return { view: "tasks" };
    return normalizedRoute(Object.fromEntries(new URLSearchParams(location.hash.slice(1))));
  } catch { return { view: "tasks" }; }
}
export function routeHash(value) {
  return "#" + new URLSearchParams(normalizedRoute(value)).toString();
}
export function useDashboardNavigation() {
  const [route, setRoute] = useState(readRoute);
  const [depth, setDepth] = useState(0);
  const scope = useRef(`codemap-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  useEffect(() => {
    const marker = { scope: scope.current, depth: 0 };
    history.replaceState({ ...history.state, codemapNavigation: marker }, "", routeHash(readRoute()));
    function restore() {
      const next = readRoute();
      setRoute(next);
      const current = history.state?.codemapNavigation;
      setDepth(current?.scope === scope.current && Number.isInteger(current.depth) ? current.depth : 0);
    }
    addEventListener("popstate", restore);
    addEventListener("hashchange", restore);
    return () => { removeEventListener("popstate", restore); removeEventListener("hashchange", restore); };
  }, []);
  function navigate(value, replace = false) {
    const next = normalizedRoute(value);
    const hash = routeHash(next);
    if (hash === location.hash) return;
    const marker = history.state?.codemapNavigation;
    const currentDepth = marker?.scope === scope.current ? marker.depth : 0;
    const nextDepth = replace ? currentDepth : currentDepth + 1;
    history[replace ? "replaceState" : "pushState"]({ ...history.state, codemapNavigation: { scope: scope.current, depth: nextDepth } }, "", hash);
    setRoute(next);
    setDepth(nextDepth);
  }
  function back() {
    const marker = history.state?.codemapNavigation;
    if (marker?.scope === scope.current && marker.depth > 0) history.back();
    else navigate({ view: "tasks" }, true);
  }
  return { route, navigate, back, canBack: depth > 0 || route.view !== "tasks" || !!route.task };
}
