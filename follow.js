/* follow.js - chord-following for the Uke player.
 *
 * mic -> spectrum -> 12-bin chroma (every 40 ms) -> vote between the CURRENT chord and the NEXT one or two
 * distinct chords of the song -> move forward when the next chord wins most of the last ~320 ms.
 *
 * Robustness rules (v2, 27 Nov):
 *  - continuous voting, no dependence on catching individual strums; one bad reading can't cancel a change
 *  - only compares chords that can actually come next, so look-alikes elsewhere in the song (E vs E7) don't compete
 *  - normally moves ONE chord at a time; skipping a missed chord needs twice the evidence
 *  - templates include overtones (octave, fifth, third) of each chord tone
 *  - never moves backwards; if lost, re-syncs only to a match AHEAD (last 3 sustained chords)
 *  - quiet / unclear frames (singing only, N.C.) are ignored
 *
 * Usage:
 *   const f = UkeFollow.create({ onPos(p), onStatus(s) });
 *   f.setEvents([{chord:'Am', line:3}, ...]);   await f.start();   f.stop();   f.setPos(i);
 *   f.feed(chroma)  // one 40 ms frame (centered 12-vector or null) - used internally, exposed for testing
 */
(function (root) {
  const PC = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
  const pcOf = n => { const m = /^([A-G])(#|b)?$/.exec(n); return m ? (PC[m[1]] + (m[2] === '#' ? 1 : m[2] === 'b' ? -1 : 0) + 12) % 12 : null; };

  function center(v) { let m = 0; for (const x of v) m += x; m /= v.length; let n = 0; const o = Array.from(v, x => x - m); for (const x of o) n += x * x; n = Math.sqrt(n) || 1; return o.map(x => x / n); }

  function chordWeights(tok) {                                // raw (uncentered) chord-tone weights
    const m = /^([A-G](?:#|b)?)([^/]*)(?:\/([A-G](?:#|b)?))?$/.exec(tok); if (!m) return null;
    const rootPc = pcOf(m[1]), q = m[2], bass = m[3] ? pcOf(m[3]) : null, t = new Array(12).fill(0);
    const add = (iv, w) => { const p = (rootPc + iv) % 12; t[p] = Math.max(t[p], w); };
    const minor = /^(m(?!aj)|min)/.test(q), dim = /^dim/.test(q), aug = /^aug/.test(q), sus2 = q.includes('sus2'), sus = q.includes('sus') && !sus2;
    add(0, 1);
    if (sus2) add(2, .7); else if (sus) add(5, .7); else if (minor || dim) add(3, .8); else add(4, .8);
    if (dim) add(6, .7); else if (aug) add(8, .7); else add(7, .7);
    if (/maj7|M7/.test(q)) add(11, .5); else if (/(^|[^j])7/.test(q)) add(10, .5);
    if (/6/.test(q)) add(9, .5);
    if (/9/.test(q)) add(2, .4);
    if (bass !== null) t[bass] = Math.max(t[bass], .6);
    return t;
  }
  function chordTemplate(tok) {                               // chord tones + their overtones
    const w = chordWeights(tok); if (!w) return null;
    const t = new Array(12).fill(0);
    for (let k = 0; k < 12; k++) if (w[k] > 0)
      for (const [iv, h] of [[0, 1], [0, .5], [7, .33], [0, .25], [4, .2]]) t[(k + iv) % 12] += w[k] * h;
    return center(t);
  }

  const K = 8, NEED = 5, MIN_SIM = 0.5, MIN_MARGIN = 0.03, LOST_AFTER = 3;

  function create(opts) {
    opts = opts || {};
    let ev = [], pos = -1, templates = {}, cands = [];
    let buf = [], allBuf = [], hist = [], miss = 0, lastSustained = null;
    let ctx = null, stream = null, an = null, freq = null, td = null, timer = 0, startedAt = 0, running = false, heard = '';

    function reset() { pos = -1; buf = []; allBuf = []; hist = []; miss = 0; lastSustained = null; }
    function setEvents(list) {
      const same = list.length === ev.length;
      ev = list.map(e => ({ chord: e.chord, line: e.line }));
      cands = [...new Set(ev.map(e => e.chord))]; templates = {};
      for (const c of cands) templates[c] = chordTemplate(c);
      if (!same) reset();
    }
    function emit(jump) { if (opts.onPos && pos >= 0) opts.onPos({ i: pos, chord: ev[pos].chord, line: ev[pos].line, jump: !!jump }); }
    function moveTo(i, jump) { if (i <= pos || i >= ev.length) return; pos = i; buf = []; miss = 0; emit(jump); }   // forward only
    function setPos(i) { moveTo(i, true); }

    function nextDistinct(n) {                                // next n distinct chords ahead (repeats collapsed)
      const out = []; let prev = pos >= 0 ? ev[pos].chord : null;
      for (let k = pos + 1; k < ev.length && out.length < n; k++) if (ev[k].chord !== prev) { out.push({ i: k, chord: ev[k].chord }); prev = ev[k].chord; }
      return out;
    }
    function best(chroma, list) {
      let b = null, bs = -2, second = -2;
      for (const c of list) { const t = templates[c]; if (!t) continue; let s = 0; for (let k = 0; k < 12; k++) s += chroma[k] * t[k]; if (s > bs) { second = bs; bs = s; b = c; } else if (s > second) second = s; }
      return (bs > MIN_SIM && (list.length === 1 || bs - second > MIN_MARGIN)) ? b : null;
    }
    const count = (a, x) => a.reduce((n, y) => n + (y === x), 0);

    function relocalise() {                                   // last 3 sustained chords, matched AHEAD only
      if (hist.length < 3) return false;
      const h = hist.slice(-3), idx = [];
      for (let i = 0; i < ev.length; i++) if (i === 0 || ev[i].chord !== ev[i - 1].chord) idx.push(i);
      for (let a = 2; a < idx.length; a++)
        if (idx[a] > pos && ev[idx[a - 2]].chord === h[0] && ev[idx[a - 1]].chord === h[1] && ev[idx[a]].chord === h[2]) { moveTo(idx[a], true); return true; }
      return false;
    }

    function feed(chroma) {                                   // one 40 ms frame
      const cur = pos >= 0 ? ev[pos].chord : null, nxt = nextDistinct(2);
      const local = [...new Set([...(cur ? [cur] : []), ...nxt.map(n => n.chord)])];
      const lab = chroma && local.length ? best(chroma, local) : null;
      const labAll = chroma && cands.length ? best(chroma, cands) : null;
      heard = labAll || '';
      buf.push(lab); if (buf.length > 2 * K) buf.shift();
      allBuf.push(labAll); if (allBuf.length > K) allBuf.shift();

      // lost-detection: a chord that clearly sustains but is neither here nor next
      if (labAll && count(allBuf, labAll) >= NEED && labAll !== lastSustained) {
        lastSustained = labAll;
        if (!hist.length || hist[hist.length - 1] !== labAll) { hist.push(labAll); if (hist.length > 12) hist.shift(); }
        if (labAll !== cur && !nxt.some(n => n.chord === labAll)) { if (++miss >= LOST_AFTER && relocalise()) return; }
      }

      if (!lab || lab === cur) return;
      if (nxt[0] && lab === nxt[0].chord && count(buf.slice(-K), lab) >= NEED) moveTo(nxt[0].i, false);
      else if (nxt[1] && lab === nxt[1].chord && lab !== nxt[0].chord && count(buf, lab) >= 2 * NEED) moveTo(nxt[1].i, false);   // skipped one chord
    }

    function chromaNow() {
      an.getFloatFrequencyData(freq);
      const binHz = ctx.sampleRate / an.fftSize, v = new Float32Array(12);
      for (let k = Math.ceil(130 / binHz), e = Math.floor(2000 / binHz); k <= e; k++) {
        const f = k * binHz, midi = 69 + 12 * Math.log2(f / 440), r = Math.round(midi), dev = Math.abs(midi - r);
        if (dev > 0.3) continue;
        v[((r % 12) + 12) % 12] += Math.pow(10, freq[k] / 20) * (1 - dev / 0.3);
      }
      let s = 0; for (const x of v) s += x;
      if (s < 1e-6) return null;
      return center(Array.from(v, x => x / s));
    }
    function rmsNow() {
      an.getFloatTimeDomainData(td);                          // newest 1024 samples sit at the end of the buffer
      let s = 0; for (let k = td.length - 1024; k < td.length; k++) s += td[k] * td[k]; return Math.sqrt(s / 1024);
    }
    function tick() {
      if (!an) return;
      const rms = rmsNow();
      feed(rms > 0.004 ? chromaNow() : null);
      if (opts.onStatus) opts.onStatus({ heard, i: pos, rms, running: true });
    }

    async function start() {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
      ctx = new (root.AudioContext || root.webkitAudioContext)(); await ctx.resume();
      const src = ctx.createMediaStreamSource(stream);
      an = ctx.createAnalyser(); an.fftSize = 8192; an.smoothingTimeConstant = 0; src.connect(an);
      freq = new Float32Array(an.frequencyBinCount); td = new Float32Array(an.fftSize);
      startedAt = performance.now(); running = true;
      timer = setInterval(tick, 40);
    }
    function stop() {
      running = false; clearInterval(timer); timer = 0;
      if (stream) stream.getTracks().forEach(t => t.stop());
      if (ctx) ctx.close().catch(() => {});
      ctx = stream = an = null; heard = '';
    }
    return { setEvents, setPos, feed, start, stop, pos: () => pos,
             state: () => ({ pos, miss, heard, running, startedAt, hist: hist.slice(), cands }) };
  }

  root.UkeFollow = { create, chordTemplate };
})(typeof window !== 'undefined' ? window : globalThis);
