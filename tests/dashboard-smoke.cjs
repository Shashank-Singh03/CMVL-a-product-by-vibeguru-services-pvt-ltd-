/* Optional browser acceptance test. Uses playwright-core and an installed Chrome.
 * Uses the shared browser discovery helper. No target app is run.
 * CODEMAP_SMOKE_VIDEO optionally supplies a real MP4 to check media metadata.
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
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'codemap-dashboard-browser-'));
const fixture = path.join(temporary, 'fixture-project');
const evidence = process.env.CODEMAP_SMOKE_EVIDENCE ? path.resolve(process.env.CODEMAP_SMOKE_EVIDENCE) : null;
const A = 'a'.repeat(32), B = 'b'.repeat(32), C = 'c'.repeat(32), D = 'd'.repeat(32);
const generator = String.raw`
import json, os, shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'skills' / 'codemap' / 'scripts'))
from codemap_lib.history import write_history
root = Path(sys.argv[2]); root.mkdir()
states = []
for i, (letter, status, title) in enumerate([
    ('a', 'complete', 'Improve task search and discovery'),
    ('b', 'rendering_video', 'Explain the background workflow'),
    ('c', 'review_failed', 'Investigate import resolution'),
    ('d', 'skipped', '')]):
    ident = letter * 32
    state = dict(task_id=ident, status=status, created_at=1700000000+i*3600,
                 updated_at=1700000060+i*3600, reviewed_files=2,
                 attribution='ambiguous' if letter == 'c' else 'window')
    output = root / '.codemap' / 'tasks' / ident / 'output'; output.mkdir(parents=True)
    if title:
        ir = dict(nodes={}, why=[dict(source='agent', text=title)],
                  stats=dict(files=3, symbols=7, edges=4),
                  scope=dict(mode='diff', files=2, add=9, **{'del': 3}))
        (output / 'ir.json').write_text(json.dumps(ir), encoding='utf-8')
    if letter in ('a', 'b'):
        state['map_ready'] = True
        (output / 'codemap.html').write_text('<!doctype html><title>Fixture map</title><h1>Fixture map</h1>', encoding='utf-8')
    if letter == 'a':
        state['video_status'] = 'ready'
        video = os.environ.get('CODEMAP_SMOKE_VIDEO')
        if video:
            shutil.copyfile(video, output / 'codemap.mp4')
        else:
            (output / 'codemap.mp4').write_bytes(b'fixture media; controls only')
    elif letter == 'b': state['video_status'] = 'rendering'
    states.append(state)
write_history(root, states)
`;

async function main() {
  const generated = spawnSync(process.env.PYTHON || 'python', ['-c', generator, repository, fixture], { encoding: 'utf8' });
  assert.equal(generated.status, 0, generated.stderr || generated.stdout);
  if (evidence) fs.mkdirSync(evidence, { recursive: true });
  const base = path.join(fixture, '.codemap');
  const index = path.join(base, 'index.html');
  const server = http.createServer((req, res) => {
    if (req.url === '/favicon.ico') { res.writeHead(204); res.end(); return; }
    let filename;
    try { filename = path.resolve(base, '.' + decodeURIComponent(req.url.split('?')[0])); }
    catch { res.writeHead(400); res.end(); return; }
    if (!filename.startsWith(base + path.sep) && filename !== base) { res.writeHead(403); res.end(); return; }
    if (filename === base) filename = index;
    if (fs.existsSync(filename) && fs.statSync(filename).isDirectory()) filename = path.join(filename, 'index.html');
    if (!fs.existsSync(filename)) { res.writeHead(404); res.end(); return; }
    const type = filename.endsWith('.mp4') ? 'video/mp4' : 'text/html; charset=utf-8';
    res.writeHead(200, { 'Content-Type': type, 'Cache-Control': 'no-store' });
    fs.createReadStream(filename).pipe(res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  const findings = { fixturesOnly: true, checks: [], pageErrors: [], externalRequests: [] };
  try {
    browser = await chromium.launch({ ...browserLaunchOptions, headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' });
    const page = await context.newPage();
    page.on('pageerror', error => findings.pageErrors.push(error.message));
    page.on('request', request => { const url = request.url(); if (/^https?:/.test(url) && !url.startsWith(origin + '/')) findings.externalRequests.push(url); });
    for (const [protocol, url] of [['file', pathToFileURL(index).href], ['http', origin + '/index.html']]) {
      await page.goto(url);
      await page.locator('html[data-dashboard="ready"]').waitFor();
      const cards = page.locator('.cm-task-grid .cm-task');
      assert.equal(await cards.count(), 4);
      assert.equal(await page.locator('meta[http-equiv="refresh"]').count(), 0, 'React must replace disruptive whole-page refresh');
      const search = page.getByRole('searchbox', { name: 'Search tasks' });
      await search.fill('search discovery');
      assert.equal(await cards.count(), 1);
      assert.match(await cards.first().innerText(), /Improve task search/);
      await search.fill('not-a-real-task');
      assert.equal(await cards.count(), 0);
      await page.getByRole('button', { name: 'Clear filters', exact: true }).click();
      assert.equal(await cards.count(), 4);
      await page.getByRole('combobox', { name: 'Filter by status' }).selectOption('attention');
      assert.equal(await cards.count(), 1);
      assert.match(await cards.first().innerText(), /Investigate import resolution/);
      await page.getByRole('combobox', { name: 'Filter by status' }).selectOption('all');
      await page.getByRole('combobox', { name: 'Sort tasks' }).selectOption('oldest');
      assert.match(await cards.first().innerText(), /Improve task search/);
      await page.getByRole('button', { name: 'Details for Improve task search and discovery', exact: true }).click();
      const detail = page.locator('dialog.cm-detail-dialog');
      await detail.waitFor({ state: 'visible' });
      assert.equal(await detail.locator('input[readonly]').inputValue(), A);
      assert.match(await detail.innerText(), /Task timeline/);
      await page.keyboard.press('Escape');
      await detail.waitFor({ state: 'detached' });
      assert.match(await page.locator(':focus').getAttribute('aria-label'), /^Details for /);
      await page.locator('.cm-nav').getByRole('button', { name: /^Videos/ }).click();
      assert.equal(await cards.count(), 1);
      await page.getByRole('button', { name: 'Preview video', exact: true }).click();
      const video = page.locator('dialog.cm-video-dialog video');
      await video.waitFor({ state: 'visible' });
      assert.equal(await video.getAttribute('controls'), '');
      assert.match(await video.getAttribute('src'), new RegExp(`tasks/${A}/output/codemap.mp4$`));
      if (process.env.CODEMAP_SMOKE_VIDEO) {
        await page.waitForFunction(() => { const video = document.querySelector('dialog video'); return video && video.readyState >= 1; });
        assert.ok(await video.evaluate(element => element.duration > 0));
      }
      await page.getByRole('button', { name: 'Close video preview' }).click();
      await page.locator('.cm-nav').getByRole('button', { name: /^All tasks/ }).click();
      const oldTheme = await page.locator('html').getAttribute('data-theme');
      await page.getByRole('button', { name: /Switch to .* theme/ }).click();
      assert.notEqual(await page.locator('html').getAttribute('data-theme'), oldTheme);
      await page.getByRole('button', { name: /Switch to .* theme/ }).click();
      await page.locator('.cm-main h1').click();
      await page.keyboard.press('/');
      assert.equal(await search.evaluate(element => element === document.activeElement), true);
      await search.fill('background');
      if (protocol === 'http') {
        await page.getByRole('button', { name: 'Refresh', exact: true }).click();
        await page.getByText('History is up to date.', { exact: true }).waitFor();
        assert.equal(await search.inputValue(), 'background', 'Refresh preserves search');
      }
      await search.fill('');
      if (evidence && protocol === 'http') await page.screenshot({ path: path.join(evidence, 'desktop.png'), fullPage: true });
      await page.setViewportSize({ width: 390, height: 844 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'Mobile page must not overflow horizontally');
      await page.getByRole('button', { name: 'Details for Improve task search and discovery', exact: true }).click();
      await detail.waitFor({ state: 'visible' });
      const box = await detail.boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= 391, 'Mobile dialog remains inside viewport');
      await page.keyboard.press('Escape');
      if (evidence && protocol === 'http') await page.screenshot({ path: path.join(evidence, 'mobile.png'), fullPage: true });
      await page.setViewportSize({ width: 1440, height: 1000 });
      findings.checks.push({ protocol, search: true, filter: true, sort: true, details: true,
                             focusRestored: true, videoPreview: true, theme: true,
                             keyboardSearch: true, mobileNoOverflow: true, refreshPreservesSearch: protocol === 'http' });
    }
    assert.deepEqual(findings.pageErrors, []);
    assert.deepEqual(findings.externalRequests, []);
    if (evidence) fs.writeFileSync(path.join(evidence, 'browser-checks.json'), JSON.stringify(findings, null, 2));
    process.stdout.write(JSON.stringify(findings, null, 2) + '\n');
    await context.close();
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; }).finally(() => {
  // Only remove the exact directory created above; no project paths are touched.
  const resolvedTemporary = fs.realpathSync(temporary);
  assert.equal(path.dirname(resolvedTemporary), fs.realpathSync(os.tmpdir()));
  assert.match(path.basename(resolvedTemporary), /^codemap-dashboard-browser-/);
  fs.rmSync(temporary, { recursive: true, force: true });
});
