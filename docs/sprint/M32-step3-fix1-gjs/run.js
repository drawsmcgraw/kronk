// M32 step 3 fix 1 (docs/sprint/M32-step3-fix1.md): the ruling on step 3's
// test, under gjs against step 2's stub DOM. The helpers are step 3's
// harness's (../M32-step3-gjs/run.js), open() given a session store so a
// reload keeps the folds.
// gjs run.js <work.html> <fixture work.json (schema 4)>
const { GLib } = imports.gi;
const HERE = GLib.path_get_dirname(imports.system.programInvocationName);
imports.searchPath.unshift(GLib.build_filenamev([HERE, '..', 'M32-step2-gjs']));
const D = imports.dom;
const rd = p => new TextDecoder().decode(GLib.file_get_contents(p)[1]);
const [PAGE_PATH, FIX_PATH] = ARGV;
const PAGE = rd(PAGE_PATH);
const FIX = JSON.parse(rd(FIX_PATH));
const MY = 'aaaaaaaaaaaa';
const stamped = html => html.replace('<meta name="kronk-page-version" content="">', `<meta name="kronk-page-version" content="${MY}">`);
const arr = x => (Array.isArray(x) ? x : []);

let failures = 0;
const ok = (cond, what) => { print(`${cond ? 'PASS' : 'FAIL'} ${what}`); if (!cond) failures++; };

const SERVER0 = Date.UTC(2026, 10, 27, 18, 0, 0) / 1000;
const iso = s => new Date(s * 1000).toISOString().replace('.000Z', 'Z');
let tsN = 0;
function fixture(edit) {
  const w = JSON.parse(JSON.stringify(FIX));
  w.ts = SERVER0 - 5 + (tsN++) * 0.001;
  w.house_zone = { name: 'America/New_York', utc_offset_s: -18000 };
  if (edit) edit(w);
  return w;
}
const item = (w, id) => w.items.find(it => it.id === id);

// The made-up item M99, walked through its life (criterion 3a's walk).
const step = (kind, extra) => Object.assign({ file: `M99-${kind}.md`, kind, n: null, name: kind, verdicts: null, commit: 'abc',
  committed_at: null, round: 1, state: 'finished', by: 'agent', agent: null, started_at: null, ended_at: iso(SERVER0 - 3600),
  verdict: null, graded: null }, extra || {});
const STAGES = {
  criteria: { stage: 'planning', words: 'planning', detail: 'criteria written', blocked: false, by: 'files' },
  review: { stage: 'reviewing', words: 'reviewing', detail: 'reviewing, agent m99-review working', blocked: false, by: 'agents' },
  build: { stage: 'implementing', words: 'implementing', detail: 'implementing, agent m99-build working', blocked: false, by: 'agents' },
  test: { stage: 'testing', words: 'testing', detail: 'testing, agent m99-test working', blocked: false, by: 'agents' },
  failed: { stage: 'waiting', words: 'waiting for the coordinator', detail: 'test 1: FAILS, graded fail', blocked: false, by: 'files' },
  fix: { stage: 'fixing', words: 'fixing', detail: 'fixing, agent m99-fix working', blocked: false, by: 'agents' },
  test2: { stage: 'testing', words: 'testing', detail: 'testing, agent m99-test2 working', blocked: false, by: 'agents' },
  passed: { stage: 'to_promote', words: 'to promote', detail: 'test 2: PASSES, graded pass', blocked: false, by: 'files' },
};
function m99(phase) {
  const steps = [step('criteria', { by: 'coordinator' })];
  let agents = [];
  const add = (kind, name, started, extra) => {
    steps.push(step(kind, Object.assign({ agent: name, started_at: iso(SERVER0 - 600), state: started ? 'started' : 'finished',
                                          ended_at: started ? null : iso(SERVER0 - 300) }, extra || {})));
    if (started) agents = [{ name, status: 'working', role: kind === 'test' ? 'test' : kind }];
  };
  const order = ['criteria', 'review', 'build', 'test', 'failed', 'fix', 'test2', 'passed'];
  const at = order.indexOf(phase);
  if (at >= 1) add('review', 'm99-review', phase === 'review');
  if (at >= 2) add('build', 'm99-build', phase === 'build');
  if (at >= 3) add('test', 'm99-test', phase === 'test', phase === 'test' ? {} : { n: 1, verdict: 'FAILS', graded: 'fail', verdicts: [{ subject: 'item', verdict: 'FAILS' }] });
  if (at >= 5) add('fix', 'm99-fix', phase === 'fix', { n: 1 });
  if (at >= 6) add('test', 'm99-test2', phase === 'test2', phase === 'test2' ? { n: 2, round: 2 } : { n: 2, round: 2, verdict: 'PASSES', graded: 'pass', verdicts: [{ subject: 'item', verdict: 'PASSES' }] });
  return { id: 'M99', title: 'a made-up item', short_title: 'a made-up item', stage: STAGES[phase], notes: [], uncommitted: [],
           tag_ref: false, promoted: false, agents, state: 'active', tier: 2, position: null, after_item: null, blocked_by: null,
           next: false, next_step: `M99 at ${phase}`, waits_on: null, waits_note: null, held_why: null, open: {}, ended_at: null,
           steps, open_list: [], shipped: [], roadmap: [], handoff: 'made up', source: 'made up' };
}
function withM99(w, phase) {
  w.items = w.items.filter(it => it.id !== 'M99');
  const it = m99(phase);
  w.items.push(it);
  w.active = ['M99', ...w.active.filter(x => x !== 'M99')];
  w.under_way = w.active;
}

