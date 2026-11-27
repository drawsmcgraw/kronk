// M32 step 2: rows that unfold, under gjs against the stub DOM.
// gjs run.js <work.html> <fixture work.json (schema 4)> [<old page work.html>]
const { GLib } = imports.gi;
imports.searchPath.unshift(GLib.path_get_dirname(imports.system.programInvocationName));
const D = imports.dom;
const rd = p => new TextDecoder().decode(GLib.file_get_contents(p)[1]);
const [PAGE_PATH, FIX_PATH, OLD_PATH] = ARGV;
const PAGE = rd(PAGE_PATH);
const FIX = JSON.parse(rd(FIX_PATH));
const SCRIPT = PAGE.split('<script>')[1].split('</script>')[0];
const MY = 'aaaaaaaaaaaa';
const stamped = (html, v) => html.replace('<meta name="kronk-page-version" content="">', `<meta name="kronk-page-version" content="${v}">`);

let failures = 0;
const ok = (cond, what) => { print(`${cond ? 'PASS' : 'FAIL'} ${what}`); if (!cond) failures++; };

// 05:00 EST on 2026-11-26 is 10:00 UTC.
const SERVER0 = Date.UTC(2026, 10, 26, 10, 0, 0) / 1000;
let tsN = 0;
function fixture(edit) {
  const w = JSON.parse(JSON.stringify(FIX));
  w.ts = SERVER0 - 5 + (tsN++) * 0.001;
  w.made_at = '2026-11-26T05:00:00-05:00';
  w.house_zone = { name: 'America/New_York', utc_offset_s: -18000 };
  if (edit) edit(w);
  return w;
}
const item = (w, id) => w.items.find(it => it.id === id);

// Invented runs ("fixture words N"): no household speech is read.
function run(i, extra) {
  return Object.assign({ run_id: `R${String(i).padStart(5, '0')}`, start_s: SERVER0 - 100000 + i * 10, seq: i, local_day: '2026-11-26',
                         local_time: `2026-11-26T04:${String(10 + (i % 50)).padStart(2, '0')}:00-05:00`, satellite_id: 'assist_satellite.x',
                         heard_state: 'text', heard: `fixture words ${i}`, tier: 'local', rule: 'fixture', reply_head: `fixture reply ${i}`,
                         reply_rest: null, service: 'fixture', pipeline: 'fixture', trace_id: `t${i}` }, extra || {});
}

function makeServer(B, s) {
  s.mode = s.mode || 'ok';
  s.runs = s.runs || [];
  s.counts = { work: 0, heard: 0, heard1: 0 };
  const later = r => new Promise(done => B.g.setTimeout(() => done(r), 5));
  return rec => {
    const u = rec.url;
    if (s.mode === '502') return later(D.response(502, '<html>502</html>'));
    if (u.startsWith('api/work')) {
      s.counts.work++;
      const now = SERVER0 + B.now / 1000;
      const have = /[?&]have=([^&]*)/.exec(u);
      const w = s.file;
      const same = !!(have && Number(decodeURIComponent(have[1])) === w.ts);
      const env = { source: { file: 'work.json', state: 'fresh', age_s: Math.max(0, now - w.ts), max_age_s: 180, every_s: 60, reason: null,
                              schema: w.schema, schema_note: null },
                    schema_known: 4, work: same ? null : w, unchanged: same, page_version: MY, server_s: now };
      return later(D.response(200, JSON.stringify(env)));
    }
    if (u.startsWith('api/voice_runs')) {
      s.counts.heard++;
      const q = new D.USP(u.split('?')[1] || '');
      if (q.get('limit') === '1') s.counts.heard1++;
      const runs = [...s.runs].sort((a, b) => b.start_s - a.start_s);
      const out = { source: { state: 'fresh', age_s: 10, every_s: 60, ok: true, warnings: [] },
                    summary: { household: { runs_24h: runs.length, wrong_24h: 0, error_24h: 0, newest_time: runs[0] ? runs[0].local_time : null },
                               as_of_s: SERVER0 + B.now / 1000 },
                    copy: { state: 'ok', rows: runs.length, built_s: SERVER0 }, runs: [], next_before: null,
                    max_seq: runs.reduce((m, r) => Math.max(m, r.seq), 0), resync: false };
      const limit = q.has('limit') ? Number(q.get('limit')) : 200;
      if (q.has('changed_after')) out.runs = runs.filter(r => r.seq > Number(q.get('changed_after'))).map(r => ({ ...r, match: true }));
      else out.runs = runs.slice(0, limit);
      return later(D.response(200, JSON.stringify(out)));
    }
    return later(D.response(404, '{}'));
  };
}

