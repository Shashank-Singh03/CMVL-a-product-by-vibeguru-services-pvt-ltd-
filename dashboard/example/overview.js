(() => {
  const $ = id => document.getElementById(id);
  const root = document.documentElement, workspace = $('workspace'), frame = $('map-frame');
  const get = key => { try { return localStorage.getItem(key); } catch { return null; } };
  const save = (key,value) => { try { localStorage.setItem(key,value); } catch {} };
  function setTheme(theme) {
    root.dataset.theme = theme;
    $('appearance').setAttribute('aria-label',`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`);
    $('appearance').querySelector('span').textContent = theme === 'dark' ? 'Light appearance' : 'Dark appearance';
    frame.contentWindow?.postMessage({type:'codemap:theme',theme},'*');
  }
  setTheme(get('codemap-theme') === 'light' ? 'light' : get('codemap-theme') === 'dark' ? 'dark' : (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
  $('appearance').onclick = () => { setTheme(root.dataset.theme === 'dark' ? 'light' : 'dark'); save('codemap-theme',root.dataset.theme); };
  function navigation(open, focus = false) {
    workspace.classList.toggle('nav-open',open); workspace.classList.toggle('collapsed',!open);
    $('nav-backdrop').hidden = !open;
    for(const id of ['nav-trigger','mobile-menu']) $(id).setAttribute('aria-expanded',String(open));
    if(focus) (matchMedia('(max-width:760px)').matches ? $('mobile-menu') : $('nav-trigger')).focus();
    if(open && matchMedia('(max-width:760px)').matches) $('sidebar').querySelector('.nav a').focus();
  }
  $('nav-trigger').onclick = () => navigation(true);
  $('mobile-menu').onclick = () => navigation(true);
  document.addEventListener('pointerdown',e => { if(!e.target.closest('#sidebar,#mobile-menu')) navigation(false); });
  document.addEventListener('focusin',e => { if(workspace.classList.contains('nav-open') && !e.target.closest('#sidebar,#mobile-menu')) navigation(false); });
  $('sidebar').addEventListener('click',e => { if(e.target.closest('a')) navigation(false); });
  function readRoute() {
    const params = new URLSearchParams(location.hash.slice(1)), page = location.hash.slice(1).split('&')[0];
    return {page:['overview','guide','map','video','review'].includes(page) ? page : 'overview', v:/^v\d+$/.test(params.get('v') || '') ? params.get('v') : '', n:(params.get('n') || '').slice(0,1200)};
  }
  function mapHash(href) {
    if(!/^codemap\.html(?:#[^\s]*)?$/.test(href)) return null;
    const input = new URLSearchParams(href.split('#')[1] || ''), params = new URLSearchParams();
    if(/^v\d+$/.test(input.get('v') || '')) params.set('v',input.get('v'));
    if(input.get('n')) params.set('n',input.get('n').slice(0,1200));
    return '#map' + (params.size ? '&'+params.toString() : '');
  }
  document.querySelectorAll('a[href^="codemap.html"],a[href="codemap.mp4"]').forEach(a => { a.href = a.getAttribute('href') === 'codemap.mp4' ? '#video' : mapHash(a.getAttribute('href')) || '#map'; });
  let activePage = '', lastMap = null, leavingMap = false;
  document.addEventListener('click',e => { const a=e.target.closest('a');if(a?.getAttribute('href')==='#map' && lastMap){e.preventDefault();location.hash=lastMap;} });
  function route(focus = false) {
    const next = readRoute();
    for(const name of ['overview','guide','map','video','review']) $(name+'-page').hidden = next.page !== name;
    $('breadcrumb-page').textContent = {overview:'Overview',guide:'Reading guide',map:'Explore map',video:'Walkthrough',review:'Review flags'}[next.page];
    document.querySelectorAll('[data-page]').forEach(a => { if(a.dataset.page === next.page) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current'); });
    if(next.page === 'map') {
      leavingMap=false;
      lastMap=location.hash;
      const params = new URLSearchParams(); if(next.v) params.set('v',next.v); if(next.n) params.set('n',next.n);
      const src = 'codemap.html?embedded=1' + (params.size ? '#'+params.toString() : '');
      if(frame.getAttribute('src') !== src) frame.src = src;
    } else if(activePage === 'map') frame.removeAttribute('src');
    if(next.page !== 'video') $('walkthrough-video').pause();
    activePage = next.page; navigation(false);
    if(focus) { $('content').focus({preventScroll:true}); window.scrollTo(0,0); }
  }
  frame.addEventListener('load',() => frame.contentWindow?.postMessage({type:'codemap:theme',theme:root.dataset.theme},'*'));
  window.addEventListener('message',e => {
    if(e.source !== frame.contentWindow || !e.data || typeof e.data !== 'object') return;
    if(e.data.type === 'codemap:interaction') navigation(false);
    if(e.data.type === 'codemap:map-state' && !leavingMap && activePage === 'map' && readRoute().page === 'map' && /^v\d+$/.test(e.data.v || '') && typeof e.data.n === 'string' && e.data.n.length <= 1200) {
      const params=new URLSearchParams({v:e.data.v});if(e.data.n)params.set('n',e.data.n);
      lastMap='#map&'+params.toString();history.replaceState(history.state,'',lastMap);
    }
    if(e.data.type === 'codemap:navigate' && ['tasks','learning','analysis'].includes(e.data.view)) {
      leavingMap=true;
      location.hash = e.data.view === 'tasks' ? '#overview' : e.data.view === 'learning' ? '#guide' : '#review';
      route(true);
    }
    if(e.data.type === 'codemap:ready') frame.contentWindow.postMessage({type:'codemap:theme',theme:root.dataset.theme},'*');
  });
  window.addEventListener('hashchange',() => route(true)); route();
  $('area-search').addEventListener('input',() => {
    const query = $('area-search').value.trim().toLocaleLowerCase(); let count = 0;
    document.querySelectorAll('.area').forEach(a => { a.hidden = !a.textContent.toLocaleLowerCase().includes(query); if(!a.hidden) count++; });
    $('area-empty').hidden = count > 0; $('search-status').textContent = `${count} matching areas`;
  });
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let metric = 0, paused = reduced.matches, hover = false, pointerAction = null;
  function selectMetric(index) {
    metric = index;
    document.querySelectorAll('[data-metric]').forEach((el,i) => el.hidden = i !== index);
    document.querySelectorAll('[data-metric-to]').forEach((el,i) => el.setAttribute('aria-pressed',String(i === index)));
    $('snapshot').dataset.tone = ['mint','sky','violet','sky'][index];
  }
  function setPaused(value) { paused=value; $('metric-pause').textContent=value ? 'Play rotation' : 'Pause rotation'; }
  $('metric-pause').onpointerdown = () => pointerAction = !paused;
  $('metric-pause').onclick = () => { setPaused(pointerAction ?? !paused); pointerAction = null; };
  $('snapshot').addEventListener('pointerenter',() => hover=true);
  $('snapshot').addEventListener('pointerleave',() => hover=false);
  $('snapshot').addEventListener('focusin',() => setPaused(true));
  document.querySelectorAll('[data-metric-to]').forEach(el => el.onclick = () => { setPaused(true); selectMetric(Number(el.dataset.metricTo)); });
  reduced.addEventListener('change',e => { if(e.matches) setPaused(true); });
  setPaused(paused); selectMetric(0);
  setInterval(() => { if(!paused && !hover && !document.hidden && activePage === 'overview') selectMetric((metric+1)%4); },7000);
  document.addEventListener('keydown',e => {
    if(e.key === 'Escape' && workspace.classList.contains('nav-open')) navigation(false,true);
    if(e.key === '/' && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName) && !document.activeElement.isContentEditable && activePage === 'overview') { e.preventDefault(); $('area-search').focus(); }
  });
})();