function makeServer(B, s) {
  const later = r => new Promise(done => B.g.setTimeout(() => done(r), 5));
  return rec => {
    const u = rec.url;
    if (s.mode === '502') return later(D.response(502, '<html>502</html>'));
    if (u.startsWith('api/work')) {
      const now = SERVER0 + B.now / 1000;
      const have = /[?&]have=([^&]*)/.exec(u);
      const w = s.file;
      const same = !!(have && Number(decodeURIComponent(have[1])) === w.ts);
      const env = { source: { file: 'work.json', state: s.state || 'fresh', age_s: Math.max(0, now - w.ts), max_age_s: 180, every_s: 60,
                              reason: s.state && s.state !== 'fresh' ? 'stale: old' : null, schema: w.schema, schema_note: null },
                    schema_known: 4, work: same ? null : w, unchanged: same, page_version: MY, server_s: now };
      return later(D.response(200, JSON.stringify(env)));
    }
    if (u.startsWith('api/voice_runs')) {
      return later(D.response(200, JSON.stringify({ source: { state: 'fresh', age_s: 10, every_s: 60, ok: true, warnings: [] },
        summary: { household: { runs_24h: 0, wrong_24h: 0, error_24h: 0, newest_time: null }, as_of_s: SERVER0 },
        copy: { state: 'ok', rows: 0, built_s: SERVER0 }, runs: [], next_before: null, max_seq: 0, resync: false })));
    }
    return later(D.response(404, '{}'));
  };
}

function open(file, session) {
  const s = { file: file || fixture(), mode: 'ok' };
  const B = D.makeBrowser({ html: stamped(PAGE), net: rec => s.handler(rec), abort: true, session });
  s.handler = makeServer(B, s);
  const probe = D.load(B, PAGE.split('<script>')[1].split('</script>')[0]);
  return { B, s, probe };
}
const byId = (B, id) => B.document.getElementById(id);
const rows = B => ['under-way', 'queue', 'done', 'dropped'].flatMap(id => byId(B, id).children.filter(n => n.dataset.key != null));
const stations = n => (n._parts && n._parts.track && n._parts.track._stn) || [];
// A station's label and classes (sorted, "stn" and "fade-in" left out).
const stnText = n => stations(n).map(x => `${x._label.textContent}[${x.className.split(/\s+/).filter(c => c && c !== 'stn' && c !== 'fade-in').sort().join(' ')}]`).join(' ');
const faded = n => stations(n).filter(x => x.classList.contains('fade-in')).map(x => x._label.textContent);
const unfade = B => rows(B).flatMap(n => stations(n)).forEach(x => x.classList.remove('fade-in'));
const flashed = B => rows(B).filter(n => n.classList.contains('flash')).map(n => n.dataset.key);
const unflash = B => rows(B).forEach(n => n.classList.remove('flash'));
const moving = B => rows(B).flatMap(n => stations(n)).filter(x => x.classList.contains('moving'));
const card = (B, id) => byId(B, 'under-way').children.find(n => n.dataset.key === `uw:${id}`);
const doneRow = (B, id) => byId(B, 'done').children.find(n => n.dataset.key && n.dataset.key.endsWith(`:${id}`));
async function change(B, s, edit) { s.file = fixture(edit); await D.advance(B, 3500); }