function open(opts) {
  opts = opts || {};
  const s = Object.assign({ file: fixture() }, opts.server || {});
  const B = D.makeBrowser({ html: stamped(opts.html || PAGE, MY), net: rec => s.handler(rec), abort: true, session: opts.session,
                            storageThrows: opts.storageThrows });
  s.handler = makeServer(B, s);
  let probe = null;
  const html = opts.html || PAGE;
  try { probe = D.load(B, html.split('<script>')[1].split('</script>')[0]); } catch (e) { B.errors.push('load threw: ' + e + '\n' + e.stack); }
  return { B, s, probe };
}
const byId = (B, id) => B.document.getElementById(id);
const T = (B, id) => byId(B, id).textContent;
const badWords = txt => ['undefined', 'NaN', '[object', 'null'].filter(w => new RegExp(`(^|[^\\w/])${w.replace('[', '\\[')}`).test(txt));

// Every keyed node of the lists, by list and key.
function keyed(B) {
  const out = new Map();
  for (const id of ['under-way', 'queue', 'done', 'dropped', 'backlog', 'shipped']) {
    for (const n of byId(B, id).children) {
      if (n.dataset.key == null) continue;
      out.set(`${id}/${n.dataset.key}`, n);
      if (n._card) for (const e of n._card.children) if (e.dataset.key != null) out.set(`${id}/${n.dataset.key}/${e.dataset.key}`, e);
    }
  }
  return out;
}
// Same node, same parts' child nodes.
function partsOf(n) { return n._parts ? Object.fromEntries(Object.entries(n._parts).map(([k, p]) => [k, [...p.childNodes]])) : {}; }
const sameParts = (a, b) => Object.keys(a).every(k => a[k].length === b[k].length && a[k].every((x, i) => x === b[k][i]));

async function change(B, s, edit) {
  s.file = fixture(edit);
  await D.advance(B, 3500);
}

