// A stub DOM and browser for running work.html's script under gjs (M32 step 1
// build; step 2: toggle reaches capture listeners on the ancestors, and a
// move of a node within its parent is counted in DOC.moves). Elements, text nodes, the selectors the page uses, a fake clock
// (performance.now, setTimeout), a scripted fetch, storage that can throw.
// Built from the page's own <body> markup, so element counts are real.

var VOID = new Set(['meta', 'input', 'br', 'link', 'img', 'hr']);

class Node_ {
  constructor() { this.parentNode = null; this.childNodes = []; }
  get firstChild() { return this.childNodes[0] || null; }
  get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n === DOC.root; }
  appendChild(c) { if (c.parentNode === this && DOC) DOC.moves++; if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = this; this.childNodes.push(c); return c; }
  removeChild(c) { const i = this.childNodes.indexOf(c); if (i < 0) throw new Error('not a child'); this.childNodes.splice(i, 1); c.parentNode = null; return c; }
  insertBefore(c, ref) {
    if (ref == null) return this.appendChild(c);
    if (c.parentNode === this && DOC) DOC.moves++;
    if (c.parentNode) c.parentNode.removeChild(c);
    const i = this.childNodes.indexOf(ref); if (i < 0) throw new Error('ref not a child');
    c.parentNode = this; this.childNodes.splice(i, 0, c); return c;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  get textContent() { return this.childNodes.map(c => c.textContent).join(''); }
  set textContent(v) { for (const c of this.childNodes) c.parentNode = null; this.childNodes = []; if (v !== '') this.appendChild(new Text_(String(v))); }
}
class Text_ extends Node_ {
  constructor(t) { super(); this.data = t; this.nodeType = 3; }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
}
class Element_ extends Node_ {
  constructor(tag) {
    super(); this.tagName = tag.toUpperCase(); this.nodeType = 1; this.attrs = {}; this.dataset = {}; this.style = { animation: '' };
    this.listeners = {}; this._open = false; this.hidden = false; this.value = ''; this.checked = false; this.offsetWidth = 10;
    const self = this;
    this.classList = {
      add: c => { const s = new Set(self.className.split(/\s+/).filter(x => x)); s.add(c); self.className = [...s].join(' '); },
      remove: c => { self.className = self.className.split(/\s+/).filter(x => x && x !== c).join(' '); },
      contains: c => self.className.split(/\s+/).includes(c),
    };
    this.className = '';
  }
  get id() { return this.attrs.id || ''; }
  set id(v) { this.attrs.id = String(v); }
  get open() { return this._open; }
  set open(v) { const was = this._open; this._open = !!v; if (was !== this._open && this.tagName === 'DETAILS') this.dispatch('toggle'); }
  get children() { return this.childNodes.filter(c => c.nodeType === 1); }
  getAttribute(k) { return k === 'class' ? this.className : (k in this.attrs ? this.attrs[k] : null); }
  setAttribute(k, v) { if (k === 'class') this.className = v; else this.attrs[k] = String(v); }
  set href(v) { this.attrs.href = v; } get href() { return this.attrs.href; }
  set rel(v) { this.attrs.rel = v; } get rel() { return this.attrs.rel; }
  addEventListener(t, f, capture) { (this.listeners[t] = this.listeners[t] || []).push({ f, capture: capture === true }); }
  dispatch(t) {
    const ev = { type: t, target: this };
    const up = []; for (let n = this.parentNode; n; n = n.parentNode) up.unshift(n);
    for (const n of up) for (const l of (n.listeners && n.listeners[t]) || []) if (l.capture) l.f(ev);
    for (const l of this.listeners[t] || []) l.f(ev);
  }
  getBoundingClientRect() { return { top: 0, bottom: 0 }; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    let scope = false;
    if (sel.startsWith(':scope > ')) { scope = true; sel = sel.slice(9); }
    const m = parseSel(sel);
    const out = [];
    const walk = n => { for (const c of n.children) { if (matches(c, m)) out.push(c); if (!scope) walk(c); } };
    walk(this);
    return out;
  }
}
function parseSel(sel) {
  const m = /^([a-z]*)((?:\.[\w-]+)*)(?:\[([\w-]+)(?:="([^"]*)")?\])?$/.exec(sel);
  if (!m) throw new Error('selector not in the stub: ' + sel);
  return { tag: m[1].toUpperCase(), classes: m[2].split('.').filter(x => x), attr: m[3], val: m[4] };
}
function matches(e, m) {
  if (m.tag && e.tagName !== m.tag) return false;
  for (const c of m.classes) if (!e.classList.contains(c)) return false;
  if (m.attr) {
    const v = m.attr === 'open' ? (e.open ? '' : null) : e.getAttribute(m.attr);
    if (v == null) return false;
    if (m.val != null && v !== m.val) return false;
  }
  return true;
}

// The page's markup into the stub tree: tags, attributes, text. No comments in the body.
function build(html) {
  const root = new Element_('html');
  const stack = [root];
  const re = /<(\/?)([a-zA-Z]+)([^>]*)>|([^<]+)/g;
  let m;
  while ((m = re.exec(html))) {
    if (m[4] != null) { const t = m[4].replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>'); if (t.trim()) stack[stack.length - 1].appendChild(new Text_(t)); continue; }
    const tag = m[2].toLowerCase();
    if (m[1]) { while (stack.length > 1 && stack.pop().tagName !== tag.toUpperCase()); continue; }
    const e = new Element_(tag);
    const ar = /([\w-]+)(?:="([^"]*)")?/g; let a;
    while ((a = ar.exec(m[3]))) {
      const k = a[1], v = a[2] == null ? '' : a[2];
      if (k === 'class') e.className = v; else if (k === 'hidden') e.hidden = true; else if (k === 'open') e._open = true; else e.attrs[k] = v;
    }
    stack[stack.length - 1].appendChild(e);
    if (!VOID.has(tag) && !m[3].trim().endsWith('/')) stack.push(e);
  }
  return root;
}

var DOC = null;

// gjs has none: the parts the page and the harness use.
var USP = class {
  constructor(init) {
    this.m = new Map();
    if (typeof init === 'string') {
      for (const part of init.replace(/^\?/, '').split('&')) {
        if (!part) continue;
        const [k, v = ''] = part.split('=');
        this.m.set(decodeURIComponent(k.replace(/\+/g, ' ')), decodeURIComponent(v.replace(/\+/g, ' ')));
      }
    } else if (init) for (const [k, v] of Object.entries(init)) this.m.set(k, String(v));
  }
  set(k, v) { this.m.set(k, String(v)); }
  get(k) { return this.m.has(k) ? this.m.get(k) : null; }
  has(k) { return this.m.has(k); }
  toString() { return [...this.m].map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&'); }
};

// One browser: the clock, the timers, the network, the storage, the page.
function makeBrowser(opts) {
  const B = { now: 0, timers: [], seq: 0, fetches: [], reloads: 0, errors: [], hrefSets: 0, logs: [] };
  B.clockSkewMs = opts.clockSkewMs || 0;
  const html = opts.html;
  const head = html.split('<head>')[1].split('</head>')[0].replace(/<style>[\s\S]*?<\/style>/, '');
  const body = html.split('<body>')[1].split('<script>')[0];
  const root = build('<head>' + head + '</head><body>' + body + '</body>');
  DOC = { root, moves: 0 };
  const byId = id => { let f = null; const walk = n => { for (const c of n.children) { if (c.id === id) { f = c; return; } walk(c); if (f) return; } }; walk(root); return f; };
  const docListeners = {};
  const document = {
    getElementById: id => byId(id),
    createElement: t => new Element_(t),
    createTextNode: t => new Text_(t),
    querySelector: s => root.querySelector(s),
    querySelectorAll: s => root.querySelectorAll(s),
    addEventListener: (t, f) => { (docListeners[t] = docListeners[t] || []).push(f); },
    visibilityState: 'visible',
  };
  B.document = document; B.root = root; B.docListeners = docListeners;
  const store = () => { const m = new Map(); return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), _m: m }; };
  B.local = opts.local || store();
  B.session = opts.session || store();
  const g = {
    document, console: { error: (...a) => B.errors.push(a.map(String).join(' ')), log: (...a) => B.logs.push(a.join(' ')) },
    performance: { now: () => B.now },
    setTimeout: (f, ms) => { const id = ++B.seq; B.timers.push({ id, at: B.now + Math.max(0, ms || 0), f }); return id; },
    clearTimeout: id => { B.timers = B.timers.filter(t => t.id !== id); },
    location: { href: opts.href || 'http://kronk/work', reload: () => { B.reloads++; } },
    window: { scrollBy: () => {}, innerHeight: 800 },
    URLSearchParams: USP,
    fetch: (url, init) => {
      const rec = { url: String(url), at: B.now, init, done: false };
      B.fetches.push(rec);
      return new Promise((resolve, reject) => {
        if (init && init.signal) init.signal._on = () => { rec.done = true; reject(new Error('The operation was aborted.')); };
        opts.net(rec, B).then(r => { rec.done = true; if (r.reject) reject(r.reject); else resolve(r); });
      });
    },
  };
  if (opts.abort) {
    g.AbortController = class { constructor() { const s = { aborted: false, _on: null }; this.signal = s; } abort() { this.signal.aborted = true; if (this.signal._on) this.signal._on(); } };
  }
  if (opts.storageThrows) g.sessionStorage = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  else g.sessionStorage = B.session;
  g.localStorage = B.local;
  // A Date whose clock is skewed (criterion 1f): the no-argument form and
  // Date.now; a stated time is the same instant.
  const RealDate = Date;
  if (B.clockSkewMs) {
    g.Date = class extends RealDate { constructor(...a) { if (a.length) super(...a); else super(RealDate.now() + B.clockSkewMs); } static now() { return RealDate.now() + B.clockSkewMs; } };
  }
  B.g = g;
  return B;
}

function response(status, text) {
  return { ok: status >= 200 && status < 300, status, text: () => Promise.resolve(text), json: () => Promise.resolve(JSON.parse(text)) };
}

// Run the page's script in a function scope with the stub's globals. Returns
// a probe that evaluates an expression inside that scope.
function load(B, script) {
  const names = Object.keys(B.g);
  const src = script + '\n;return (expr) => eval(expr);';
  const f = new Function(...names, src);
  return f(...names.map(n => B.g[n]));
}

async function drain() { for (let i = 0; i < 200; i++) await null; }

// Advance the fake clock by ms, running each timer when due, the promise
// jobs between.
async function advance(B, ms) {
  const end = B.now + ms;
  await drain();
  for (;;) {
    B.timers.sort((a, b) => a.at - b.at || a.id - b.id);
    const t = B.timers[0];
    if (!t || t.at > end) break;
    B.timers.shift();
    B.now = t.at;
    try { t.f(); } catch (e) { B.errors.push('timer threw: ' + e); }
    await drain();
  }
  B.now = end;
  await drain();
}

function countElements(n) { let k = 0; for (const c of n.children) k += 1 + countElements(c); return k; }
function allText(n) { return n.textContent; }
