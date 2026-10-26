/* Real local-server acceptance with disposable source captures only.
 * Uses the shared browser discovery helper.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { pathToFileURL } = require('node:url');
const { spawn, spawnSync } = require('node:child_process');
const { chromium, browserLaunchOptions } = require('./browser-test-support.cjs');

const repository = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'codemap-controls-browser-'));
const fixture = path.join(temporary, 'project');
const evidence = process.env.CODEMAP_CONTROLS_EVIDENCE ? path.resolve(process.env.CODEMAP_CONTROLS_EVIDENCE) : null;
const helper = String.raw`
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'skills' / 'codemap' / 'scripts'))
from codemap_lib import automatic, history, pipeline, tasks
from codemap_lib.server import create_server
root=Path(sys.argv[2]); root.mkdir()
source=root/'app.py'
source.write_text("raise RuntimeError('Never execute fixture source')\n\ndef answer():\n    return 1\n",encoding='utf-8')
automatic.configure(root,'on',video=False)
first=tasks.start(root,'retry-session','retry-prompt')
source.write_text(source.read_text().replace('return 1','return 2'),encoding='utf-8')
first=tasks.freeze(root,first['task_id'])
first=tasks.update(root,first['task_id'],status='analysis_failed',stage='analysis',settings=dict(automatic.DEFAULTS,video=False))
second=tasks.start(root,'cancel-session','cancel-prompt')
source.write_text(source.read_text().replace('return 2','return 3'),encoding='utf-8')
second=tasks.freeze(root,second['task_id'])
pipeline.analyze(second['work_root'],out=second['out_dir'],isolated=True,isolated_files=sorted(second['after']['files']),
                 project_name=root.name,tier='video',structural_review=True,log=lambda *_:None)
second=tasks.update(root,second['task_id'],status='awaiting_review',map_ready=True,settings=dict(automatic.DEFAULTS,video=False))
history.write_history(root)
server=create_server(root,port=0)
print(json.dumps({'port':server.server_address[1],'retry':first['task_id'],'cancel':second['task_id']}),flush=True)
try: server.serve_forever()
finally: server.server_close()
`;

async function serverInfo(child) {
  return new Promise((resolve,reject) => {
    let stdout='',stderr='';
    const timeout=setTimeout(() => reject(new Error('Local test server did not start: '+stderr)),20000);
    child.stderr.on('data',chunk => { stderr+=chunk; });
    child.stdout.on('data',chunk => {
      stdout+=chunk;
      const end=stdout.indexOf('\n');
      if(end>=0){clearTimeout(timeout);try{resolve(JSON.parse(stdout.slice(0,end)));}catch(error){reject(error);}}
    });
    child.once('error',error => {clearTimeout(timeout);reject(error);});
    child.once('exit',code => {clearTimeout(timeout);reject(new Error(`Local server exited ${code}: ${stderr}`));});
  });
}

async function main() {
  const child=spawn(process.env.PYTHON||'python',['-u','-c',helper,repository,fixture],{stdio:['ignore','pipe','pipe']});
  let browser, page, readonlyServer;
  const captures=[];
  const findings={fixturesOnly:true,sourceApplicationsExecuted:false,checks:{},pageErrors:[],externalRequests:[]};
  try {
    const info=await serverInfo(child);
    const origin=`http://127.0.0.1:${info.port}`;
    const readonlyApi=[];
    readonlyServer=http.createServer((request,response)=>{
      if(request.url.startsWith('/api/'))readonlyApi.push(request.url);
      if(request.url==='/favicon.ico'){response.writeHead(204);response.end();return;}
      response.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'});
      response.end(fs.readFileSync(path.join(fixture,'.codemap','index.html')));
    });
    await new Promise(resolve=>readonlyServer.listen(0,'127.0.0.1',resolve));
    const readonlyOrigin=`http://127.0.0.1:${readonlyServer.address().port}`;
    const allowedOrigins=new Set([origin,readonlyOrigin]);
    browser=await chromium.launch({...browserLaunchOptions,headless:true});
    const context=await browser.newContext({viewport:{width:1440,height:1000},colorScheme:'dark'});
    await context.addInitScript(()=>{
      window.__codemapCspViolations=[];
      addEventListener('securitypolicyviolation',event=>window.__codemapCspViolations.push({directive:event.effectiveDirective,blocked:event.blockedURI}));
    });
    page=await context.newPage();
    findings.responses=[];
    findings.navigations=[];
    page.on('framenavigated',frame=>{if(frame===page.mainFrame())findings.navigations.push(frame.url());});
    page.on('response',response=>{if(response.url().startsWith(origin+'/api/'))findings.responses.push({path:new URL(response.url()).pathname,status:response.status()});});
    const api=[];
    const protocol=await context.newCDPSession(page);
    await protocol.send('Page.enable');
    findings.scheduledNavigations=[];
    protocol.on('Page.frameScheduledNavigation',event=>findings.scheduledNavigations.push({reason:event.reason,delay:event.delay}));
    page.on('pageerror',error=>findings.pageErrors.push(error.message));
    page.on('request',request=>{
      const url=request.url();
      if(/^https?:/.test(url)&&!allowedOrigins.has(new URL(url).origin))findings.externalRequests.push(url);
      if(url.startsWith(origin+'/api/'))api.push(request.method()+' '+new URL(url).pathname);
    });

    for(const url of [pathToFileURL(path.join(fixture,'.codemap','index.html')).href,readonlyOrigin+'/']){
      await page.goto(url);
      await page.locator('html[data-dashboard="ready"]').waitFor();
      await page.locator('.cm-nav').getByRole('button',{name:'Settings',exact:true}).click();
      await page.getByRole('heading',{name:'Enable local project controls'}).waitFor();
      assert.equal(await page.getByRole('button',{name:'Save settings',exact:true}).count(),0);
      const navigationCount=findings.navigations.length;
      await page.waitForTimeout(6000);
      assert.equal(findings.navigations.length,navigationCount,'Read-only active pages must not reset the selected view');
      assert.equal(await page.locator('[data-testid="settings-view"]').count(),1);
    }
    assert.equal(api.length,0,'Offline pages must not probe a local API');
    assert.deepEqual(readonlyApi,[],'Ordinary static HTTP pages must not probe a local API');
    findings.checks.offlineSettings=true;
    findings.checks.readonlyViewSurvivesRefreshInterval=true;

    await page.goto(origin+'/');
    await page.locator('html[data-dashboard="ready"]').waitFor();
    await page.locator('#cm-nav-trigger').click();
    await page.locator('.cm-sidebar').getByText('Local controls connected',{exact:true}).waitFor();
    await page.locator('.cm-nav').getByRole('button',{name:'Settings',exact:true}).click();
    const video=page.getByRole('checkbox',{name:/Generate videos/});
    assert.equal(await video.isChecked(),false);
    await video.check();
    await page.getByLabel('Target video length').fill('1.5');
    await page.getByRole('button',{name:'Save settings',exact:true}).click();
    await page.locator('.cm-control-notice').waitFor();
    assert.match(await page.locator('.cm-control-notice').innerText(),/sav|updat|setting/i);
    const config=JSON.parse(fs.readFileSync(path.join(fixture,'.codemap','config.json'),'utf8'));
    assert.equal(config.video,true);
    assert.equal(config.minutes,1.5);
    const pinned=JSON.parse(fs.readFileSync(path.join(fixture,'.codemap','tasks',info.cancel,'state.json'),'utf8'));
    assert.equal(pinned.settings.video,false,'Saved settings must not rewrite captured task settings');
    findings.checks.settingsSave=true;
    findings.checks.pinnedSettingsPreserved=true;
    await page.reload();
    await page.locator('#cm-nav-trigger').click();
    await page.locator('.cm-sidebar').getByText('Local controls connected',{exact:true}).waitFor();
    await page.locator('.cm-nav').getByRole('button',{name:'Settings',exact:true}).click();
    assert.equal(await page.getByRole('checkbox',{name:/Generate videos/}).isChecked(),true);
    assert.equal(await page.getByLabel('Target video length').inputValue(),'1.5');
    const before= findings.navigations.length;
    await page.waitForTimeout(5500);
    assert.equal(findings.navigations.length,before,'Active tasks must not reload the local controls page');
    findings.checks.activeTasksDoNotReloadPage=true;
    captures.push(['settings-desktop.png',await page.screenshot({fullPage:true})]);

    await page.locator('.cm-nav').getByRole('button',{name:/^All tasks/}).click();
    const retryCard=page.locator('.cm-task').filter({hasText:info.retry.slice(0,8).toUpperCase()});
    await retryCard.getByRole('button',{name:/^Details for/}).click();
    const detail=page.locator('dialog.cm-detail-dialog');
    await detail.getByRole('button',{name:'Retry analysis',exact:true}).click();
    await page.waitForFunction(id=>{
      const input=document.getElementById('cm-full-task-id');
      const dialog=input?.closest('dialog');
      return input?.value===id && /Building map|Map queued|Review pending/.test(dialog.innerText);
    },info.retry);
    // The real bounded worker parses the frozen source; it never executes it.
    const until=Date.now()+20000;
    let retried;
    do {
      retried=JSON.parse(fs.readFileSync(path.join(fixture,'.codemap','tasks',info.retry,'state.json'),'utf8'));
      if(retried.status==='awaiting_review')break;
      await new Promise(resolve=>setTimeout(resolve,150));
    }while(Date.now()<until);
    assert.equal(retried.status,'awaiting_review');
    assert.ok(fs.existsSync(path.join(retried.out_dir,'codemap.html')));
    findings.checks.realAnalysisRetry=true;
    await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await page.getByText('History is up to date.',{exact:true}).waitFor();

    // Exercise existing rich views under the real server's CSP as well as its
    // controls, rather than relying on the separate static-page acceptance.
    await page.locator('.cm-nav').getByRole('button',{name:/^Analysis/}).click();
    await page.locator('[data-testid="analysis-view"]').waitFor();
    await page.waitForFunction(()=>{
      const charts=[...document.querySelectorAll('[data-testid="analysis-view"] .cm-chart')];
      return charts.length===4&&charts.every(chart=>{
        const canvas=chart.querySelector('canvas');
        return canvas&&canvas.width>0&&canvas.height>0;
      });
    });
    assert.equal(await page.locator('[data-testid="analysis-view"] .cm-chart-card').count(),4);
    for(const table of await page.locator('[data-testid="analysis-view"] table').all())
      assert.ok(await table.locator('tbody tr').count()>0,'Every chart must expose recorded data');
    findings.checks.localServerEchartsCsp=true;
    captures.push(['analysis-local-server.png',await page.screenshot({fullPage:true})]);

    await page.locator('.cm-nav').getByRole('button',{name:/^Learning/}).click();
    const learning=page.locator('[data-testid="learning-view"]');
    await learning.waitFor();
    assert.equal(await learning.locator('[data-lesson-step]').count(),5);
    await learning.locator('[data-lesson-step="changes"]').click();
    assert.ok(await learning.locator('[data-line-kind="added"]').count()>0);
    assert.ok(await learning.locator('[data-line-kind="removed"]').count()>0);
    findings.checks.localServerLearning=true;
    captures.push(['learning-local-server.png',await page.screenshot({fullPage:true})]);

    await page.locator('.cm-nav').getByRole('button',{name:/^Compare/}).click();
    const comparison=page.locator('[data-testid="compare-view"]');
    await comparison.waitFor();
    assert.notEqual(await comparison.getByRole('combobox',{name:'Capture A',exact:true}).inputValue(),
      await comparison.getByRole('combobox',{name:'Capture B',exact:true}).inputValue());
    findings.checks.localServerComparison=true;
    assert.deepEqual(await page.evaluate(()=>window.__codemapCspViolations),[]);
    findings.checks.noCspViolations=true;
    await page.locator('.cm-nav').getByRole('button',{name:/^All tasks/}).click();

    const cancelCard=page.locator('.cm-task').filter({hasText:info.cancel.slice(0,8).toUpperCase()});
    await cancelCard.getByRole('button',{name:/^Details for/}).click();
    await detail.getByRole('button',{name:'Cancel generation',exact:true}).click();
    assert.equal(JSON.parse(fs.readFileSync(path.join(fixture,'.codemap','tasks',info.cancel,'state.json'),'utf8')).status,'awaiting_review','Opening confirmation must not cancel');
    await detail.getByRole('button',{name:'Keep running',exact:true}).click();
    assert.equal(await detail.getByRole('button',{name:'Confirm cancel',exact:true}).count(),0);
    await detail.getByRole('button',{name:'Cancel generation',exact:true}).click();
    await detail.getByRole('button',{name:'Confirm cancel',exact:true}).click();
    await detail.getByText('Cancelled',{exact:true}).waitFor();
    assert.equal(JSON.parse(fs.readFileSync(path.join(fixture,'.codemap','tasks',info.cancel,'state.json'),'utf8')).status,'cancelled');
    assert.ok(fs.existsSync(path.join(fixture,'.codemap','tasks',info.cancel,'output','codemap.html')));
    findings.checks.confirmedCancellation=true;
    findings.checks.completedMapPreserved=true;
    captures.push(['cancelled-task-desktop.png',await page.screenshot({fullPage:true})]);
    await page.keyboard.press('Escape');

    // Once all generation is idle, the connected dashboard must still discover
    // a new capture without a manual Refresh or a whole-page navigation.
    await retryCard.getByRole('button',{name:/^Details for/}).click();
    await detail.getByRole('button',{name:'Cancel generation',exact:true}).click();
    await detail.getByRole('button',{name:'Confirm cancel',exact:true}).click();
    await detail.getByText('Cancelled',{exact:true}).waitFor();
    await page.keyboard.press('Escape');
    // Closing detail uses browser history; wait for that asynchronous navigation
    // before measuring whether idle polling navigates the page.
    await detail.waitFor({state:'hidden'});
    const beforeIdle=findings.navigations.length;
    const newCapture=spawnSync(process.env.PYTHON||'python',['-c',
      "import sys;sys.path.insert(0,sys.argv[1]);from codemap_lib import tasks;print(tasks.start(sys.argv[2],'new-session','new-prompt')['task_id'])",
      path.join(repository,'skills','codemap','scripts'),fixture],{encoding:'utf8'});
    assert.equal(newCapture.status,0,newCapture.stderr);
    const newId=newCapture.stdout.trim();
    await page.locator('.cm-task').filter({hasText:newId.slice(0,8).toUpperCase()}).waitFor({timeout:12000});
    assert.equal(findings.navigations.length,beforeIdle);
    findings.checks.newCaptureAppearsAfterIdle=true;

    for(const width of [360,390]){
      await page.setViewportSize({width,height:844});
      await page.getByRole('button',{name:'Menu',exact:true}).click();
      await page.locator('.cm-nav').getByRole('button',{name:'Settings',exact:true}).click();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Settings must fit mobile');
      if(width===390)captures.push(['settings-mobile.png',await page.screenshot({fullPage:true})]);
    }
    findings.checks.mobileWidths=[360,390];
    assert.equal(fs.readFileSync(path.join(fixture,'app.py'),'utf8').includes('return 3'),true);
    assert.deepEqual(findings.pageErrors,[]);
    assert.deepEqual(findings.externalRequests,[]);
    assert.deepEqual(await page.evaluate(()=>window.__codemapCspViolations),[]);
    findings.apiRequests=api;
    await context.close();
  }catch(error){
    if(page&&!page.isClosed()) {
      findings.diagnostic=await page.evaluate(()=>({ready:document.documentElement.dataset.dashboard,meta:[...document.querySelectorAll('meta[http-equiv]')].map(m=>m.outerHTML),body:document.body.innerText.slice(-3500)})).catch(()=>null);
    }
    process.stderr.write(JSON.stringify(findings,null,2)+'\n');
    throw error;
  }finally{
    if(browser)await browser.close();
    if(readonlyServer)await new Promise(resolve=>readonlyServer.close(resolve));
    child.kill();
    await new Promise(resolve=>{if(child.exitCode!==null)resolve();else child.once('exit',resolve);});
  }
  if(evidence){fs.mkdirSync(evidence,{recursive:true});for(const [name,bytes]of captures)fs.writeFileSync(path.join(evidence,name),bytes);fs.writeFileSync(path.join(evidence,'browser-checks.json'),JSON.stringify(findings,null,2));}
  process.stdout.write(JSON.stringify(findings,null,2)+'\n');
}
main().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;}).finally(()=>{
  const exact=fs.realpathSync(temporary);assert.equal(path.dirname(exact),fs.realpathSync(os.tmpdir()));assert.match(path.basename(exact),/^codemap-controls-browser-/);fs.rmSync(exact,{recursive:true,force:true});
});