async function main() {
  // ── 2a: first load; what and why; folds by default ──
  {
    const { B, s } = open();
    await D.advance(B, 100);
    ok(B.errors.length === 0, `2a no script error (${B.errors.join(' | ')})`);
    const uw = byId(B, 'under-way').children;
    ok(uw.length === FIX.active.length && uw.every(n => n.open), `2a the item under way open: ${uw.map(n => `${n.dataset.key}=${n.open}`).join(', ')}`);
    const others = [...byId(B, 'queue').children, ...byId(B, 'done').children, ...byId(B, 'dropped').children].filter(n => n.tagName === 'DETAILS');
    ok(others.length > 30 && others.every(n => !n.open), `2a every other row closed (${others.length})`);
    ok(!byId(B, 'heard').open && !byId(B, 'dropped-fold').open, '2a Heard and the dropped list folded');
    // A click opens and closes a row.
    const r = others[0];
    r.open = true; const a = r.open; r.open = false;
    ok(a === true && r.open === false, '2a a row unfolds and folds again');
    // what and why equal the handoff and the source; none: title and Shipped only.
    let checked = 0, bad = [];
    for (const n of byId(B, 'done').children) {
      if (!n._parts || !n._parts.body) continue;              // a deploy with no item
      const id = n.dataset.key.split(':').pop();
      const it = item(FIX, id);
      const body = n._parts.body;
      const labels = body.children.filter(c => c.className === 'body-part').map(c => c.firstChild.textContent);
      if (it.handoff) {
        if (!labels.includes('What') || !body.textContent.includes(it.handoff.replace(/`/g, '').slice(0, 30).split('*')[0])) bad.push(`${id} what`);
      } else if (labels.includes('What')) bad.push(`${id} empty what`);
      if (it.source) { if (!labels.includes('Why')) bad.push(`${id} why`); }
      else if (labels.includes('Why')) bad.push(`${id} empty why`);
      if (!it.handoff && arr(it.shipped).length && !labels.includes('Shipped as')) bad.push(`${id} shipped`);
      if (!labels.includes('Steps')) bad.push(`${id} steps`);
      checked++;
    }
    ok(checked > 30 && !bad.length, `2a what/why/Shipped/Steps on ${checked} Done rows (${bad.join(', ') || 'all as the file says'})`);
    const m32 = uw[0];
    ok(/What/.test(m32._parts.body.textContent) && /Why/.test(m32._parts.body.textContent) && /Steps/.test(m32._parts.body.textContent),
       '2a the item under way unfolds to What, Why and Steps');
    ok(m32._parts.track.childNodes.length === 0, "2a the track's slot is there and empty (step 3)");
    // Steps: kind and round, agent, verdict or grade, times in the house's zone.
    const steps = m32._parts.body.textContent;
    ok(/criteria \(round 2\)/.test(steps) && /m32-test/.test(steps) && /PASSES/.test(steps) && /graded pass/.test(steps) && /Nov 26, 05:14 AM → 05:21 AM/.test(steps),
       `2a M32's steps: round, agent, verdict, grade, start → end in the house's zone`);
    // Open leftovers and decisions with their text.
    const m24 = [...byId(B, 'done').children].find(n => n.dataset.key && n.dataset.key.endsWith(':M24'));
    const fixM24 = item(FIX, 'M24').open_list;
    ok(m24 && fixM24.every(o => m24._parts.body.textContent.includes(o.text.slice(0, 40))), `2a M24's ${fixM24.length} open entries with their text`);
    const bw = badWords(B.root.textContent);
    ok(!bw.length, `2j no bare ${bw.join(', ') || 'undefined/NaN/[object/null'} in the page`);
  }

  // ── 2b: a change of one item's next_step; a new item queued; promoted; dropped ──
  {
    const { B, s } = open();
    await D.advance(B, 4000);
    const someDone = byId(B, 'done').children[3];
    someDone.open = true;
    const before = keyed(B);
    const parts = new Map([...before].map(([k, n]) => [k, partsOf(n)]));
    const m32 = before.get('under-way/uw:M32');
    D.DOC.moves = 0;
    await change(B, s, w => { item(w, 'M32').next_step = 'a new next step'; });
    let after = keyed(B);
    const same = [...before].filter(([k, n]) => after.get(k) === n).length;
    ok(same === before.size && after.size === before.size, `2b next_step: ${same} of ${before.size} rows the same node`);
    const unchanged = [...before].filter(([k, n]) => k !== 'under-way/uw:M32' && n._parts && sameParts(parts.get(k), partsOf(n))).length;
    const withParts = [...before].filter(([k, n]) => k !== 'under-way/uw:M32' && n._parts).length;
    ok(unchanged === withParts, `2b every other row's parts untouched (${unchanged} of ${withParts})`);
    const p0 = parts.get('under-way/uw:M32'), p1 = partsOf(m32);
    ok(sameParts({ head: p0.head, body: p0.body }, { head: p1.head, body: p1.body }) && !sameParts({ s: p0.stage }, { s: p1.stage }) && /a new next step/.test(m32.textContent),
       '2b M32: same node; its stage part drawn again, head and body not');
    ok(m32.open && someDone.open, '2b open rows still open');
    ok(D.DOC.moves === 0, `2b no node moved (${D.DOC.moves})`);

    // The edits from here on add up: each fixture is the file with all of them.
    const edits = [w => { item(w, 'M32').next_step = 'a new next step'; }];
    const applied = w => { for (const e of edits) e(w); };
    const M99 = () => ({ id: 'M99', title: 'queued `new`', state: 'queued', position: 1, steps: [], uncommitted: [], shipped: [], roadmap: [],
                         open_list: [], agents: [{ name: 'm99-review', status: 'working', role: 'review' }], stage: { stage: 'planning' } });

    // A new item queued (at the front of the queue).
    let b2 = keyed(B); D.DOC.moves = 0;
    edits.push(w => { w.items.push(M99()); w.queue = ['M99', ...w.queue]; });
    await change(B, s, applied);
    after = keyed(B);
    ok([...b2].every(([k, n]) => after.get(k) === n) && after.has('queue/q:M99'), `2b queued: every old row the same node, M99 added`);
    ok(D.DOC.moves === 0 && byId(B, 'queue').firstChild.dataset.key === 'q:M99', `2b queued: M99 first, nothing moved (${D.DOC.moves})`);
    ok(/m99-review: working/.test(after.get('queue/q:M99').textContent), "2b a queued item's agent is on its row");

    // M32 promoted: off Under way, on top of Done.
    b2 = keyed(B); D.DOC.moves = 0;
    edits.push(w => {
      item(w, 'M32').state = 'promoted'; w.active = []; w.under_way = [];
      w.done = [{ ...w.done[0], id: 'M32', promotion: 999, tag: 'item-M32-step2', at: '2026-11-27T06:00:00Z', date: '2026-11-27', notes: [] }, ...w.done];
    });
    await change(B, s, applied);
    after = keyed(B);
    const lost = [...b2].filter(([k, n]) => k !== 'under-way/uw:M32' && after.get(k) !== n).map(([k]) => k);
    ok(!lost.length && !after.has('under-way/uw:M32') && byId(B, 'done').firstChild.dataset.key === 'done:999:M32',
       `2b promoted: M32 on top of Done, every other row the same node (${lost.join(', ') || 'none lost'})`);
    ok(D.DOC.moves === 0, `2b promoted: nothing moved (${D.DOC.moves})`);

    // A queued item dropped.
    b2 = keyed(B); D.DOC.moves = 0;
    const qid = s.file.queue[1];
    edits.push(w => {
      w.queue = w.queue.filter(x => x !== qid);
      Object.assign(item(w, qid), { state: 'dropped', ended_at: '2026-11-26T11:00:00Z', position: null });
    });
    await change(B, s, applied);
    after = keyed(B);
    const lost2 = [...b2].filter(([k, n]) => k !== `queue/q:${qid}` && after.get(k) !== n).map(([k]) => k);
    ok(!lost2.length && after.has(`dropped/drop:${qid}`) && !after.has(`queue/q:${qid}`),
       `2b dropped: ${qid} off the Queue and in Dropped, every other row the same node (${lost2.join(', ') || 'none lost'})`);
    ok(D.DOC.moves === 0, `2b dropped: nothing moved (${D.DOC.moves})`);
    // A queue reordered: the fewest moves (one), every node kept.
    b2 = keyed(B); D.DOC.moves = 0;
    const q = s.file.queue.slice();
    edits.push(w => { w.queue = [...q.slice(1), q[0]]; });
    await change(B, s, applied);
    after = keyed(B);
    ok([...b2].every(([k, n]) => after.get(k) === n) && D.DOC.moves === 1 &&
       byId(B, 'queue').children.map(n => n.dataset.key).join(',') === [...q.slice(1), q[0]].map(x => `q:${x}`).join(','),
       `2b the first queued item moved to the end: ${D.DOC.moves} move, every node kept`);
    ok(B.errors.length === 0, `2b no script error (${B.errors.join(' | ')})`);
  }

  // ── 2c: folds across a reload; a new tab; Heard folded on every load ──
  {
    const session = (() => { const m = new Map(); return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), _m: m }; })();
    const first = open({ session });
    await D.advance(first.B, 100);
    const d3 = byId(first.B, 'done').children[2];
    const k3 = d3.dataset.key;
    d3.open = true;
    byId(first.B, 'under-way').firstChild.open = false;
    byId(first.B, 'dropped-fold').open = true;
    byId(first.B, 'heard').open = true;
    const be = byId(first.B, 'backlog').children[0]._card.children.find(e => e.querySelector('details'));
    be.querySelector('details').open = true;
    const beKey = be.dataset.key;
    const kept = session.getItem('kronk.work.folds');
    ok(kept && JSON.parse(kept)[k3] === true && JSON.parse(kept)['uw:M32'] === false && !/heard/.test(kept),
       `2c the tab keeps the folds that differ from their default, never Heard's (${kept})`);
    const again = open({ session });
    await D.advance(again.B, 100);
    const d3b = [...byId(again.B, 'done').children].find(n => n.dataset.key === k3);
    const be2 = byId(again.B, 'backlog').children[0]._card.children.find(e => e.dataset.key === beKey);
    ok(d3b.open && !byId(again.B, 'under-way').firstChild.open && byId(again.B, 'dropped-fold').open && be2.querySelector('details').open,
       '2c after a reload in the same tab: the folds as left');
    ok(!byId(again.B, 'heard').open, '2c Heard folded after the reload');
    // Folding a row back to its default drops it from the store.
    d3b.open = false;
    ok(!(k3 in JSON.parse(session.getItem('kronk.work.folds'))), '2c a row folded back to its default is no longer kept');
    const tab = open({});
    await D.advance(tab.B, 100);
    const d3c = [...byId(tab.B, 'done').children].find(n => n.dataset.key === k3);
    ok(!d3c.open && byId(tab.B, 'under-way').firstChild.open && !byId(tab.B, 'dropped-fold').open && !byId(tab.B, 'heard').open,
       '2c a new tab starts from the defaults');
    const none = open({ storageThrows: true });
    await D.advance(none.B, 100);
    const d = byId(none.B, 'done').children[2]; d.open = true;
    await change(none.B, none.s, w => { item(w, 'M32').next_step = 'x'; });
    ok(none.B.errors.length === 0 && d.open, '2c with no session storage: no error, folds kept while the page lives');
  }

  // ── 2d: markup in a leftover, a decision and a title is text ──
  {
    const evil = '<img src=x onerror=alert(1)> [link](https://example.invalid/) `code` *em*';
    const { B } = open({ server: { file: fixture(w => {
      item(w, 'M32').title = evil;
      item(w, 'M32').open_list = [{ id: 901, kind: 'leftover', owner: 'coordinator', blocks: false, date: '2026-11-26', text: evil }];
      item(w, 'M32').waits_on = 'drew';
      w.decisions_owed = [...w.decisions_owed, { id: 902, item: 'M32', date: '2026-11-26', opened_at: '2026-11-26T10:00:00Z', blocks: true, text: evil }];
      const q0 = item(w, w.queue[0]); q0.title = evil;
    }) } });
    await D.advance(B, 100);
    const rows = ['under-way', 'queue', 'done', 'dropped'].map(id => byId(B, id));
    const els = tag => rows.reduce((n, r) => n + r.querySelectorAll(tag).length, 0);
    ok(els('img') + els('script') === 0 && els('a') === 0, `2d no img, script or link element in the rows (a: ${els('a')})`);
    const uw = byId(B, 'under-way').firstChild;
    ok(uw._parts.body.textContent.split('<img src=x onerror=alert(1)> [link](https://example.invalid/)').length === 3,
       '2d the leftover and the decision show their markup as text');
    ok(/#902decisionDrew2026-11-26blocks the item/.test(uw._parts.body.textContent) && /Waits on Drew/.test(uw._parts.body.textContent),
       '2d the decision it waits on, with its line and text');
    ok(uw._parts.head.querySelectorAll('code').length === 1 && uw._parts.head.querySelectorAll('em').length === 1,
       '2d a title renders code and emphasis, no link');
    ok(B.errors.length === 0, `2d no script error (${B.errors.join(' | ')})`);
  }

  // ── 2e: the dropped list ──
  {
    const { B } = open();
    await D.advance(B, 100);
    ok(!byId(B, 'dropped-section').hidden && T(B, 'dropped-count') === '1 item', `2e the dropped list is there: "${T(B, 'dropped-count')}"`);
    const r = byId(B, 'dropped').firstChild;
    ok(r && /^M29/.test(r._parts.head.textContent) && r._parts.stage.textContent === 'dropped2026-11-24',
       `2e M29, its title, "dropped" and the day: "${r && r.textContent.slice(0, 80)}"`);
  }

  // ── 2g: Heard's folded line ──
  {
    const { B, s } = open({ server: { runs: [run(1), run(2)] } });
    await D.advance(B, 100);
    ok(/newest Nov 26 04:12: “fixture words 2”/.test(T(B, 'heard-line')), `2g the newest run's words and time: "${T(B, 'heard-line')}"`);
    s.runs.push(run(3, { heard_state: 'nothing_heard', heard: null }));
    const t0 = B.now; let seen = null;
    for (let k = 0; k < 40 && seen == null; k++) { await D.advance(B, 1000); if (/newest Nov 26 04:13: nothing heard/.test(T(B, 'heard-line'))) seen = B.now - t0; }
    ok(seen != null && seen <= 31000, `2g a newer run that heard nothing says so, after ${seen} ms (Heard every 30 s)`);
    s.runs.push(run(4, { heard: '<b>bold</b> words' }));
    await D.advance(B, 31000);
    ok(T(B, 'heard-line').includes('“<b>bold</b> words”') && byId(B, 'heard-line').querySelectorAll('b').length === 0, '2g the words are set as text');
    // Open, the line still follows the newest run.
    byId(B, 'heard').open = true;
    await D.advance(B, 100);
    s.runs.push(run(5));
    await D.advance(B, 31000);
    ok(/fixture words 5/.test(T(B, 'heard-line')) && byId(B, 'heard-list').querySelectorAll('details').length === 5,
       `2g open: the line follows the newest run and the list has it (${byId(B, 'heard-list').querySelectorAll('details').length} rows)`);
    ok(B.errors.length === 0, `2g no script error (${B.errors.join(' | ')})`);
  }

  // ── note 6: after a cut, Heard asks at once ──
  {
    const { B, s } = open({ server: { runs: [run(1)] } });
    await D.advance(B, 4000);
    s.mode = '502';
    await D.advance(B, 60000);
    ok(/not read/.test(T(B, 'heard-line')), `note 6 during the cut Heard says so: "${T(B, 'heard-line').slice(0, 50)}"`);
    s.mode = 'ok';
    const t0 = B.now; let back = null, heardBack = null;
    for (let k = 0; k < 40 && heardBack == null; k++) {
      await D.advance(B, 250);
      if (back == null && byId(B, 'live-banner').hidden) back = B.now - t0;
      if (!/not read/.test(T(B, 'heard-line'))) heardBack = B.now - t0;
    }
    ok(heardBack != null && heardBack - back <= 1500, `note 6 the page live after ${back} ms, Heard read again after ${heardBack} ms`);
  }

  // ── 2i: counts after 500 updates equal those after 5 ──
  {
    const { B, s } = open();
    const counts = [];
    for (let k = 1; k <= 500; k++) {
      s.file = fixture(w => { item(w, 'M32').next_step = `step ${k % 7}`; if (k % 3 === 0) w.queue = [...w.queue].reverse(); });
      await D.advance(B, 3010);
      if (k === 5 || k === 500) counts.push([D.countElements(B.root), B.timers.filter(t => !t.server).length]);
    }
    ok(counts[0][0] === counts[1][0] && counts[0][1] === counts[1][1], `2i after 5 and 500 updates: elements ${counts[0][0]} / ${counts[1][0]}, timers ${counts[0][1]} / ${counts[1][1]}`);
    ok(B.errors.length === 0, `2i no script error (${B.errors.slice(0, 2).join(' | ')})`);
  }

  // ── 2h: item-M32-step1's page reads the schema-4 file ──
  if (OLD_PATH) {
    const { B } = open({ html: rd(OLD_PATH) });
    await D.advance(B, 4000);
    ok(B.errors.length === 0 && /M32/.test(T(B, 'under-way')) && T(B, 'banner') === '', `2h item-M32-step1's page: no error, Under way drawn, banner "${T(B, 'banner')}"`);
  }

  print(failures ? `\n${failures} FAILED` : '\nall passed');
}
const arr = x => (Array.isArray(x) ? x : []);

let done = false;
main().then(() => { done = true; }, e => { print('FAIL threw: ' + e + '\n' + e.stack); failures++; done = true; });
const loop = GLib.MainLoop.new(null, false);
GLib.idle_add(GLib.PRIORITY_DEFAULT, () => { if (done) { loop.quit(); return false; } return true; });
loop.run();
