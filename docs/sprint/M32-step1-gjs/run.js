// M32 step 1: the page's scheduler under gjs against the stub DOM.
// gjs -I <dir> run.js <work.html> <fixture work.json> [<old page work.html>]
const { GLib } = imports.gi;
imports.searchPath.unshift(GLib.path_get_dirname(imports.system.programInvocationName));
const D = imports.dom;
const rd = p => new TextDecoder().decode(GLib.file_get_contents(p)[1]);
const [PAGE_PATH, FIX_PATH, OLD_PATH] = ARGV;
const PAGE = rd(PAGE_PATH);
const FIX = JSON.parse(rd(FIX_PATH));
const SCRIPT = PAGE.split('<script>')[1].split('</script>')[0];
const MY = 'aaaaaaaaaaaa', NEXT = 'bbbbbbbbbbbb', OTHER = 'cccccccccccc';
const stamped = v => PAGE.replace('<meta name="kronk-page-version" content="">', `<meta name="kronk-page-version" content="${v}">`);

let failures = 0;
const ok = (cond, what) => { print(`${cond ? 'PASS' : 'FAIL'} ${what}`); if (!cond) failures++; };

// The house: 05:00 EST on 2026-11-26 is 10:00 UTC.
const SERVER0 = Date.UTC(2026, 10, 26, 10, 0, 0) / 1000;

function fixture(ts, extra) {
  const w = JSON.parse(JSON.stringify(FIX));
  w.schema = 3; w.ts = ts; w.made_at = '2026-11-26T05:00:00-05:00';
  w.house_zone = { name: 'America/New_York', utc_offset_s: -18000 };
  Object.assign(w, extra || {});
  return w;
}

// The server the stub's fetch talks to: /api/work as the route answers it,
// /api/voice_runs, and the page itself. mode: ok, 502, refused, hang, notjson.
function makeServer(B, s) {
  s.mode = s.mode || 'ok'; s.pageStatus = s.pageStatus || 200;
  s.runs = s.runs || [];
  s.counts = { work: 0, heard: 0, page: 0, unchanged: 0, full: 0 };
  s.serverNow = () => SERVER0 + (s.serverSkew || 0) + B.now / 1000;
  const later = (r, ms) => new Promise(done => { B.g.setTimeout(() => done(r), ms || 5); B.timers[B.timers.length - 1].server = true; });
  return (rec) => {
    const u = rec.url;
    if (s.mode === 'hang') return new Promise(() => {});
    if (s.mode === 'refused') return later({ reject: new TypeError('NetworkError when attempting to fetch resource.') });
    if (s.mode === '502') return later(D.response(502, '<html><body>502 Bad Gateway</body></html>'));
    if (u.startsWith('http://kronk/')) {
      s.counts.page++;
      return later(D.response(s.pageStatus, s.pageStatus === 200 ? stamped(s.pageStamp || s.version) : '<html>502</html>'));
    }
    if (u.startsWith('api/work')) {
      s.counts.work++;
      if (s.mode === 'notjson') return later(D.response(200, '<html>not json</html>'));
      const now = s.serverNow();
      const have = /[?&]have=([^&]*)/.exec(u);
      const w = s.file;
      const same = !!(have && w && Number(decodeURIComponent(have[1])) === w.ts);
      if (same) s.counts.unchanged++; else s.counts.full++;
      const age = w ? Math.round((now - w.ts) * 10) / 10 : null;
      const env = { source: { file: 'work.json', state: w ? (age > 180 ? 'stale' : 'fresh') : 'missing', age_s: age, max_age_s: 180, every_s: 60,
                              reason: w ? (age > 180 ? `stale: work.json was written ${Math.round(age)} s ago` : null) : 'work.json does not exist', schema: w ? 3 : null, schema_note: null },
                    schema_known: 3, work: same ? null : w, unchanged: same, page_version: s.version, server_s: now };
      return later(D.response(200, JSON.stringify(env)));
    }
    if (u.startsWith('api/voice_runs')) {
      s.counts.heard++;
      const q = new D.USP(u.split('?')[1] || '');
      const runs = [...s.runs].sort((a, b) => b.start_s - a.start_s || (b.run_id > a.run_id ? 1 : -1));
      const maxSeq = runs.reduce((m, r) => Math.max(m, r.seq), 0);
      const out = { source: { state: 'fresh', age_s: 10, every_s: 60, ok: true, warnings: [] },
                    summary: { household: { runs_24h: runs.length, wrong_24h: 0, error_24h: 0, newest_time: runs[0] ? runs[0].local_time : null }, as_of_s: s.serverNow() },
                    copy: { state: 'ok', rows: runs.length, built_s: SERVER0 }, runs: [], next_before: null, max_seq: maxSeq, resync: false };
      const limit = q.has('limit') ? Number(q.get('limit')) : 200;
      if (q.has('changed_after')) {
        out.runs = runs.filter(r => r.seq > Number(q.get('changed_after'))).map(r => ({ ...r, match: true }));
      } else if (limit > 0) {
        let list = runs;
        if (q.get('before')) { const [bs, bid] = q.get('before').split(','); list = runs.filter(r => r.start_s < Number(bs) || (r.start_s === Number(bs) && r.run_id < bid)); }
        out.runs = list.slice(0, limit);
        if (list.length > limit) { const l = out.runs[out.runs.length - 1]; out.next_before = `${l.start_s},${l.run_id}`; }
      }
      return later(D.response(200, JSON.stringify(out)));
    }
    return later(D.response(404, '{}'));
  };
}

