/* Browser navigation acceptance using only a disposable captured notes demo. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const {spawnSync} = require('node:child_process');
const {pathToFileURL} = require('node:url');
const { chromium, browserLaunchOptions } = require('./browser-test-support.cjs');
const repo = path.resolve(__dirname,'..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(),'codemap-nav-browser-'));
const evidence = process.env.CODEMAP_NAV_EVIDENCE ? path.resolve(process.env.CODEMAP_NAV_EVIDENCE) : null;
const make = String.raw`
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'skills/codemap/scripts'))
from codemap_lib import demo,tasks,pipeline,history
root,_=demo.create(Path(sys.argv[2])/'notes-demo')
start=tasks.start(root,'navigation','one')
demo.apply_agent_changes(root)
state=tasks.freeze(root,start['task_id'])
ir=pipeline.analyze(state['work_root'],out=state['out_dir'],isolated=True,isolated_files=sorted(state['after']['files']),project_name='notes-demo',tier='video',structural_review=True,include_tests=True,log=lambda _:None)
state=tasks.update(root,state['task_id'],status='awaiting_review',map_ready=True,reviewed_files=0)
page=history.write_history(root)
print(json.dumps({'project':str(root),'id':state['task_id'],'page':str(page),'map':str(Path(state['out_dir'])/'codemap.html')}))
`;
let browser, server; const captures=[];
const findings={fixturesOnly:true,sourceApplicationsExecuted:false,checks:[],pageErrors:[],externalRequests:[]};
async function main(){
 const result=spawnSync('python',['-c',make,repo,temp],{encoding:'utf8'}); assert.equal(result.status,0,result.stderr);
 const built=JSON.parse(result.stdout); const base=path.dirname(built.page);
 server=http.createServer((req,res)=>{let target=path.resolve(base,'.'+decodeURIComponent(req.url.split('?')[0]));if(!target.startsWith(base+path.sep)&&target!==base){res.writeHead(403);return res.end();}if(target===base)target=path.join(base,'index.html');if(!fs.existsSync(target)){res.writeHead(404);return res.end();}res.setHeader('Content-Type',target.endsWith('.html')?'text/html; charset=utf-8':'text/plain');res.end(fs.readFileSync(target));});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin=`http://127.0.0.1:${server.address().port}`;
 browser=await chromium.launch({...browserLaunchOptions,headless:true});
 for(const protocol of ['file','http']){
  const context=await browser.newContext({viewport:{width:1440,height:1000},colorScheme:'dark'});const page=await context.newPage();
  page.on('pageerror',e=>findings.pageErrors.push(String(e)));
  page.on('request',req=>{const u=req.url();if(/^https?:/.test(u)&&!u.startsWith(origin))findings.externalRequests.push(u);});
  const entry=protocol==='file'?pathToFileURL(built.page).href:origin+'/index.html';
  await page.goto(entry);await page.getByRole('heading',{name:'Task library',exact:false}).waitFor();
  await page.locator('.cm-nav button[title="Learning"]').click();
  await page.locator('[data-testid="learning-view"]').waitFor();
  await page.locator('[data-lesson-step="concepts"]').click();assert.match(page.url(),/step=concepts/);
  await page.locator('[data-lesson-step="changes"]').click();assert.match(page.url(),/step=changes/);
  assert.equal(await page.locator('.cm-change-example').count(),1);
  assert.equal(await page.locator('.cm-glossary').evaluate(el=>el.open),false);
  if(await page.getByRole('button',{name:'Next change →',exact:true}).isEnabled()){
    await page.getByRole('button',{name:'Next change →',exact:true}).click();assert.match(page.url(),/item=change-/);
    const itemUrl=page.url();await page.reload();await page.locator('.cm-change-example').waitFor();assert.equal(page.url(),itemUrl);
  }
  const frame=page.frameLocator('.map-frame');
  const lesson=page.url();const mapLink=page.getByRole('link',{name:'See this item in the map →',exact:true});
  assert.equal(await mapLink.count(),1);await mapLink.click();await frame.locator('#mapHome').waitFor();
  assert.match(page.url(),/view=map&task=.*&v=.*&n=/);assert.equal(await frame.locator('#findPanel').evaluate(el=>el.open),false);
  assert.equal(await frame.locator('#panel > details[open]').count(),0);
  const firstMap=page.url();await frame.locator('#mapHome').click();assert.notEqual(page.url(),firstMap);
  await frame.locator('#navBack').click();await page.waitForURL(firstMap);
  await frame.locator('#findPanel > summary').click();await frame.locator('#findQuery').fill('delete');
  assert.ok(await frame.locator('#findResults .result').count()>0);
  await frame.locator('#findResults .result').first().click();assert.equal(await frame.locator('#findPanel').evaluate(el=>el.open),false);
  await frame.locator('#navBack').click();await page.waitForURL(firstMap);
  await frame.locator('#mapLearning').click();await page.locator('[data-testid="learning-view"]').waitFor();assert.match(page.url(),/view=learning.*task=.*step=changes/);
  assert.equal(new URLSearchParams(new URL(page.url()).hash.slice(1)).get('item'),new URLSearchParams(new URL(lesson).hash.slice(1)).get('item'),'Map returns to the same selected learning example');
  await page.getByRole('button',{name:'Analyze this task →',exact:true}).click();await page.locator('[data-testid="analysis-view"]').waitFor();assert.match(page.url(),/view=analysis&task=/);
  await page.getByRole('button',{name:'Back',exact:true}).click();await page.locator('[data-testid="learning-view"]').waitFor();
  await page.goForward();await page.locator('[data-testid="analysis-view"]').waitFor();
  await page.getByRole('navigation',{name:'Breadcrumb'}).getByRole('link',{name:'Home',exact:true}).click();
  await page.getByRole('heading',{name:'Task library',exact:false}).waitFor();
  await page.goto(lesson);await page.locator('.cm-change-example').waitFor();
  if(protocol==='http')captures.push(['learning-desktop.png',await page.screenshot({fullPage:true})]);
  await page.getByRole('link',{name:'Open full map',exact:false}).click();await frame.locator('#mapHome').waitFor();
  await frame.locator('#risks').click();assert.ok(await frame.locator('#panel details').count()>0);
  await frame.getByRole('button',{name:'Back to item',exact:false}).click();
  for(const width of [390,360]){
    await page.setViewportSize({width,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Map page overflow');
    await frame.locator('#mapLearning').click();await page.locator('[data-testid="learning-view"]').waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Learning overflow');
    if(width===390&&protocol==='http')captures.push(['learning-mobile.png',await page.screenshot({fullPage:true})]);
    await page.getByRole('link',{name:'Open full map',exact:false}).click();await frame.locator('#mapHome').waitFor();
  }
  if(protocol==='http')captures.push(['map-mobile.png',await page.screenshot({fullPage:true})]);
  await page.setViewportSize({width:1440,height:1000});await frame.locator('#mapHome').click();
  if(protocol==='http')captures.push(['map-desktop.png',await page.screenshot({fullPage:true})]);
  await frame.locator('#dashboardHome').click();await page.getByRole('heading',{name:'Task library',exact:false}).waitFor();
  findings.checks.push({protocol,mapWithinWorkspace:true,home:true,backForward:true,bookmarkedLesson:true,oneChangeAtATime:true,collapsedDefinitions:true,deepMapLink:true,sameLearningItemReturn:true,searchReturnsToMap:true,relatedAnalysis:true,collapsedMapEvidence:true,risksAndReturn:true,mobileNoOverflow:true});
  await context.close();
 }
 // The user's curated standalone map has a real companion overview, not a fake task.
 const context=await browser.newContext();const page=await context.newPage();
 await page.goto(pathToFileURL(path.join(repo,'examples/demo-output/codemap.html')).href);await page.locator('#mapHome').waitFor();await page.locator('#dashboardHome').click();await page.getByRole('heading',{name:'A clearer view of every change.'}).waitFor();
 findings.curatedExampleHome=true;await context.close();
 assert.deepEqual(findings.pageErrors,[]);assert.deepEqual(findings.externalRequests,[]);
}
main().then(()=>process.stdout.write(JSON.stringify(findings,null,2)+'\n')).catch(e=>{process.stderr.write(e.stack+'\n');process.exitCode=1;}).finally(async()=>{
 if(browser)await browser.close();if(server)await new Promise(r=>server.close(r));
 if(evidence){fs.mkdirSync(evidence,{recursive:true});for(const[name,buffer]of captures)fs.writeFileSync(path.join(evidence,name),buffer);fs.writeFileSync(path.join(evidence,'browser-checks.json'),JSON.stringify(findings,null,2));}
 const resolved=fs.realpathSync(temp);assert.equal(path.dirname(resolved),fs.realpathSync(os.tmpdir()));assert.match(path.basename(resolved),/^codemap-nav-browser-/);fs.rmSync(resolved,{recursive:true,force:true});
});
