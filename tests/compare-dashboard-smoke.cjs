/* Optional comparison acceptance, using actual frozen task captures.
 * Uses the shared browser discovery helper.
 * Only trusted Codemap tooling parses these fixtures; source is never run.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { pathToFileURL } = require('node:url');
const { spawnSync } = require('node:child_process');
const { chromium, browserLaunchOptions } = require('./browser-test-support.cjs');

const repository = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'codemap-compare-browser-'));
const fixture = path.join(temporary, 'comparison-fixture');
const evidence = process.env.CODEMAP_COMPARE_EVIDENCE ? path.resolve(process.env.CODEMAP_COMPARE_EVIDENCE) : null;
const generator = String.raw`
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'skills/codemap/scripts'))
from codemap_lib import history,pipeline,tasks
root=Path(sys.argv[2]);root.mkdir()
source=root/'app.py'
source.write_text("raise RuntimeError('Comparison fixture source must never execute')\n\ndef answer():\n    return 1\n",encoding='utf-8')
(root/'app.js').write_text('export function helper() {\n  return 1;\n}\n',encoding='utf-8')
records=[]
for index in (2,3):
    started=tasks.start(root,'comparison-fixture','task-'+str(index))
    source.write_text(source.read_text(encoding='utf-8').replace('return '+str(index-1),'return '+str(index)),encoding='utf-8')
    if index==3:
        (root/'app.js').write_text('export function helper() {\n  return 2;\n}\n',encoding='utf-8')
        (root/'helpers.py').write_text('def extra():\n    return 3\n',encoding='utf-8')
    frozen=tasks.freeze(root,started['task_id'])
    ir=pipeline.analyze(frozen['work_root'],out=frozen['out_dir'],isolated=True,
        isolated_files=sorted(frozen['after']['files']),project_name=root.name,
        tier='video',structural_review=True,include_tests=True,log=lambda _:None)
    state=tasks.update(root,frozen['task_id'],status='awaiting_review',map_ready=True,reviewed_files=0,video_status='pending')
    records.append(state)
for name,states in [('single.html',records[:1]),('empty.html',[])]:
    page=history.write_history(root,states)
    (page.parent/name).write_bytes(page.read_bytes())
page=history.write_history(root,records)
data=history._dashboard_data(root,root/'.codemap',records)
ordered=sorted(data['tasks'],key=lambda t:t['createdAt'])
original=page.read_bytes()
started=tasks.start(root,'comparison-fixture','task-4')
source.write_text(source.read_text(encoding='utf-8').replace('return 3','return 4'),encoding='utf-8')
frozen=tasks.freeze(root,started['task_id'])
pipeline.analyze(frozen['work_root'],out=frozen['out_dir'],isolated=True,
    isolated_files=sorted(frozen['after']['files']),project_name=root.name,
    tier='video',structural_review=True,include_tests=True,log=lambda _:None)
third=tasks.update(root,frozen['task_id'],status='awaiting_review',map_ready=True,reviewed_files=0,video_status='pending')
updated=history.write_history(root,records+[third])
(updated.parent/'updated.html').write_bytes(updated.read_bytes())
page.write_bytes(original)
print(json.dumps({'ids':[t['id'] for t in ordered],
    'metrics':[{k:t[k] for k in ('changedFiles','added','removed','symbols','edges')} for t in ordered],
    'thirdId':third['task_id'],'snapshotsValid':all(tasks.verify(root,state['task_id']) for state in records+[third])}))
`;

async function main() {
  const built = spawnSync(process.env.PYTHON || 'python', ['-c', generator, repository, fixture], { encoding: 'utf8' });
  assert.equal(built.status, 0, built.stderr || built.stdout);
  const parsed = JSON.parse(built.stdout);
  assert.equal(parsed.snapshotsValid, true);
  assert.deepEqual(parsed.metrics.map(t => t.changedFiles), [1, 3]);
  const base = path.join(fixture, '.codemap');
  const captures = [];
  let advanced = false;
  const findings = { fixturesOnly: true, sourceApplicationsExecuted: false, parsed, checks: [], pageErrors: [], externalRequests: [] };
  const server = http.createServer((req, res) => {
    if (req.url === '/favicon.ico') { res.writeHead(204); res.end(); return; }
    let file;
    try { file = path.resolve(base, '.' + decodeURIComponent(req.url.split('?')[0])); }
    catch { res.writeHead(400); res.end(); return; }
    if (file !== base && !file.startsWith(base + path.sep)) { res.writeHead(403); res.end(); return; }
    if (file === base) file = path.join(file, 'index.html');
    if (advanced && file === path.join(base, 'index.html')) file = path.join(base, 'updated.html');
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
    fs.createReadStream(file).pipe(res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  try {
    browser = await chromium.launch({ ...browserLaunchOptions, headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark', acceptDownloads: true });
    const page = await context.newPage();
    page.on('pageerror', error => findings.pageErrors.push(String(error)));
    page.on('request', request => { if (/^https?:/.test(request.url()) && !request.url().startsWith(origin + '/')) findings.externalRequests.push(request.url()); });
    for (const protocol of ['file', 'http']) {
      const url = name => protocol === 'file' ? pathToFileURL(path.join(base, name)).href : origin + '/' + name;
      await page.goto(url('index.html'));
      await page.locator('html[data-dashboard="ready"]').waitFor();
      await page.locator('.cm-nav').getByRole('button', { name: /^Compare/ }).click();
      const view = page.getByTestId('compare-view');
      await view.waitFor();
      const a = view.getByRole('combobox', { name: 'Capture A', exact: true });
      const b = view.getByRole('combobox', { name: 'Capture B', exact: true });
      assert.equal(await a.inputValue(), parsed.ids[0]);
      assert.equal(await b.inputValue(), parsed.ids[1]);
      assert.equal(await a.locator(`option[value="${parsed.ids[1]}"]`).evaluate(option => option.disabled), true);
      assert.deepEqual((await view.locator('[data-metric="changedFiles"] td').allTextContents()).map(s => s.trim()), ['1', '3', '+2']);
      assert.match(await view.innerText(), /not a source diff between the final snapshots/i);
      await view.getByRole('button', { name: 'Swap captures', exact: true }).click();
      assert.equal(await a.inputValue(), parsed.ids[1]);
      assert.equal(await b.inputValue(), parsed.ids[0]);
      assert.equal((await view.locator('[data-metric="changedFiles"] td').last().innerText()).trim(), '−2');
      await view.getByRole('button', { name: 'Swap captures', exact: true }).click();
      assert.equal(await a.inputValue(), parsed.ids[0]);
      await page.evaluate(() => { window.__COMPARE_SECRET_SENTINEL__ = 'NEVER_EXPORT_COMPARE_SESSION_TOKEN'; window.__CODEMAP_SERVER__ = { apiBase: '/api', token: 'NEVER_EXPORT_COMPARE_SESSION_TOKEN' }; });
      const downloaded = page.waitForEvent('download');
      await view.getByRole('button', { name: /^Download comparison/ }).click();
      const download = await downloaded;
      const artifact = await download.path();
      const exported = fs.readFileSync(artifact, 'utf8');
      assert.match(download.suggestedFilename(), /^codemap-compare-[a-f0-9]{8}-[a-f0-9]{8}\.md$/);
      assert.match(exported, /not a source diff/);
      assert.match(exported, /\| Changed files \| 1 \| 3 \| \+2 \|/);
      assert.doesNotMatch(exported, /NEVER_EXPORT_COMPARE_SESSION_TOKEN|before_root|source_root|Comparison fixture source must never execute/);
      if (protocol === 'http') {
        advanced = true;
        await page.getByRole('button', { name: 'Refresh', exact: true }).click();
        await a.locator('option').nth(2).waitFor({ state: 'attached' });
        assert.equal(await a.locator('option').count(), 3);
        assert.equal(await a.inputValue(), parsed.ids[0], 'History refresh must preserve capture A');
        assert.equal(await b.inputValue(), parsed.ids[1], 'History refresh must preserve capture B');
        await a.selectOption(parsed.thirdId);
        assert.equal(await a.inputValue(), parsed.thirdId, 'A third capture is selectable');
        assert.equal(await b.inputValue(), parsed.ids[1]);
        await a.selectOption(parsed.ids[0]);
      }
      assert.ok(await view.getByText('In both selections', { exact: true }).count() > 0);
      assert.ok(await view.getByText('Only in this selection', { exact: true }).count() > 0);
      for (const section of await view.locator('.cm-compare-selection').all()) {
        const first = section.locator('details').first();
        await first.locator('summary').click();
        assert.ok(await first.locator('.cm-compare-code code').count() > 0);
      }
      if (evidence && protocol === 'http') {
        const desktop = path.join(temporary, 'comparison-desktop.png');
        await page.evaluate(() => scrollTo(0, 0));
        await page.screenshot({ path: desktop, fullPage: true }); captures.push([desktop, 'comparison-desktop.png']);
      }
      const widths = [];
      for (const width of [360, 390]) {
        await page.setViewportSize({ width, height: 844 });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `No ${width}px comparison overflow`);
        const select = await a.boundingBox();
        assert.ok(select.x >= 0 && select.x + select.width <= width + 1);
        widths.push(width);
      }
      if (evidence && protocol === 'http') {
        const mobile = path.join(temporary, 'comparison-mobile.png');
        await page.evaluate(() => scrollTo(0, 0));
        await page.screenshot({ path: mobile, fullPage: true }); captures.push([mobile, 'comparison-mobile.png']);
      }
      await page.setViewportSize({ width: 1440, height: 1000 });
      await view.locator('[data-compare-side="A"]').getByRole('button', { name: 'Learn changes', exact: true }).click();
      await page.getByTestId('learning-view').waitFor();
      for (const [name, heading] of [['single.html', 'One more capture unlocks comparison'], ['empty.html', 'Compare your captured work']]) {
        await page.goto(url(name)); await page.locator('html[data-dashboard="ready"]').waitFor();
        await page.locator('.cm-nav').getByRole('button', { name: /^Compare/ }).click();
        await page.getByTestId('compare-view').getByRole('heading', { name: heading, exact: true }).waitFor();
        assert.equal(await page.getByRole('button', { name: /^Download comparison/ }).count(), 0);
      }
      findings.checks.push({ protocol, actualFrozenCaptures: true, selectionAndSwap: true, exactRecordedDeltas: true,
        ownBaselinesDisclosure: true, selectedExcerptMembership: true, markdownExport: true, tokenNotExported: true,
        learningNavigation: true, gracefulEmptyAndSingle: true, refreshPreservesPair: protocol === 'http',
        thirdCaptureSelection: protocol === 'http', mobileWidths: widths, mobileNoOverflow: true });
    }
    assert.deepEqual(findings.pageErrors, []); assert.deepEqual(findings.externalRequests, []);
    await context.close();
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
  if (evidence) {
    fs.mkdirSync(evidence, { recursive: true });
    captures.forEach(([source, name]) => fs.copyFileSync(source, path.join(evidence, name)));
    fs.writeFileSync(path.join(evidence, 'browser-checks.json'), JSON.stringify(findings, null, 2));
  }
  process.stdout.write(JSON.stringify(findings, null, 2) + '\n');
}
main().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; }).finally(() => {
  const resolved = fs.realpathSync(temporary);
  assert.equal(path.dirname(resolved), fs.realpathSync(os.tmpdir())); assert.match(path.basename(resolved), /^codemap-compare-browser-/);
  fs.rmSync(temporary, { recursive: true, force: true });
});
