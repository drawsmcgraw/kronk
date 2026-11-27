// M32 step 3: the track and its motion, under gjs against step 2's stub DOM.
// gjs run.js <work.html> <fixture work.json (schema 4)>
// The stub has no style engine: what it shows is the classes the script
// sets (which station is cur, moving, working; which row got "flash", which
// station "fade-in"), the nodes kept, the clock's text and the counts. What
// the CSS then does with them (only transform and opacity, the pulse, the
// stripe, reduced motion) is the browser's.
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

function open(file) {
  const s = { file: file || fixture(), mode: 'ok' };
  const B = D.makeBrowser({ html: stamped(PAGE), net: rec => s.handler(rec), abort: true });
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

async function main() {
  // ── 3e: the real items' tracks ──
  {
    const { B, probe } = open();
    await D.advance(B, 100);
    ok(B.errors.length === 0, `load: no script error (${B.errors.join(' | ')})`);
    const t = id => stnText(doneRow(B, id));
    ok(t('M30') === 'criteria[done] review[done] build 9[done] test 9[done pass] promote[done]', `3e M30: rounds, not extra stations: ${t('M30')}`);
    ok(t('M35') === 'criteria[done] review[done] build[done] test[done pass] promote[done]', `3e M35 (review before criteria in the list): ${t('M35')}`);
    ok(t('M31') === 'criteria[done] review skipped[skipped] build[done] test[done pass] promote[done]', `3e M31 no review: hollow: ${t('M31')}`);
    ok(/test\[done pass\]/.test(t('M36')) && /^criteria\[done\] review\[done\] build\[todo\]/.test(t('M36')),
       `3e M36: test by graded "pass" (verdict ${JSON.stringify(item(FIX, 'M36').steps.find(s => s.kind === 'test').verdict)}); its stopped build unfilled: ${t('M36')}`);
    const fake = { steps: [{ kind: 'test', state: 'finished', verdict: 'FAILS', graded: 'pass' }] };
    ok(probe('trackOf')(fake, false, true)[3].grade === 'pass', '3e graded "pass" over verdict FAILS (made-up step)');
    ok(probe('trackOf')({ steps: [{ kind: 'test', state: 'finished', verdict: 'FAILS' }] }, false, true)[3].grade === 'fail', '3e no grading: the verdict');
    const m32 = card(B, 'M32');
    ok(m32 && /build \d+\[cur moving working\]/.test(stnText(m32)), `3a real data: M32's track ${stnText(m32)}`);
    ok(m32 && m32._parts.track._agent.textContent === 'm32-build3' && !m32._parts.track._clock.hidden,
       `3a real data: M32's agent and clock "${m32 && m32._parts.track._agent.textContent} ${m32 && m32._parts.track._clock.textContent}"`);
    ok(byId(B, 'queue').children.every(n => !n._parts || !n._parts.track || !n._parts.track.childNodes.length) &&
       byId(B, 'dropped').children.every(n => !n._parts || !n._parts.track || !n._parts.track.childNodes.length), '1 no track on queued or dropped rows');
    ok(flashed(B).length === 0, `3d none on first load (${flashed(B).join(',')})`);
    ok(moving(B).length === 1, `3b one station moving (the first item under way): ${moving(B).length}`);
    const blank = ['undefined', 'NaN', '[object'].filter(x => byId(B, 'under-way').textContent.includes(x) || byId(B, 'done').textContent.includes(x));
    ok(!blank.length, `3h no "undefined", NaN or [object in Under way and Done (${blank})`);
  }

  // ── 3a, 3c, 3d: the made-up item's life, M32 kept under way behind it ──
  {
    const { B, s, probe } = open(fixture(w => withM99(w, 'criteria')));
    await D.advance(B, 100);
    const log = [];
    const at = phase => {
      const c = card(B, 'M99');
      const tr = c._parts.track;
      log.push(`${phase}: ${stnText(c)} | ${tr._agent.hidden ? 'no agent' : `${tr._agent.textContent} ${tr._clock.textContent}`} | flashed ${flashed(B).join(',') || 'none'}`);
      return c;
    };
    let c = at('criteria');
    ok(/^criteria\[cur moving\]/.test(stnText(c)) && moving(B).length === 1, `3a criteria: ${stnText(c)}`);
    ok(/\[cur working\]/.test(stnText(card(B, 'M32'))) && !stnText(card(B, 'M32')).includes('moving'), `3b the second item under way: current, still: ${stnText(card(B, 'M32'))}`);
    const expect = {
      review: /review\[cur moving working\]/, build: /review\[done\] build\[cur moving working\]/, test: /build\[done\] test\[cur moving working\]/,
      failed: /build\[done\] test\[done fail\] promote\[todo\]/, fix: /build 2\[cur moving working\] test\[done fail\]/,
      test2: /build 2\[done\] test 2\[cur moving working\]/, passed: /test 2\[done pass\] promote\[cur moving\]/,
    };
    for (const phase of Object.keys(expect)) {
      unflash(B); unfade(B);
      const before = card(B, 'M99');
      const others = new Map(rows(B).filter(n => n !== before).map(n => [n.dataset.key, n]));
      await change(B, s, w => withM99(w, phase));
      c = at(phase);
      ok(expect[phase].test(stnText(c)), `3a ${phase}: ${stnText(c)}`);
      ok(c === before && flashed(B).join(',') === 'uw:M99', `3d ${phase}: the same card, it flashed and nothing else did (${flashed(B).join(',')})`);
      ok([...others].every(([k, n]) => rows(B).includes(n)), `2b ${phase}: every other row the same node`);
      ok(moving(B).length === (phase === 'failed' ? 0 : 1), `3b ${phase}: ${phase === 'failed' ? 'nothing moving (waiting for the coordinator: no current station)' : 'one station moving'} (${moving(B).length})`);
      const fades = faded(c).join(',');
      ok(fades === (phase === 'failed' ? 'test' : phase === 'passed' ? 'test 2' : ''), `3a ${phase}: verdict faded in: ${fades || 'none'}`);
    }
    // The clock: the build's agent, started 600 s before SERVER0.
    // 3c: a pulse survives an update that leaves the track as it was.
    await change(B, s, w => withM99(w, 'build'));
    const cur = stations(card(B, 'M99'))[2];
    const clk1 = card(B, 'M99')._parts.track._clock.textContent;
    unflash(B);
    await change(B, s, w => { withM99(w, 'build'); item(w, 'M99').next_step = 'something else'; });
    const cur2 = stations(card(B, 'M99'))[2];
    const clk2 = card(B, 'M99')._parts.track._clock.textContent;
    ok(cur === cur2 && cur2.classList.contains('moving') && cur2.classList.contains('working'), `3c next_step changed: the moving station is the same node (${cur2.className})`);
    ok(clk1 !== clk2 && /^1[0-9]:\d\d$/.test(clk2), `2 the agent's time counts: ${clk1} → ${clk2}`);
    await change(B, s, w => { withM99(w, 'build'); item(w, 'M99').steps.splice(1, 0, step('other', { file: 'M99-notes.md' })); });
    ok(stations(card(B, 'M99'))[2] === cur, '3c the track drawn again (a step added): the moving station still the same node');
    // 3d: exports that change only ts.
    await change(B, s, w => withM99(w, 'build'));
    unflash(B);
    await change(B, s, w => withM99(w, 'build'));
    await change(B, s, w => withM99(w, 'build'));
    ok(flashed(B).length === 0, `3d exports that change only ts: no flash (${flashed(B).join(',')})`);
    // Promoted: the card leaves, the Done row flashes, nothing on it moves.
    unflash(B);
    await change(B, s, w => {
      const it = m99('passed');
      Object.assign(it, { state: 'promoted', stage: { stage: 'promoted', words: 'promoted', detail: null, blocked: false, by: 'promotions' }, agents: [] });
      w.items = w.items.filter(x => x.id !== 'M99'); w.items.push(it);
      w.done = [{ ...w.done[0], id: 'M99', promotion: 999, tag: 'item-M99', at: iso(SERVER0), date: '2026-11-27', notes: [] }, ...w.done];
    });
    ok(!card(B, 'M99') && flashed(B).join(',') === 'done:999:M99', `3a promoted: the card left, the Done row flashed and nothing else (${flashed(B).join(',')})`);
    ok(/build 5\[cur moving working\]/.test(stnText(card(B, 'M32'))), `3b M32 now first under way moves, and did not flash: ${stnText(card(B, 'M32'))}`);
    ok(stnText(doneRow(B, 'M99')) === 'criteria[done] review[done] build 2[done] test 2[done pass] promote[done]', `3a promoted: its Done track ${stnText(doneRow(B, 'M99'))}`);
    ok(!stnText(doneRow(B, 'M99')).includes('moving'), '3a promoted: nothing on the Done row moves');
    for (const l of log) print(`  ${l}`);
    ok(B.errors.length === 0, `walk: no script error (${B.errors.join(' | ')})`);
  }

  // ── 3b: motion stops when no poll is answered for 30 s, or the data is stale ──
  {
    const { B, s } = open();
    await D.advance(B, 4000);
    const main = B.document.querySelector('main');
    ok(!main.classList.contains('motion-still'), '3b answered and fresh: motion on');
    s.mode = '502';
    await D.advance(B, 25000);
    const at25 = main.classList.contains('motion-still');
    await D.advance(B, 10000);
    ok(!at25 && main.classList.contains('motion-still'), `3b no answer: still at 35 s, not at 25 s (${at25})`);
    s.mode = 'ok';
    await D.advance(B, 4000);
    ok(!main.classList.contains('motion-still'), '3b answered again: motion on');
    s.state = 'stale';
    await D.advance(B, 4000);
    ok(main.classList.contains('motion-still'), '3b data stale: still');
  }

  // ── 3g: counts after 500 updates equal those after 5, an agent working ──
  {
    const { B, s } = open(fixture(w => withM99(w, 'build')));
    const counts = [];
    for (let k = 1; k <= 500; k++) {
      s.file = fixture(w => { withM99(w, k % 2 ? 'build' : 'test'); item(w, 'M32').next_step = `step ${k % 7}`; });
      await D.advance(B, 3010);
      if (k === 6 || k === 500) counts.push([D.countElements(B.root), B.timers.filter(t => !t.server).length]);
    }
    ok(counts[0][0] === counts[1][0] && counts[0][1] === counts[1][1],
       `3g after 6 and 500 updates (the same phase): elements ${counts[0][0]} / ${counts[1][0]}, timers ${counts[0][1]} / ${counts[1][1]}`);
    ok(B.errors.length === 0, `3g no script error (${B.errors.slice(0, 2).join(' | ')})`);
  }

  print(failures ? `\n${failures} FAILED` : '\nall passed');
}

let done = false;
main().then(() => { done = true; }, e => { print('FAIL threw: ' + e + '\n' + e.stack); failures++; done = true; });
const loop = GLib.MainLoop.new(null, false);
GLib.idle_add(GLib.PRIORITY_DEFAULT, () => { if (done) { loop.quit(); return false; } return true; });
loop.run();