let runN = 0;
function run(i) {
  runN++;
  return { run_id: `R${String(i).padStart(5, '0')}`, start_s: SERVER0 - 100000 + i * 10, seq: i, local_day: '2026-11-25',
           local_time: '2026-11-25T08:55:45-05:00', satellite_id: 'assist_satellite.x', heard_state: 'text',
           heard: `fixture words ${i}`, tier: 'local', rule: 'fixture', reply_head: `fixture reply ${i}`, reply_rest: null,
           service: 'fixture', pipeline: 'fixture', trace_id: `t${i}` };
}

// A page in a fresh browser, with its server. Returns {B, s, probe}.
function open(opts) {
  const s = Object.assign({ version: MY, file: fixture(SERVER0 - 5) }, opts.server || {});
  let B;
  B = D.makeBrowser({ html: stamped(opts.stamp || MY), net: (rec, b) => s.handler(rec, b), abort: opts.abort,
                      session: opts.session, storageThrows: opts.storageThrows, clockSkewMs: opts.clockSkewMs });
  s.handler = makeServer(B, s);
  const script = opts.script || SCRIPT;
  let probe = null;
  try { probe = D.load(B, script); } catch (e) { B.errors.push('load threw: ' + e + '\n' + e.stack); }
  return { B, s, probe };
}
const T = (B, id) => B.document.getElementById(id).textContent;
const pendingTimers = B => B.timers.length;
const badWords = txt => ['undefined', 'NaN', '[object', 'null'].filter(w => new RegExp(`(^|[^\\w/])${w.replace('[', '\\[')}`).test(txt.replace(/2>\/dev\/null/g, '')));