// M32 as today's export has it, its stage and agents set by the case.
const AG = (name, status, role) => ({ name, status, role });
const ST = {
  waiting: { stage: 'waiting', words: 'waiting for the coordinator', detail: 'build in, not tested', blocked: false, by: 'files' },
  fixing: { stage: 'fixing', words: 'fixing', detail: 'fixing, agent m32-fix3 working', blocked: false, by: 'agents' },
  fixBlocked: { stage: 'fixing', words: 'fixing', detail: 'fixing, agent m32-fix3 BLOCKED', blocked: true, by: 'agents' },
  planning: { stage: 'planning', words: 'planning', detail: 'criteria written', blocked: false, by: 'files' },
  testing: { stage: 'testing', words: 'testing', detail: 'testing, agent m32-test5 working', blocked: false, by: 'agents' },
};
function setM32(w, stage, agents, steps) {
  const it = item(w, 'M32');
  it.stage = ST[stage];
  it.agents = agents;
  if (steps) it.steps = steps(it.steps.map(s => ({ ...s })));
}
// M32's steps with fix 3 finished (its last round in today's file is started).
const fix3Done = steps => steps.map(s => (s.kind === 'fix' && s.state === 'started' ? { ...s, state: 'finished', ended_at: iso(SERVER0 - 60) } : s));
// Under way: M32 first, M99 second.
function both(w, m32stage, m32agents, m99phase, m99agents) {
  setM32(w, m32stage, m32agents, fix3Done);
  withM99(w, m99phase);
  if (m99agents) item(w, 'M99').agents = m99agents;
  w.active = ['M32', 'M99'];
  w.under_way = w.active;
}
const movingOf = B => moving(B).map(x => {
  const c = byId(B, 'under-way').children.find(n => stations(n).includes(x));
  return `${c.dataset.key}:${x._label.textContent}${x.classList.contains('working') ? '+stripe' : ''}`;
});

