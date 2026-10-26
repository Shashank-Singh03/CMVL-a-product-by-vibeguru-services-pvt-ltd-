/* Optional browser acceptance for the real polyglot learning fixture.
 * Uses the shared browser discovery helper. Only trusted
 * Python tooling parses the fixture; its applications/CI/deployments never run.
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
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'codemap-learning-browser-'));
const fixture = path.join(temporary, 'polyglot-learning-fixture');
const evidence = process.env.CODEMAP_LEARNING_EVIDENCE ? path.resolve(process.env.CODEMAP_LEARNING_EVIDENCE) : null;
const generator = String.raw`
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'tests'))
from test_learning_integration import build_fixture
state, ir, page = build_fixture(sys.argv[2])
print(json.dumps({'task_id':state['task_id'],'languages':ir['stats']['languages'],'domains':ir['stats']['domains']}))
`;

async function main() {
  const built = spawnSync(process.env.PYTHON || 'python', ['-c', generator, repository, fixture], { encoding: 'utf8' });
  assert.equal(built.status, 0, built.stderr || built.stdout);
  const parsed = JSON.parse(built.stdout);
  const base = path.join(fixture, '.codemap');
  const index = path.join(base, 'index.html');
  const findings = { fixturesOnly: true, sourceApplicationsExecuted: false, parsed, checks: [], pageErrors: [], externalRequests: [] };
  const captures = [];
  const server = http.createServer((req, res) => {
    if (req.url === '/favicon.ico') { res.writeHead(204); res.end(); return; }
    let file;
    try { file = path.resolve(base, '.' + decodeURIComponent(req.url.split('?')[0])); }
    catch { res.writeHead(400); res.end(); return; }
    if (file !== base && !file.startsWith(base + path.sep)) { res.writeHead(403); res.end(); return; }
    if (file === base || fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
    if (!fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
    fs.createReadStream(file).pipe(res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  try {
    browser = await chromium.launch({ ...browserLaunchOptions, headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' });
    const page = await context.newPage();
    page.on('pageerror', error => findings.pageErrors.push(String(error)));
    page.on('request', request => {
      if (/^https?:/.test(request.url()) && !request.url().startsWith(origin + '/')) findings.externalRequests.push(request.url());
    });
    for (const [protocol, url] of [['file', pathToFileURL(index).href], ['http', origin + '/index.html']]) {
      await page.goto(url);
      await page.locator('html[data-dashboard="ready"]').waitFor();
      const payload = await page.evaluate(() => JSON.parse(document.getElementById('codemap-history-data').textContent));
      const task = payload.tasks.find(task => task.id === parsed.task_id);
      assert.ok(task.learning.available);
      assert.ok(task.progress.done < task.progress.total);
      assert.equal(task.reviewCoverage.reviewed, 0);
      assert.ok(task.languages.some(item => item.name === 'cpp'));
      assert.ok(task.domains.some(item => item.name === 'ci'));
      assert.equal(await page.locator('.cm-task-grid .cm-new-badge').count(), 1, 'Unviewed task is marked new');
      await page.getByRole('button', { name: /^Learn changes:/ }).first().click();
      const learning = page.getByTestId('learning-view');
      await learning.waitFor();
      await learning.getByRole('progressbar', { name: 'Lessons completed' }).waitFor();
      const original = await learning.getByRole('progressbar', { name: 'Lessons completed' }).evaluate(element => element.value);
      await learning.getByRole('button', { name: /^Mark understood/ }).click();
      const progressed = await learning.getByRole('progressbar', { name: 'Lessons completed' }).evaluate(element => element.value);
      assert.ok(Number(progressed) > Number(original), 'Explicit reading completion advances personal lesson progress');
      await page.reload();
      await page.locator('html[data-dashboard="ready"]').waitFor();
      assert.equal(await page.locator('.cm-task-grid .cm-new-badge').count(), 0, 'Opening learning marks the task viewed');
      if (await page.locator('#cm-menu-toggle').isVisible()) await page.locator('#cm-menu-toggle').click();
      await page.locator('.cm-nav').getByRole('button', { name: /^Learning/ }).click();
      await learning.waitFor();
      assert.equal(await learning.getByRole('progressbar', { name: 'Lessons completed' }).evaluate(element => element.value), progressed, 'Lesson IDs persist for the same content revision');
      const changed = task.learning.steps.find(step => step.kind === 'changes');
      assert.ok(changed, 'Real change lesson must exist');
      await learning.locator(`[data-lesson-step="${changed.id}"]`).click();
      assert.ok(await learning.locator('[data-line-kind="added"]').count(), 'Real added lines are highlighted');
      assert.ok(await learning.locator('[data-line-kind="removed"]').count(), 'Real removed lines are highlighted');
      if (evidence && protocol === 'http') {
        const changes = path.join(temporary, 'learning-changes-desktop.png');
        await page.screenshot({ path: changes, fullPage: true });
        captures.push([changes, 'learning-changes-desktop.png']);
      }
      const conceptStep = task.learning.steps.find(step => step.conceptIds?.length);
      await learning.locator(`[data-lesson-step="${conceptStep.id}"]`).click();
      const concept = task.learning.concepts.find(item => item.id === conceptStep.conceptIds[0]);
      await learning.getByRole('button', { name: new RegExp('^' + concept.term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) }).first().click();
      await learning.getByTestId('concept-definition').waitFor();
      assert.match(await learning.getByTestId('concept-definition').innerText(), new RegExp(concept.term, 'i'));
      await page.locator('.cm-nav').getByRole('button', { name: /^Analysis/ }).click();
      const analysis = page.getByTestId('analysis-view');
      await analysis.waitFor();
      await page.waitForFunction(() => document.querySelectorAll('[data-testid="analysis-view"] [data-chart] canvas').length >= 3);
      assert.ok(await analysis.locator('[data-chart] canvas').count() >= 3, 'Apache ECharts renders actual chart canvases');
      assert.ok(await analysis.locator('table').count() >= 3, 'Charts expose readable data tables');
      assert.match(await analysis.innerText(), /JavaScript|TypeScript|C\+\+|CI\/CD/);
      if (evidence && protocol === 'http') {
        const desktop = path.join(temporary, 'analysis-desktop.png');
        await page.screenshot({ path: desktop, fullPage: true });
        captures.push([desktop, 'analysis-desktop.png']);
      }
      const widths = [];
      for (const width of [360, 390]) {
        await page.setViewportSize({ width, height: 844 });
        await page.waitForTimeout(250);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `No ${width}px analysis overflow`);
        widths.push(width);
      }
      await page.getByRole('button', { name: 'Menu', exact: true }).click();
      await page.locator('.cm-nav').getByRole('button', { name: /^Learning/ }).click();
      await learning.waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'Mobile learner must not overflow');
      if (evidence && protocol === 'http') {
        const mobile = path.join(temporary, 'learning-mobile.png');
        await page.screenshot({ path: mobile, fullPage: true });
        captures.push([mobile, 'learning-mobile.png']);
      }
      await page.setViewportSize({ width: 1440, height: 1000 });
      const beforeTheme = await page.locator('html').getAttribute('data-theme');
      await page.getByRole('button', { name: /Switch to .* theme/ }).click();
      assert.notEqual(await page.locator('html').getAttribute('data-theme'), beforeTheme);
      findings.checks.push({ protocol, languageDomains: true, learningNavigation: true, definitions: true,
        highlightedChanges: true, personalProgressPersists: true, echartsCanvas: true,
        chartTables: true, mobileWidths: widths, mobileNoOverflow: true, theme: true });
    }
    assert.deepEqual(findings.pageErrors, []);
    assert.deepEqual(findings.externalRequests, []);
    await context.close();
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
  if (evidence) {
    fs.mkdirSync(evidence, { recursive: true });
    for (const [source, name] of captures) fs.copyFileSync(source, path.join(evidence, name));
    fs.writeFileSync(path.join(evidence, 'browser-checks.json'), JSON.stringify(findings, null, 2));
  }
  process.stdout.write(JSON.stringify(findings, null, 2) + '\n');
}
main().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; }).finally(() => {
  const resolved = fs.realpathSync(temporary);
  assert.equal(path.dirname(resolved), fs.realpathSync(os.tmpdir()));
  assert.match(path.basename(resolved), /^codemap-learning-browser-/);
  fs.rmSync(temporary, { recursive: true, force: true });
});
