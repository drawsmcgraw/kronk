/* Kronk UI shared utilities (extracted 2026-06-12 — was copy-pasted
 * across the dashboard pages). Plain script, no modules: pages load it
 * with <script src="/static/js/kronk.js"></script> before their inline
 * code. Everything hangs off window.KronkUI. */
window.KronkUI = {
  /* fetch + JSON with a sane timeout; throws on !ok */
  async fetchJSON(url, timeoutMs = 5000) {
    const r = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
    if (!r.ok) throw new Error(`${url} -> ${r.status}`);
    return r.json();
  },

  /* run fn now and on an interval; standard dashboard cadence is 30s */
  pollEvery(fn, ms = 30000) {
    fn();
    return setInterval(fn, ms);
  },

  /* The status-dot health probe pattern: checks = [{id, url}, ...].
   * Greens/reds the dot element per probe result; tolerates missing els. */
  async checkDots(checks, timeoutMs = 3000) {
    await Promise.allSettled(checks.map(async ({ id, url }) => {
      const dot = document.getElementById(id);
      if (!dot) return;
      try {
        const r = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
        dot.classList.toggle('up', r.ok);
        dot.classList.toggle('down', !r.ok);
      } catch {
        dot.classList.add('down');
        dot.classList.remove('up');
      }
    }));
  },

  /* Chart.js destroy-before-recreate wrapper. Keeps its own registry so
   * pages stop hand-rolling `if (charts[id]) charts[id].destroy()`. */
  _charts: {},
  makeChart(canvasId, config) {
    if (this._charts[canvasId]) {
      this._charts[canvasId].destroy();
      delete this._charts[canvasId];
    }
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    this._charts[canvasId] = new Chart(ctx, config);
    return this._charts[canvasId];
  },
};