async function main() {
  // ── B1: which card moves ──
  const b1 = [
    ['M32 waiting, M99 building (agent working)', w => both(w, 'waiting', [], 'build'), ['uw:M99:build+stripe']],
    ['both worked: the first', w => both(w, 'fixing', [AG('m32-fix3', 'working', 'fix')], 'build'), ['uw:M32:build 6+stripe']],
    ['both current, neither worked: the first with a current station', w => both(w, 'planning', [], 'passed'), ['uw:M32:criteria 3']],
    ['M32 waiting, M99 at criteria (no agent): M99, the only current station', w => both(w, 'waiting', [], 'criteria'), ['uw:M99:criteria']],
    ['neither current: nothing', w => both(w, 'waiting', [], 'failed'), []],
    ['M32 blocked, M99 at criteria: M99 (note 2: a blocked station is passed over)', w => both(w, 'fixBlocked', [AG('m32-fix3', 'blocked', 'fix')], 'criteria'), ['uw:M99:criteria']],
    ['M32 blocked, M99 building: M99', w => both(w, 'fixBlocked', [AG('m32-fix3', 'blocked', 'fix')], 'build'), ['uw:M99:build+stripe']],
  ];
  {
    const { B, s } = open(fixture(w => both(w, 'waiting', [], 'build')));
    await D.advance(B, 100);
    ok(B.errors.length === 0, `load: no script error (${B.errors.join(' | ')})`);
    for (const [what, edit, want] of b1) {
      await change(B, s, edit);
      const got = movingOf(B);
      ok(JSON.stringify(got) === JSON.stringify(want), `B1 ${what}: moving ${JSON.stringify(got)}`);
      const endless = moving(B).length + moving(B).filter(x => x.classList.contains('working')).length;
      ok(endless <= 2, `B1 ${what}: endless effects at most two (${endless})`);
    }
    // The moving card changes and the other card's data did not: no flash on it.
    await change(B, s, w => both(w, 'fixing', [AG('m32-fix3', 'working', 'fix')], 'build'));
    unflash(B);
    const m99node = card(B, 'M99'), m99build = stations(m99node)[2];
    await change(B, s, w => both(w, 'waiting', [], 'build'));
    ok(JSON.stringify(movingOf(B)) === '["uw:M99:build+stripe"]' && card(B, 'M99') === m99node && stations(m99node)[2] === m99build,
       `B1 the move passes to M99's same build node: ${JSON.stringify(movingOf(B))}`);
    ok(JSON.stringify(flashed(B)) === '["uw:M32"]', `B1 only the changed card flashed, not the one that began to move: ${JSON.stringify(flashed(B))}`);
    // note 2: blocked alone, red, still.
    await change(B, s, w => { setM32(w, 'fixBlocked', [AG('m32-fix3', 'blocked', 'fix')]); w.active = ['M32']; w.under_way = w.active; w.items = w.items.filter(x => x.id !== 'M99'); });
    const bt = stnText(card(B, 'M32'));
    ok(/build 6\[blocked cur\]/.test(bt) && moving(B).length === 0, `note 2 M32 alone, its agent blocked: red, no pulse, no stripe: ${bt}`);
    await change(B, s, w => setM32(w, 'fixing', [AG('m32-fix3', 'working', 'fix')]));
    ok(/build 6\[cur moving working\]/.test(stnText(card(B, 'M32'))), `note 2 unblocked: moves again: ${stnText(card(B, 'M32'))}`);
  }

  // ── B2: an unfinished round is never drawn with an earlier grade ──
  {
    const { B, s } = open(fixture(w => { setM32(w, 'waiting', [], fix3Done); }));
    await D.advance(B, 100);
    const t0 = stnText(card(B, 'M32'));
    ok(/build 6\[done\] test 4\[done fail\]/.test(t0), `B2 baseline, every round finished: ${t0}`);
    // Test 5 started (placed before a finished step in the list, as M32's fix 3 was), the agent between turns.
    const t5 = { kind: 'test', state: 'started', n: 5, round: 5, agent: 'm32-test5', started_at: '2026-11-27T21:00:00Z', ended_at: null,
                 committed_at: null, verdict: null, graded: null, verdicts: null, file: null };
    const insertT5 = steps => { const st = fix3Done(steps); st.splice(st.length - 1, 0, t5); return st; };
    await change(B, s, w => setM32(w, 'waiting', [AG('m32-test5', 'idle', 'test')], insertT5));
    const t1 = stnText(card(B, 'M32'));
    ok(/test 5\[started\]/.test(t1) && !/test 5\[[^\]]*(pass|fail|nd|done)/.test(t1), `B2 test 5 started, not current: reached, unfinished, no grade: ${t1}`);
    ok(moving(B).length === 0, `B2 nothing moves while waiting (${moving(B).length})`);
    await change(B, s, w => setM32(w, 'testing', [AG('m32-test5', 'working', 'test')], insertT5));
    ok(/test 5\[cur moving working\]/.test(stnText(card(B, 'M32'))), `B2 the agent working again: current: ${stnText(card(B, 'M32'))}`);
    unfade(B);
    await change(B, s, w => setM32(w, 'waiting', [], steps => insertT5(steps).map(x => (x === t5 || x.n === 5 && x.kind === 'test') ? { ...x, state: 'finished', ended_at: iso(SERVER0 - 10), graded: 'pass', verdict: 'PASSES' } : x)));
    const t2 = stnText(card(B, 'M32'));
    ok(/test 5\[done pass\]/.test(t2) && faded(card(B, 'M32')).includes('test 5'), `B2 test 5 finished, graded pass: green, faded in: ${t2}`);
    // A fix started, the agent between turns: build drawn unfinished, not green.
    await change(B, s, w => setM32(w, 'waiting', [AG('m32-fix3', 'done', 'fix')]));
    const t3 = stnText(card(B, 'M32'));
    ok(/build 6\[started\]/.test(t3), `B2 fix 3 started (today's file), agent done: build unfinished: ${t3}`);
    // Done rows: unchanged.
    ok(stnText(doneRow(B, 'M30')) === 'criteria[done] review[done] build 9[done] test 9[done pass] promote[done]', `B2 M30's Done row as before: ${stnText(doneRow(B, 'M30'))}`);
  }

  // ── the first screen: the last five steps, the earlier behind one fold ──
  {
    const session = (() => { const m = new Map(); return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: k => m.delete(k), _m: m }; })();
    let { B, s } = open(fixture(), session);
    await D.advance(B, 100);
    const view = c => {
      const outer = c._parts.body.querySelectorAll('.steps')[0];
      const det = outer.children.find(n => n.tagName === 'DETAILS');
      return { shown: outer.children.filter(n => n.className === 'step').length, det,
               label: det ? det.children.find(n => n.tagName === 'SUMMARY').textContent : null,
               inner: det ? det.children.find(n => n.className === 'steps').children.length : 0 };
    };
    const total = arr(item(s.file, 'M32').steps).length + arr(item(s.file, 'M32').uncommitted).length;
    let v = view(card(B, 'M32'));
    ok(v.shown === 5 && v.det && !v.det.open && v.label === `${total - 5} earlier steps` && v.inner === total - 5,
       `fold: M32 (${total} steps) shows 5, "${v.label}" closed, ${v.inner} inside`);
    v.det.open = true;
    ok(JSON.parse(session.getItem('kronk.work.folds') || '{}')['uw:M32:steps'] === true, `fold: opened, kept: ${session.getItem('kronk.work.folds')}`);
    // A new step: the body is drawn again, the fold stays open, one more inside.
    await change(B, s, w => { const it = item(w, 'M32'); it.steps = [...it.steps, { kind: 'test', state: 'started', n: 5, agent: 'm32-test5', started_at: iso(SERVER0 - 30), file: null }]; });
    v = view(card(B, 'M32'));
    ok(v.det && v.det.open && v.shown === 5 && v.label === `${total - 4} earlier steps`, `fold: a step added, still open, "${v.label}", 5 shown`);
    ok(card(B, 'M32').open, 'fold: the card itself still open');
    // A reload in the same tab: kept.
    ({ B, s } = open(fixture(), session));
    await D.advance(B, 100);
    v = view(card(B, 'M32'));
    ok(v.det && v.det.open, 'fold: after a reload, still open');
    v.det.open = false;
    ok(!('uw:M32:steps' in JSON.parse(session.getItem('kronk.work.folds') || '{}')), `fold: closed again, back to its default, not kept: ${session.getItem('kronk.work.folds')}`);
    // A Done row shows every step; an item with five or fewer has no fold.
    const d30 = doneRow(B, 'M30');
    const d30steps = d30._parts.body.querySelectorAll('.steps')[0];
    ok(!d30steps.children.some(n => n.tagName === 'DETAILS') && d30steps.children.length === arr(item(s.file, 'M30').steps).length,
       `fold: M30's Done row shows all ${d30steps.children.length} steps, no fold`);
    await change(B, s, w => { withM99(w, 'build'); });
    v = view(card(B, 'M99'));
    ok(!v.det && v.shown === 3, `fold: M99 with 3 steps: no fold, ${v.shown} shown`);
    ok(B.errors.length === 0, `no script error (${B.errors.join(' | ')})`);
  }

  // ── S1: the clock with no recorded start ──
  {
    const { B } = open(fixture(w => setM32(w, 'fixing', [AG('m32-fix9', 'working', 'fix')])));
    await D.advance(B, 1100);
    const c = card(B, 'M32')._parts.track._clock;
    ok(c.textContent === 'start not recorded' && !c.hidden, `S1 the clock's text: "${c.textContent}" (its box is the browser's)`);
  }
  print(failures ? `\n${failures} FAILED` : '\nall passed');
}
let done = false;
main().then(() => { done = true; }, e => { print('FAIL threw: ' + e + '\n' + e.stack); failures++; done = true; });
const loop = GLib.MainLoop.new(null, false);
GLib.idle_add(GLib.PRIORITY_DEFAULT, () => { if (done) { loop.quit(); return false; } return true; });
loop.run();
