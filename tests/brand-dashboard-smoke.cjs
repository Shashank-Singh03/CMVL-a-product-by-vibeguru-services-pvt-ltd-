/* Brand shell acceptance on file://, static HTTP and the actual scoped server. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),http=require('node:http');
const {spawn,spawnSync}=require('node:child_process'),{pathToFileURL}=require('node:url'),{ chromium, browserLaunchOptions } = require('./browser-test-support.cjs');
const repo=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'codemap-brand-'));
const evidence=path.resolve(process.env.CODEMAP_BRAND_EVIDENCE||'benchmarks/brand/2026-10-26/browser'),shots=[];
const report={checks:[],pageErrors:[],consoleErrors:[],externalRequests:[]};let browser,staticServer,api,activePage;
const make=String.raw`
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'skills/codemap/scripts'))
from codemap_lib import demo,tasks,pipeline,history
root,_=demo.create(Path(sys.argv[2])/'notes-study')
start=tasks.start(root,'brand','one');demo.apply_agent_changes(root);state=tasks.freeze(root,start['task_id'])
pipeline.analyze(state['work_root'],out=state['out_dir'],isolated=True,isolated_files=sorted(state['after']['files']),project_name='notes-study',tier='video',structural_review=True,include_tests=True,log=lambda _:None)
tasks.update(root,start['task_id'],status='awaiting_review',map_ready=True,reviewed_files=0)
print(json.dumps({'root':str(root),'page':str(history.write_history(root))}))
`;
async function shot(page,name){
 await page.evaluate(()=>document.fonts.ready);
 // A map is its own scrollable viewport. Capture the actual screen rather than
 // resizing that viewport during a full-document capture before pointer checks.
 shots.push([name+'.png',await page.screenshot({fullPage:!name.endsWith('-map')})]);
}
async function fits(page,label){assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),label+' overflow');}
async function nav(page,label){if(await page.locator('#cm-menu-toggle').isVisible())await page.locator('#cm-menu-toggle').click();await page.locator(`.cm-nav button[title="${label}"]`).click();}
async function main(){
 const made=spawnSync('python',['-c',make,repo,temp],{encoding:'utf8'});assert.equal(made.status,0,made.stderr);const built=JSON.parse(made.stdout);
 api=spawn('python',['-u','-c',"import sys;sys.path.insert(0,sys.argv[1]);from codemap_lib.server import create_server;s=create_server(sys.argv[2]);print(s.origin,flush=True);s.serve_forever()",path.join(repo,'skills/codemap/scripts'),built.root],{stdio:['pipe','pipe','pipe'],windowsHide:true});
 const origin=await new Promise((resolve,reject)=>{let out='';const timer=setTimeout(()=>reject(Error('Server startup timeout')),15000);api.stdout.on('data',d=>{out+=d; if(out.includes('\n')){clearTimeout(timer);resolve(out.split('\n')[0].trim());}});api.on('error',reject);});
 const example=path.join(repo,'examples/demo-output');
 staticServer=http.createServer((req,res)=>{if(req.url==='/favicon.ico'){res.writeHead(204);return res.end();}const file=path.resolve(example,'.'+decodeURIComponent(req.url.split('?')[0]));if(!file.startsWith(example+path.sep)||!fs.existsSync(file)){res.writeHead(404);return res.end();}res.setHeader('Content-Type',file.endsWith('.html')?'text/html; charset=utf-8':file.endsWith('.mp4')?'video/mp4':'text/plain');fs.createReadStream(file).pipe(res);});
 await new Promise(r=>staticServer.listen(0,'127.0.0.1',r));const staticOrigin=`http://127.0.0.1:${staticServer.address().port}`;
 browser=await chromium.launch({...browserLaunchOptions,headless:true});
 for(const kind of ['example-file','example-http','react-file','react-api'].filter(k=>!process.env.CODEMAP_BRAND_FILTER||k===process.env.CODEMAP_BRAND_FILTER)){
  const isExample=kind.startsWith('example'),isFile=kind.endsWith('file');
  const context=await browser.newContext({viewport:{width:1440,height:1000},colorScheme:'dark',reducedMotion:'reduce'}),page=await context.newPage();activePage=page;
  page.on('pageerror',e=>report.pageErrors.push(kind+': '+e));
  page.on('console',m=>{if(m.type()==='error')report.consoleErrors.push(kind+': '+m.text());});
  page.on('request',r=>{if(/^https?:/.test(r.url())&&!r.url().startsWith(origin)&&!r.url().startsWith(staticOrigin))report.externalRequests.push(r.url());});
  const entry=isExample?(isFile?pathToFileURL(path.join(example,'index.html')).href:staticOrigin+'/index.html'):(isFile?pathToFileURL(built.page).href:origin+'/');
  await context.addInitScript(() => {
    window.__navTrace=[];
    window.addEventListener('message',e=>{if(e.data?.type?.startsWith('codemap:'))window.__navTrace.push({kind:'message',data:e.data,hash:location.hash});});
    window.addEventListener('hashchange',()=>window.__navTrace.push({kind:'hashchange',hash:location.hash}));
    document.addEventListener('click',e=>{if(e.target.closest?.('#dashboardHome'))window.__navTrace.push({kind:'home',prevented:e.defaultPrevented,href:e.target.closest('#dashboardHome').getAttribute('href')});});
    document.addEventListener('pointerdown',e=>window.__navTrace.push({kind:'pointer',target:e.target.tagName+'#'+e.target.id,x:e.clientX,y:e.clientY,scrollY:window.scrollY}));
  });
  await page.goto(entry);await page.locator(isExample?'#snapshot':'.cm-app .snapshot').waitFor();
  const trigger=page.locator(isExample?'#nav-trigger':'#cm-nav-trigger'),rail=page.locator(isExample?'#sidebar':'.cm-sidebar'),main=page.locator(isExample?'#content':'.cm-main');
  assert.equal(await page.getByRole('button',{name:'Collapse sidebar',exact:true}).count(),0);
  const left=await main.evaluate(e=>e.getBoundingClientRect().left);
  await trigger.click();assert.equal(await trigger.getAttribute('aria-expanded'),'true');
  await page.locator(isExample?'.project':'.cm-workspace').click();assert.equal(await trigger.getAttribute('aria-expanded'),'true');
  await page.getByRole('heading',{level:1}).click();assert.equal(await trigger.getAttribute('aria-expanded'),'false');
  assert.equal(await main.evaluate(e=>e.getBoundingClientRect().left),left,'Navigation overlays rather than shifting content');
  await trigger.click();await page.keyboard.press('Escape');assert.equal(await trigger.getAttribute('aria-expanded'),'false');assert.ok(await trigger.evaluate(e=>e===document.activeElement));
  assert.equal(await page.getByRole('button',{name:'Play rotation',exact:true}).count(),1,'Reduced motion disables automatic rotation');
  await page.getByRole('button',{name:'Mapped symbols',exact:true}).click();assert.equal(await page.locator('.metric-slide:not([hidden]) h2').textContent(),'Mapped symbols');
  for(const width of [3221,1920,1440,1024,768,760,390,360]){
   await page.setViewportSize({width,height:width>1000?1100:844});await fits(page,kind+' '+width);
   if(width===3221){assert.ok(await main.evaluate(e=>e.getBoundingClientRect().width/innerWidth)>.9,'Wide workspace must use the canvas');}
   if(width===360||width===390){
    const heights=[];
    for(const label of ['Changed files','Mapped symbols','Connections',isExample?'Languages & formats':'Maps available']){await page.getByRole('button',{name:label,exact:true}).click();heights.push(await page.locator('.snapshot').evaluate(e=>e.getBoundingClientRect().height));}
    assert.ok(Math.max(...heights)-Math.min(...heights)<1,'Rotating topics must not shift phone layout');
    await page.getByRole('button',{name:'Mapped symbols',exact:true}).click();
   }
   if(width===3221||width===1440||width===390)await shot(page,kind+'-'+width);
  }
  const menu=page.locator(isExample?'#mobile-menu':'#cm-menu-toggle');await menu.click();
  await page.locator(isExample?'#nav-backdrop':'.cm-nav-backdrop').click({position:{x:350,y:250}});assert.equal(await menu.getAttribute('aria-expanded'),'false');
  await menu.click();assert.ok(await page.locator(isExample?'.nav a:first-child':'.cm-nav button:first-child').evaluate(e=>e===document.activeElement));
  await main.focus();assert.equal(await menu.getAttribute('aria-expanded'),'false','Moving focus out of the drawer dismisses it');
  await page.setViewportSize({width:1440,height:1000});
  if(isExample)await page.getByRole('navigation',{name:'Main navigation'}).getByRole('link',{name:'Explore map',exact:true}).click();else await nav(page,'Explore map');
  const frame=page.frameLocator(isExample?'#map-frame':'.map-frame');await frame.locator('#graph .node').first().waitFor();
  assert.equal(new URL(page.url()).pathname,new URL(entry).pathname,'Map stays in parent workspace');
  await trigger.click();await frame.locator('#canvas').click({position:{x:450,y:20}});assert.equal(await trigger.getAttribute('aria-expanded'),'false','Iframe interaction dismisses navigation');
  await frame.locator('#findPanel>summary').click();await frame.locator('#findQuery').fill('delete');assert.ok(await frame.locator('#findResults .result').count()>0);await frame.locator('#findResults .result').first().click();
  await shot(page,kind+'-map');
  await page.waitForURL(/(?:[&#])n=/);
  const selectedMap=page.url();
  // Ignore navigation messages from any window other than our pinned map frame.
  const current=page.url();await page.evaluate(()=>postMessage({type:'codemap:navigate',view:'settings',task:'a'.repeat(32)},'*'));assert.equal(page.url(),current);
  await frame.locator('#dashboardHome').click();await page.locator('.snapshot').waitFor();
  if(isExample)await page.getByRole('navigation',{name:'Main navigation'}).getByRole('link',{name:'Explore map',exact:true}).click();else await page.locator('.cm-task-actions').getByRole('link',{name:'Open map',exact:false}).first().click();
  await frame.locator('#graph .node').first().waitFor();assert.equal(page.url(),selectedMap,'Reopening the map retains the selected item');
  await frame.locator('body').evaluate(()=>{
    const data=JSON.parse(document.getElementById('codemap-data').textContent);
    parent.postMessage({type:'codemap:navigate',view:'tasks',task:data.artifacts?.task_id},'*');
    parent.postMessage({type:'codemap:map-state',v:'v0',n:'',task:data.artifacts?.task_id},'*');
  });
  await page.locator('.snapshot').waitFor();assert.ok(isExample ? !page.url().includes('#map') : !page.url().includes('view=map'),'Late map state cannot override leaving the map');
  if(isExample){
   await page.getByRole('navigation',{name:'Main navigation'}).getByRole('link',{name:'Watch walkthrough',exact:true}).click();await page.locator('#video-page:visible').waitFor();
   const video=page.locator('#walkthrough-video');await video.evaluate(async v=>{v.muted=true;await v.play();});assert.equal(await video.evaluate(v=>v.paused),false);
   await page.getByRole('link',{name:'Back to overview',exact:true}).click();assert.equal(await video.evaluate(v=>v.paused),true);assert.equal(new URL(page.url()).pathname,new URL(entry).pathname);
   await page.goBack();await page.locator('#video-page:visible').waitFor();await shot(page,kind+'-video');
   await page.goto(entry+'#overview');await page.locator('#area-search').fill('Java');assert.equal(await page.locator('.area:visible').count(),1);await page.locator('.area:visible').click();await frame.locator('#graph .node').first().waitFor();assert.match(page.url(),/#map&v=v1/);
  }else{
   await nav(page,'Learning');await page.locator('[data-testid="learning-view"]').waitFor();await page.getByRole('link',{name:'Open full map',exact:false}).click();await frame.locator('#graph .node').first().waitFor();
   await frame.locator('#mapLearning').click();await page.locator('[data-testid="learning-view"]').waitFor();await nav(page,'Analysis');await page.locator('.cm-chart canvas').first().waitFor();await shot(page,kind+'-analysis');
  }
  await page.goto(entry);await page.locator('.snapshot').waitFor();await trigger.click();await page.getByRole('button',{name:'Switch to light theme',exact:true}).first().click();assert.equal(await page.locator('html').getAttribute('data-theme'),'light');await page.getByRole('heading',{level:1}).click();await shot(page,kind+'-light');
  report.checks.push({kind,fluidWidths:true,stablePhoneSnapshot:true,outsideDismiss:true,insideStaysOpen:true,escapeFocus:true,focusDismiss:true,mapInShell:true,frameDismiss:true,mapSelectionRestored:true,lateStateRace:true,sourceValidatedMessages:true,theme:true});await context.close();
 }
 // Auto rotation advances, hover suspends, and explicit selection stops it.
 const context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:'no-preference'}),page=await context.newPage();await page.goto(staticOrigin+'/index.html');
 await page.mouse.move(5,5);await page.waitForFunction(()=>document.querySelector('[data-metric="1"]').hidden===false,{},{timeout:11000});
 await page.locator('#snapshot').hover();const selected=await page.locator('[data-metric]:not([hidden])').getAttribute('data-metric');await page.waitForTimeout(7300);assert.equal(await page.locator('[data-metric]:not([hidden])').getAttribute('data-metric'),selected);
 await page.getByRole('button',{name:'Connections',exact:true}).click();assert.equal(await page.locator('#metric-pause').textContent(),'Play rotation');await context.close();report.rotation={advances:true,hoverPauses:true,manualStops:true,reducedMotion:true};
 assert.deepEqual(report.pageErrors,[]);assert.deepEqual(report.externalRequests,[]);
 assert.ok(!report.consoleErrors.some(e=>/Content Security Policy|Refused to frame|Blocked script|font/i.test(e)),JSON.stringify(report.consoleErrors));
}
main().then(()=>console.log(JSON.stringify(report,null,2))).catch(async e=>{console.error(e);process.exitCode=1;if(activePage&&!activePage.isClosed()){report.diagnostic={url:activePage.url(),frames:await Promise.all(activePage.frames().map(async f=>({url:f.url(),trace:await f.evaluate(()=>window.__navTrace)})))};await shot(activePage,'failure');console.log(JSON.stringify(report.diagnostic));}}).finally(async()=>{
 if(browser)await browser.close();if(staticServer)await new Promise(r=>staticServer.close(r));if(api)api.kill();
 fs.mkdirSync(evidence,{recursive:true});for(const[name,buffer]of shots)fs.writeFileSync(path.join(evidence,name),buffer);fs.writeFileSync(path.join(evidence,'checks.json'),JSON.stringify(report,null,2));
 const resolved=fs.realpathSync(temp);assert.equal(path.dirname(resolved),fs.realpathSync(os.tmpdir()));assert.match(path.basename(resolved),/^codemap-brand-/);fs.rmSync(resolved,{recursive:true,force:true});
});
