"""Exercise viewer logic in Node with a small DOM substitute; no browser dependency.

These checks cover state and event handling, not browser rendering, focus behaviour,
or the platform's speech implementation.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")

HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.nodeType = 1;
    this.children = []; this.attrs = {}; this.events = {}; this.style = {};
    this.value = ''; this.checked = false; this.hidden = false; this.clientWidth = 1200;
    this.classList = {
      add: name => this.setClass(name, true),
      remove: name => this.setClass(name, false),
      toggle: (name, enabled) => this.setClass(name, enabled)
    };
  }
  setClass(name, enabled) {
    let names = (this.className || '').split(/\s+/).filter(Boolean);
    if (enabled && !names.includes(name)) names.push(name);
    if (!enabled) names = names.filter(n => n !== name);
    this.className = names.join(' ');
  }
  setAttribute(key, value) { this.attrs[key] = String(value); if (key === 'class') this.className = value; }
  getAttribute(key) { return this.attrs[key]; }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  get firstChild() { return this.children[0] || null; }
  get childNodes() { return this.children; }
  set innerHTML(value) { this.children = []; this._text = ''; this._html = value; }
  get innerHTML() { return this._html || ''; }
  set textContent(value) { this.children = []; this._text = String(value); }
  get textContent() { return (this._text || '') + this.children.map(c => c.textContent).join(''); }
  addEventListener(name, fn) { this.events[name] = fn; }
  focus() { document.activeElement = this; }
  click() { if (this.onclick) this.onclick(); if (this.events.click) this.events.click({}); }
  showModal() { this.open = true; }
  getContext() { return { measureText: text => ({width: text.length * 7}) }; }
  querySelectorAll(selector) {
    const match = element => selector === '.node' ? (element.className || '').split(' ').includes('node')
      : selector === 'li[data-id]' ? element.tagName === 'LI' && !!element.attrs['data-id']
      : selector.split(/,\s*/).some(tag => element.tagName.toLowerCase() === tag);
    const result = [];
    const walk = el => el.children.forEach(child => { if (match(child)) result.push(child); walk(child); });
    walk(this); return result;
  }
}
const elements = {};
const $ = id => elements[id] || (elements[id] = new Element('div'));
global.document = {
  documentElement: new Element('html'), activeElement: new Element('body'), events: {},
  getElementById: $, createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag),
  createTextNode: text => { const node = new Element('text'); node.textContent = text; return node; },
  addEventListener(name, fn) { this.events[name] = fn; }
};
global.location = {hash: '', protocol: 'file:', href: 'file:///C:/work/my%20repo/.codemap/codemap.html'};
const historyCalls = [];
global.history = {
  pushState(_, __, hash) { historyCalls.push(['push', hash]); location.hash = hash; },
  replaceState(_, __, hash) { historyCalls.push(['replace', hash]); location.hash = hash; }
};
const timers = new Map(); let timerID = 0;
global.setTimeout = (fn, ms) => { timers.set(++timerID, {fn, ms}); return timerID; };
global.clearTimeout = id => timers.delete(id);
global.getComputedStyle = () => ({ getPropertyValue: () => 'sans-serif' });
global.window = {
  innerHeight: 900, innerWidth: 1400, events: {},
  addEventListener(name, fn) { this.events[name] = fn; },
  matchMedia: () => ({matches: false}),
  speechSynthesis: {
    cancels: 0, pauses: 0, resumes: 0,
    getVoices: () => [], cancel() { this.cancels++; },
    speak(utterance) { this.last = utterance; }, pause() { this.pauses++; }, resume() { this.resumes++; }
  }
};
global.SpeechSynthesisUtterance = function(text) { this.text = text; };
global.Audio = class {
  constructor(clip) { this.clip = clip; this.currentTime = 0; }
  play() { this.paused = false; return Promise.resolve(); }
  pause() { this.paused = true; }
};
const fixture = {
  repo: 'sample', scope: {mode: 'diff', label: 'HEAD', files: 3, add: 5, del: 1},
  artifacts: {diff: 'codemap.diff', source_base: '../'},
  stats: {files: 2, symbols: 2, parse_errors: ['broken.py: syntax error <unsafe>']},
  other_files: [{path: 'lock.json', note: 'lockfile'}],
  risks: [
    {id: 'r1', sev: 'high', file: 'svc.py', line: 3, msg: 'High priority check', rule: 'test'},
    {id: 'r2', sev: 'medium', file: 'svc.py', line: 1, msg: 'Medium priority check', rule: 'test'}
  ],
  nodes: {
    root: {id:'root', name:'sample', kind:'root', view:'v0', children:['module', 'job'], add:5, del:1},
    module: {id:'module', name:'svc.py', kind:'module', parent:'root', view:'v1', domain:'code', status:'modified', file:'svc.py', children:['function'], risk:2, flags:['r2']},
    function: {id:'function', name:'serve', qual:'serve', kind:'function', parent:'module', domain:'code', status:'modified', file:'svc.py', line:3, end:40, flags:['r1'], diff:['+secret_safe()', ' ... 9 more lines']},
    job: {id:'job', name:'deploy', kind:'function', parent:'root', domain:'ci', status:'added', file:'.github/workflows/deploy.yml'}
  },
  views: [
    {id:'v0', node:'root', children:['module','job'], order:['module','job'], edges:[], level:0},
    {id:'v1', node:'module', children:['function'], order:['function'], edges:[], level:1}
  ],
  beats:[{kind:'title', text:'A test narration with several words.', sid:'b0'}],
  audio:{b0:'data:audio/mpeg;base64,AA=='}, version:'test', tier:'free', generated:'now'
};
$('codemap-data').textContent = JSON.stringify(fixture);
$('player').hidden = true;
let script = fs.readFileSync('skills/codemap/assets/viewer.html', 'utf8').split('<script>')[1].split('</script>')[0];
script = script.replace(/\}\)\(\);\s*$/, 'globalThis.api = {D, renderCoverage, findMatches, state, go, select, P, playFrom, pause, resume, setMuted, openPlayer, closePlayer, expandDiff, sourceHref, fullDiffLink, ARTIFACTS};})();');
vm.runInThisContext(script);
"""