async function main() {
  // ── A. first load, the age counting each second, unchanged polls keep the rows ──
  for (const abort of [false, true]) {
    const { B, s, probe } = open({ abort });
    await D.advance(B, 50);
    ok(B.errors.length === 0, `A${abort ? '+abort' : ''} no script error (${B.errors.join(' | ')})`);
    const card = B.document.getElementById('under-way').firstChild;
    ok(card && /M32/.test(card.textContent), `A first answer draws Under way (${card && card.textContent.slice(0, 40)})`);
    const ages = [];
    for (let k = 0; k < 5; k++) { await D.advance(B, 1000); ages.push(T(B, 'last-updated')); }
    ok(ages.join('|') === 'data 6 s old|data 7 s old|data 8 s old|data 9 s old|data 10 s old', `A age counts each second: ${ages.join(', ')}`);
    ok(/export made Nov 26, 05:00 AM, \d+ s ago/.test(T(B, 'state-line')), `A state line in the house's time, its age counting: "${T(B, 'state-line').slice(0, 60)}"`);
    ok(B.document.getElementById('under-way').firstChild === card, 'A unchanged polls leave the rows (same node)');
    ok(s.counts.unchanged >= 1 && s.counts.full === 1, `A ?have= after the first answer: full ${s.counts.full}, unchanged ${s.counts.unchanged}`);
    ok(/Times are the house’s \(America\/New_York\)\./.test(T(B, 'src-line')), `A source line names the zone`);
    const bad = badWords(B.root.textContent);
    ok(bad.length === 0, `A no bare ${bad.join(', ') || 'undefined/NaN/[object/null'} in the page text`);
    ok(probe('live.version') === MY, 'A the page knows its version from its own bytes');
    // A change of the file is drawn within one poll.
    const t0 = B.now;
    s.file = fixture(SERVER0 + B.now / 1000 - 0.5, { title_marker: 1 });
    s.file.items.find(it => it.id === s.file.active[0]).title = '<img src=x onerror=alert(1)> changed';
    let seen = null;
    for (let k = 0; k < 10 && seen == null; k++) { await D.advance(B, 500); if (/changed/.test(T(B, 'under-way') + T(B, 'queue') + T(B, 'done'))) seen = B.now - t0; }
    ok(seen != null && seen <= 3500, `A a changed file is on the page after ${seen} ms (poll every 3 s, answer 5 ms)`);
    const imgs = B.root.querySelectorAll('img').length + B.root.querySelectorAll('script').length;
    ok(imgs === 0 && /<img src=x/.test(B.root.textContent), 'A markup in a title shows as text, no element made');
  }

  // ── A2. ten changes at different phases of the poll: time to the page ──
  {
    const { B, s } = open({ abort: true });
    await D.advance(B, 4000);
    const times = [];
    for (let k = 0; k < 10; k++) {
      await D.advance(B, 1000 + k * 317);
      const t0 = B.now;
      s.file = fixture(SERVER0 + B.now / 1000, { marker: k });
      s.file.items.find(it => it.id === s.file.active[0]).title = `changed ${k}`;
      let seen = null;
      for (let j = 0; j < 100 && seen == null; j++) { await D.advance(B, 50); if (T(B, 'under-way').includes(`changed ${k}`)) seen = B.now - t0; }
      times.push(seen);
    }
    ok(times.every(t => t != null && t <= 3100), `A2 ten changes on the page after (ms): ${times.join(', ')}`);
  }

  // ── Z. the house's zone: by name, by offset when the name is unknown ──
  for (const zone of [{ name: 'America/New_York', utc_offset_s: -18000 }, { name: 'Not/AZone', utc_offset_s: -18000 }, { name: null, utc_offset_s: -18000 }]) {
    const { B, s } = open({ abort: true, server: { file: fixture(SERVER0 - 5, { house_zone: zone, made_at: '2026-11-26T10:00:00Z' }) } });
    await D.advance(B, 100);
    ok(/export made Nov 26, 05:00 AM/.test(T(B, 'state-line')) && B.errors.length === 0,
       `Z zone ${JSON.stringify(zone)}: "${T(B, 'state-line').slice(0, 32)}", "${T(B, 'src-line').split('. ').pop()}"`);
  }

  // ── B. cuts: 502, refused, hang, not JSON; each a minute, then back ──
  for (const mode of ['502', 'refused', 'hang', 'notjson']) {
    for (const abort of [false, true]) {
      const { B, s } = open({ abort });
      await D.advance(B, 4000);
      const before = B.document.getElementById('under-way').firstChild;
      const elems0 = D.countElements(B.root);
      s.mode = mode;
      let maxInflight = 0;
      const cutAt = B.now;
      for (let k = 0; k < 60; k++) {
        await D.advance(B, 1000);
        const open_ = B.fetches.filter(f => f.url.startsWith('api/work') && !f.done).length;
        maxInflight = Math.max(maxInflight, open_);
      }
      const banner = B.document.getElementById('live-banner');
      const why = T(B, 'live-why');
      ok(!banner.hidden && /^since 05:00:0\d AM \(.+\), \d+ s ago\. Shown below: the last known state\.$/.test(why),
         `B ${mode}${abort ? '+abort' : ''}: the page says so: "NO ANSWER: ${why}"`);
      ok(B.document.getElementById('under-way').firstChild === before && before.textContent.length > 10,
         `B ${mode}${abort ? '+abort' : ''}: kept its last state (same nodes), not blank`);
      ok(B.reloads === 0 && s.counts.page === 0, `B ${mode}${abort ? '+abort' : ''}: no navigation, no page fetch`);
      if (mode === 'hang') {
        const workFetches = B.fetches.filter(f => f.url.startsWith('api/work') && f.at > cutAt).length;
        ok(workFetches <= 5, `B hang${abort ? '+abort' : ''}: ${workFetches} api/work requests in 60 s (one at a time, each cut at 10 s, the next 3 s after)`);
        ok(/no answer in 10 s/.test(why), `B hang: the reason is the time limit (${why})`);
      }
      if (abort || mode !== 'hang') ok(maxInflight <= 1, `B ${mode}${abort ? '+abort' : ''}: at most one api/work in flight (${maxInflight})`);
      else print(`NOTE B hang without AbortController: ${maxInflight} hung requests left open (the page waits on one at a time; each is given up, not cut)`);
      s.mode = 'ok';
      await D.advance(B, 31000);                  // Heard's next poll too, which redraws its own line
      ok(banner.hidden && T(B, 'last-updated').startsWith('data '), `B ${mode}${abort ? '+abort' : ''}: live again without a touch ("${T(B, 'last-updated')}")`);
      ok(B.errors.length === 0, `B ${mode}${abort ? '+abort' : ''}: no script error (${B.errors.slice(0, 2).join(' | ')})`);
      ok(Math.abs(D.countElements(B.root) - elems0) <= 0, `B ${mode}: element count the same after (${elems0} → ${D.countElements(B.root)})`);
    }
  }

  // ── B2. an hour cut (502), then back ──
  {
    const { B, s } = open({ abort: true });
    await D.advance(B, 4000);
    const before = B.document.getElementById('under-way').firstChild;
    s.mode = '502';
    await D.advance(B, 3600 * 1000);
    ok(/, 60 min ago\./.test(T(B, 'live-why')), `B2 after an hour: "${T(B, 'live-why')}"`);
    ok(B.document.getElementById('under-way').firstChild === before && B.reloads === 0, 'B2 an hour: last state kept, no navigation');
    const mine = B.timers.filter(t => !t.server).length;
    ok(mine <= 2, `B2 an hour: ${mine} page timers pending (the tick, and one request's limit when one is in flight)`);
    s.mode = 'ok';
    await D.advance(B, 4000);
    ok(B.document.getElementById('live-banner').hidden, `B2 live again without a touch, ${B.now - 3600e3 - 4000} ms after`);
  }

  // ── B3. the first poll fails: never answered ──
  {
    const { B, s } = open({ abort: true, server: { mode: 'refused' } });
    await D.advance(B, 2000);
    ok(/^none yet \(api\/work: NetworkError/.test(T(B, 'live-why')) && T(B, 'last-updated') === 'fetching…',
       `B3 never answered: "NO ANSWER: ${T(B, 'live-why')}", header "${T(B, 'last-updated')}"`);
  }

  // ── C. versions: reload only on a 200 stamped with the announced version ──
  {
    const session = (() => { const m = new Map(); return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), _m: m }; })();
    const { B, s } = open({ abort: true, session });
    await D.advance(B, 4000);
    s.version = NEXT; s.pageStamp = NEXT;
    await D.advance(B, 4000);
    ok(B.reloads === 1 && s.counts.page === 1, `C another version announced and served: reloads ${B.reloads}, page fetches ${s.counts.page}`);
    const stampAt = Number(session.getItem('kronk.work.reloaded_at_s'));
    ok(Math.abs(stampAt - s.serverNow()) < 10, `C the limit's stamp is the route's clock (${stampAt})`);
    // The reloaded page: a fresh script, the same tab's storage.
    const two = open({ abort: true, session, stamp: NEXT, server: { version: NEXT } });
    two.s.serverSkew = B.now / 1000;
    await D.advance(two.B, 4000);
    ok(two.B.reloads === 0, 'C the new page, same version: no reload');
    two.s.version = OTHER; two.s.pageStamp = OTHER;
    await D.advance(two.B, 9 * 60 * 1000);
    ok(two.B.reloads === 0 && two.s.counts.page === 0, `C a third version within ten minutes: no reload (page fetches ${two.s.counts.page})`);
    await D.advance(two.B, 2 * 60 * 1000);
    ok(two.B.reloads === 1, `C past ten minutes by the route's clock: one reload (${two.B.reloads})`);
  }
  {
    const { B, s } = open({ abort: true });
    await D.advance(B, 4000);
    s.version = NEXT; s.pageStatus = 502;
    await D.advance(B, 120 * 1000);
    ok(B.reloads === 0, `C2 another version announced, the page answers 502: no navigation (page fetches ${s.counts.page} in 120 s)`);
    ok(s.counts.page <= 5, 'C2 the page is asked at most once in 30 s');
    s.pageStatus = 200; s.pageStamp = OTHER;
    await D.advance(B, 31 * 1000);
    ok(B.reloads === 0, 'C2 a 200 stamped with a version other than the announced one: no reload');
    s.pageStamp = NEXT;
    await D.advance(B, 31 * 1000);
    ok(B.reloads === 1, 'C2 then a 200 with the announced version: one reload');
  }
  {
    const { B, s } = open({ abort: true, storageThrows: true });
    await D.advance(B, 4000);
    s.version = NEXT; s.pageStamp = NEXT;
    await D.advance(B, 60000);
    const note = B.document.getElementById('reload-note');
    ok(B.reloads === 0 && !note.hidden, `C3 no session storage: no reload, the note says so ("${note.textContent.slice(0, 60)}…")`);
  }
  {
    // A browser clock an hour wrong does not change the limit (the route's clock).
    const session = (() => { const m = new Map(); return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) }; })();
    const { B, s } = open({ abort: true, session, clockSkewMs: 3600e3 });
    await D.advance(B, 4000);
    s.version = NEXT; s.pageStamp = NEXT;
    await D.advance(B, 4000);
    ok(B.reloads === 1, 'C4 clock an hour out: the version reload still happens once');
  }

  // ── D. the daily reload, by the same path, only when nothing is under way ──
  {
    const { B, s } = open({ abort: true });
    await D.advance(B, 24 * 3600 * 1000 + 5000);
    ok(B.reloads === 0, `D a day with an item under way: no reload (${s.file.active.join(',')})`);
    s.file = fixture(SERVER0 + B.now / 1000, { active: [], under_way: [] });
    await D.advance(B, 8000);
    ok(B.reloads === 1 && s.counts.page === 1, `D a day, nothing under way: one reload by the safe path (page fetches ${s.counts.page})`);
  }

  // ── E. a browser clock an hour wrong shows the same ages ──
  {
    const texts = [];
    for (const skew of [0, 3600e3, -3600e3]) {
      const { B } = open({ abort: true, clockSkewMs: skew });
      await D.advance(B, 10500);
      texts.push(T(B, 'last-updated') + ' / ' + T(B, 'state-line').slice(0, 45));
    }
    ok(texts[0] === texts[1] && texts[0] === texts[2], `E clock skew 0, +1 h, -1 h: ${texts.join(' | ')}`);
  }

  // ── F. element and timer counts after 5 and 500 updates ──
  {
    const { B, s } = open({ abort: true });
    const counts = [];
    for (let k = 1; k <= 500; k++) {
      s.file = fixture(SERVER0 + B.now / 1000 - 1);
      await D.advance(B, 3010);
      if (k === 5 || k === 500) counts.push([D.countElements(B.root), pendingTimers(B), s.counts.full]);
    }
    ok(counts[0][0] === counts[1][0] && counts[0][1] === counts[1][1],
       `F after 5 and 500 updates: elements ${counts[0][0]} / ${counts[1][0]}, timers ${counts[0][1]} / ${counts[1][1]} (full answers ${counts[0][2]} / ${counts[1][2]})`);
  }

  // ── G. Heard on the scheduler; open through 300 new runs holds 200 rows ──
  {
    const runs = []; for (let i = 1; i <= 250; i++) runs.push(run(i));
    const { B, s, probe } = open({ abort: true, server: { runs } });
    await D.advance(B, 125 * 1000);
    ok(s.counts.heard >= 4 && s.counts.heard <= 5, `G folded, Heard asked ${s.counts.heard} times in 125 s (every 30 s from its last end)`);
    const heardBox = B.document.getElementById('heard');
    heardBox.open = true;
    await D.advance(B, 100);
    ok(probe('heard.rows.size') === 200, `G opened: ${probe('heard.rows.size')} rows`);
    for (let i = 251; i <= 550; i++) { s.runs.push(run(i)); if (i % 10 === 0) await D.advance(B, 31 * 1000); }
    await D.advance(B, 31 * 1000);
    const rows = probe('heard.rows.size'), dom = B.root.querySelectorAll('details.heard-row').length;
    const newest = probe('[...heard.rows.keys()].sort().pop()');
    ok(rows === 200 && dom === 200 && newest === 'R00550', `G through 300 new runs: ${rows} rows kept, ${dom} in the page, newest ${newest}`);
    ok(!B.document.getElementById('heard-older').hidden && /^\d+(\.\d+)?,R00351$/.test(probe('heard.nextBefore')),
       `G "show older" goes on from the oldest kept (${probe('heard.nextBefore')})`);
    B.document.getElementById('heard-older').dispatch('click');
    await D.advance(B, 100);
    ok(probe('heard.rows.size') === 400 && probe('heard.limit') === 400, `G after "show older": ${probe('heard.rows.size')} rows, limit ${probe('heard.limit')}`);
    for (let i = 551; i <= 560; i++) s.runs.push(run(i));
    await D.advance(B, 31 * 1000);
    ok(probe('heard.rows.size') === 400, `G new runs after "show older": held at ${probe('heard.rows.size')}`);
    heardBox.open = false;
    await D.advance(B, 9 * 60 * 1000);
    ok(probe('heard.rows.size') === 400, 'G folded 9 min: rows kept');
    await D.advance(B, 70 * 1000);
    ok(probe('heard.rows.size') === 0 && B.root.querySelectorAll('details.heard-row').length === 0 && probe('heard.loaded') === false,
       'G folded 10 min: rows dropped');
    heardBox.open = true;
    await D.advance(B, 100);
    ok(probe('heard.rows.size') === 200, `G opened again: the newest ${probe('heard.rows.size')}`);
    // Filter still works.
    B.document.getElementById('heard-q').value = 'words 555';
    B.document.getElementById('heard-q').dispatch('input');
    await D.advance(B, 400);
    ok(B.errors.length === 0, `G no script error (${B.errors.slice(0, 2).join(' | ')})`);
  }
  {
    // Heard fetch hangs: its own time limit, the work poll goes on.
    const { B, s } = open({ abort: true });
    await D.advance(B, 4000);
    const orig = s.handler;
    s.handler = (rec, b) => (rec.url.startsWith('api/voice_runs') ? new Promise(() => {}) : orig(rec, b));
    await D.advance(B, 70 * 1000);
    ok(/not read/.test(T(B, 'heard-line')) && /no answer in 10 s/.test(T(B, 'heard-line')), `G2 Heard hanging: "${T(B, 'heard-line')}"`);
    ok(B.document.getElementById('live-banner').hidden && T(B, 'last-updated').startsWith('data '), 'G2 the work poll is not held up');
  }

  // ── H. the old page (item-M34's) against the new route ──
  if (OLD_PATH) {
    const OLD = rd(OLD_PATH);
    const oldScript = OLD.split('<script>')[1].split('</script>')[0];
    const { B } = open({ abort: true, script: oldScript.replace(/setInterval\(/g, 'setTimeout(') });
    B.document.getElementById('beat'); // the old body is not this page's; draw into this page's body
    await D.advance(B, 100);
    ok(B.errors.length === 0 && /M32/.test(T(B, 'under-way')) && !/UNREADABLE/.test(T(B, 'banner')),
       `H item-M34's script reads the new envelope: no error, Under way drawn, banner "${T(B, 'banner').slice(0, 60)}"`);
  }

  print(`\n${failures ? 'FAILURES: ' + failures : 'all passed'}`);
}

main().catch(e => { print('HARNESS ERROR ' + e + '\n' + e.stack); failures++; });