@unittest.skipUnless(NODE, "Node is needed to exercise viewer JavaScript")
class ViewerRegressionTest(unittest.TestCase):
    def run_js(self, checks: str):
        result = subprocess.run(
            [NODE, "-"], input=HARNESS + checks, text=True, encoding="utf-8",
            cwd=ROOT, capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_coverage_exposes_parse_problems_and_unmapped_changes(self):
        self.run_js(r"""
assert.equal($('coverage').open, true);
assert.match($('coverageSummary').textContent, /2 mapped files.*1 reported parse problem.*1 unmapped change/);
assert.match($('coverageDetails').textContent, /broken.py: syntax error <unsafe>/);
assert.match($('coverageDetails').textContent, /lock.json.*lockfile/);
assert.match($('coverageDetails').textContent, /Dynamic dispatch/);
assert.equal($('coverageDetails').querySelectorAll('img').length, 0);
""")

    def test_review_coverage_and_source_evidence_are_visible_and_safe(self):
        self.run_js(r"""
api.D.stats.parse_errors = []; api.D.other_files = [];
api.D.stats.coverage = {omitted_count: 1, omitted_files: ['large.bin'], without_symbols_count: 0};
api.D.review = {status: 'pending'}; api.ARTIFACTS.review_request = 'review.md';
api.renderCoverage();
assert.equal($('coverage').open, false); // Warning remains in summary; details are on demand.
assert.match($('coverageSummary').textContent, /1 omitted files.*model review pending/);
assert.match($('coverageDetails').textContent, /large.bin/);
assert.match($('coverageDetails').textContent, /Open structural review instructions/);
api.D.review = {status:'reviewed', reviewed_file_count:1, accepted_symbol_count:1, accepted_edge_count:1,
  uncertain_count:1, uncertain:[{file:'svc.py',line:3,reason:'Runtime value <unsafe>'}]};
api.renderCoverage();
assert.match($('coverageDetails').textContent, /Model reports reviewing 1 files.*1 uncertain/);
api.D.nodes.function.provenance = {reason:'Recovered wrapper', evidence:[{file:'svc.py',line:3,quote:'<img src=x onerror=bad()>'}]};
api.select('function');
assert.match($('panel').textContent, /Recovered by model review.*Source evidence.*svc.py:3.*<img/);
assert.equal($('panel').querySelectorAll('img').length, 0);
api.D.views[0].edges = [['module','job',2,.9,'refs']];
api.D.views[0].review_edges = [['module','job',1]];
api.go('v0');
const modelPath = $('graph').querySelectorAll('path').find(p => (p.getAttribute('class') || '').includes('edge'));
assert.match(modelPath.getAttribute('class'), /weak/);
assert.match(modelPath.textContent, /uses.*includes 1 source-backed model inference.*maximum/);
""")

    def test_subtree_risks_keep_global_priority_and_root_counts_are_labeled(self):
        self.run_js(r"""
const risks = $('panel').querySelectorAll('div').filter(el => el.className === 'risk-item');
assert.equal(risks.length, 2);
assert.match(risks[0].textContent, /High priority check/);
assert.match(risks[1].textContent, /Medium priority check/);
assert.match($('panel').textContent, /Mapped lines/);
""")

    def test_search_combines_text_status_domain_and_risk_filters(self):
        self.run_js(r"""
assert.deepEqual(api.findMatches('svc.py', 'modified', 'code', true), ['function', 'module']);
assert.deepEqual(api.findMatches('DEPLOY', 'added', 'ci', false), ['job']);
assert.deepEqual(api.findMatches('deploy', 'modified', '', false), []);
$('findQuery').value = 'serve'; $('findQuery').oninput();
assert.equal($('findResults').hidden, false);
assert.equal($('findResults').children.length, 1);
$('findResults').children[0].click();
assert.equal(api.state.view, 'v1'); assert.equal(api.state.sel, 'function');
$('findClear').onclick(); assert.equal($('findResults').hidden, true);
""")

    def test_browser_history_restores_view_and_selection_without_new_entries(self):
        self.run_js(r"""
assert.equal(historyCalls[0][0], 'replace');
api.go('v1', 'function');
assert.equal(historyCalls.at(-1)[0], 'push');
let entries = historyCalls.length;
location.hash = '#v=v0&n=job'; window.events.popstate();
assert.equal(api.state.view, 'v0'); assert.equal(api.state.sel, 'job');
assert.equal(historyCalls.length, entries);
location.hash = '#v=unknown&n=%E0%A4%A'; window.events.hashchange();
assert.equal(api.state.view, 'v0'); assert.equal(api.state.sel, null);
""")

    def test_graph_and_workflow_offer_keyboard_activation(self):
        self.run_js(r"""
let node = $('graph').querySelectorAll('.node')[0];
assert.equal(node.getAttribute('tabindex'), '0'); assert.equal(node.getAttribute('role'), 'button');
let prevented = false;
node.events.keydown({key:'Enter', preventDefault(){prevented = true;}});
assert.equal(prevented, true); assert.equal(api.state.sel, 'module');
node.events.keydown({key:'Enter', shiftKey:true, preventDefault(){}});
assert.equal(api.state.view, 'v1');
assert.equal($('steps').children[0].children[0].tagName, 'BUTTON');
""")

    def test_expanded_diff_keeps_location_and_truncation_notice(self):
        self.run_js(r"""
api.expandDiff(fixture.nodes.function);
assert.equal($('diffDialog').open, true);
assert.match($('diffLocation').textContent, /svc.py:3/);
assert.match($('diffContent').textContent, /9 more lines/);
assert.match($('diffTitle').textContent, /excerpt/);
let view = api.state.view;
document.events.keydown({key:'Escape'});
assert.equal(api.state.view, view);
""")

    def test_source_handoff_encodes_local_path_and_uses_recorded_line(self):
        self.run_js(r"""
assert.equal(api.sourceHref({file:'src/café #1.py', line:7}), 'vscode://file/C:/work/my%20repo/src/caf%C3%A9%20%231.py:7');
api.expandDiff(fixture.nodes.function);
const links = $('diffHandoff').querySelectorAll('a');
assert.equal(links[0].textContent, 'Open in VS Code');
assert.equal(links[0].getAttribute('href'), 'vscode://file/C:/work/my%20repo/svc.py:3');
assert.equal(links[1].getAttribute('href'), 'codemap.diff');
""")

    def test_source_handoff_is_hidden_for_hosted_or_deleted_source(self):
        self.run_js(r"""
assert.equal(api.sourceHref({file:'svc.py', status:'deleted', line:3}), null);
location.protocol = 'https:'; location.href = 'https://example.test/map/codemap.html';
assert.equal(api.sourceHref({file:'svc.py', line:3}), null);
assert.equal(api.fullDiffLink().getAttribute('href'), 'codemap.diff');
""")

    def test_masked_source_paths_have_no_editor_link_and_diff_is_review_only(self):
        self.run_js(r"""
assert.equal(api.sourceHref({file:'src/[REDACTED].py', line:3}), null);
api.ARTIFACTS.source_base = '../[REDACTED]/';
assert.equal(api.sourceHref({file:'svc.py', line:3}), null);
api.expandDiff(fixture.nodes.function);
const links = $('diffHandoff').querySelectorAll('a');
assert.equal(links.length, 1); assert.equal(links[0].getAttribute('href'), 'codemap.diff');
assert.match($('diffHandoff').textContent, /Review only.*not an apply-ready patch/);
assert.match($('panel').textContent, /Review only.*not an apply-ready patch/);
""")

    def test_handoffs_reject_absolute_or_external_artifact_metadata(self):
        self.run_js(r"""
for (const base of ['C:/private/repo/', 'https://example.test/', '/home/private/', '../\\private/', '../\nprivate/']) {
  api.ARTIFACTS.source_base = base;
  assert.equal(api.sourceHref({file:'svc.py'}), null);
}
api.ARTIFACTS.source_base = '../';
for (const file of ['../secret.py', 'C:/private.py', '/etc/passwd', 'https://example.test/x']) {
  assert.equal(api.sourceHref({file}), null);
}
api.ARTIFACTS.diff = 'https://example.test/steal'; assert.equal(api.fullDiffLink(), null);
delete api.ARTIFACTS.diff; assert.equal(api.fullDiffLink(), null);
""")

    def test_relative_diff_link_is_available_without_embedded_full_source(self):
        self.run_js(r"""
assert.equal($('fullDiff').hidden, false);
assert.equal($('fullDiff').getAttribute('href'), 'codemap.diff');
assert.match($('coverageDetails').textContent, /Keep codemap.diff beside this map/);
api.expandDiff(fixture.nodes.function);
assert.match($('diffContent').textContent, /9 more lines/);
assert.equal($('diffHandoff').querySelectorAll('a')[1].getAttribute('download'), 'codemap.diff');
""")

    def test_recorded_audio_mutes_immediately_and_resumes_at_same_position(self):
        self.run_js(r"""
api.playFrom(0); const clip = api.P.audio;
clip.currentTime = 12; api.setMuted(true);
assert.equal(clip.muted, true); assert.equal(clip.paused, false);
api.setMuted(false); assert.equal(clip.muted, false);
api.pause(); assert.equal(clip.paused, true); assert.equal(api.P.paused, true);
api.resume(); assert.equal(api.P.audio, clip); assert.equal(clip.currentTime, 12);
assert.equal(clip.paused, false);
""")

    def test_browser_speech_pause_resume_and_immediate_mute(self):
        self.run_js(r"""
api.P.browserVoice = true; api.playFrom(0);
let voice = api.P.utt;
api.pause(); assert.equal(window.speechSynthesis.pauses, 1);
api.resume(); assert.equal(window.speechSynthesis.resumes, 1); assert.equal(api.P.utt, voice);
let cancels = window.speechSynthesis.cancels;
api.setMuted(true);
assert.equal(window.speechSynthesis.cancels, cancels + 1);
assert.equal(api.P.utt, null); assert.equal(voice.onend, null);
assert.equal(typeof api.P.timerCallback, 'function');
api.pause(); const callback = api.P.timerCallback;
api.resume(); assert.equal(api.P.timerCallback, callback);
""")

    def test_player_restores_explorer_access_and_focus_when_closed(self):
        self.run_js(r"""
$('watch').focus(); api.openPlayer();
assert.equal($('app').inert, true); assert.equal(document.activeElement, $('pPlay'));
api.closePlayer(); assert.equal($('app').inert, false);
assert.equal(document.activeElement, $('watch')); assert.equal($('player').hidden, true);
""")


if __name__ == "__main__":
    unittest.main()
