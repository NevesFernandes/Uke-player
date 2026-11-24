/* follow.js - chord-following for the Uke player.
 *
 * mic -> spectrum -> 12-bin chroma -> best matching chord OF THIS SONG -> position in the song's chord list.
 * Only chords that appear in the song are candidates, and the position only moves to a chord that is
 * coming up (look-ahead window), so singing noise and the odd misread don't throw it around.
 *
 * Usage:
 *   const f = UkeFollow.create({ onPos(p), onStatus(s) });
 *   f.setEvents([{chord:'Am', line:3}, ...]);   await f.start();   f.stop();   f.setPos(i);
 */
(function (root) {
  const PC = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
  const pcOf = n => { const m = /^([A-G])(#|b)?$/.exec(n); return m ? (PC[m[1]] + (m[2] === '#' ? 1 : m[2] === 'b' ? -1 : 0) + 12) % 12 : null; };

  function center(v) { let m = 0; for (const x of v) m += x; m /= v.length; let n = 0; const o = v.map(x => x - m); for (const x of o) n += x * x; n = Math.sqrt(n) || 1; return o.map(x => x / n); }

  function chordTemplate(tok) {
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
    return center(t);
  }

  function create(opts) {
    opts = opts || {};
    let ev = [], pos = -1, miss = 0, pending = null, hist = [], templates = {}, cands = [];
    let ctx = null, stream = null, an = null, freq = null, td = null, timer = 0;
    let slow = 0, lastOnset = 0, pendingAt = 0, startedAt = 0, heard = '', heardSim = 0, running = false;
    const W = 8;                                              // look-ahead (events)

    function setEvents(list) {
      const same = list.length === ev.length;
      ev = list.map(e => ({ chord: e.chord, line: e.line }));
      cands = [...new Set(ev.map(e => e.chord))]; templates = {};
      for (const c of cands) templates[c] = chordTemplate(c);
      if (!same) { pos = -1; pending = null; hist = []; miss = 0; }
    }
    function emit(jump) { if (opts.onPos && pos >= 0) opts.onPos({ i: pos, chord: ev[pos].chord, line: ev[pos].line, jump: !!jump }); }
    function setPos(i) { if (i >= 0 && i < ev.length) { pos = i; miss = 0; pending = null; emit(true); } }

    function classify(chroma) {                               // chroma: centered+normalised 12-vector
      let best = null, bs = -2, second = -2;
      for (const c of cands) { const t = templates[c]; if (!t) continue; let s = 0; for (let k = 0; k < 12; k++) s += chroma[k] * t[k]; if (s > bs) { second = bs; bs = s; best = c; } else if (s > second) second = s; }
      return { chord: best, sim: bs, margin: bs - second };
    }

    function relocalise() {                                   // match the last 3 distinct chords anywhere in the song
      if (hist.length < 3) return false;
      const h = hist.slice(-3), idx = [];
      for (let i = 0; i < ev.length; i++) if (i === 0 || ev[i].chord !== ev[i - 1].chord) idx.push(i);   // distinct-chord positions
      const hits = [];
      for (let a = 2; a < idx.length; a++) if (ev[idx[a - 2]].chord === h[0] && ev[idx[a - 1]].chord === h[1] && ev[idx[a]].chord === h[2]) hits.push(idx[a]);
      if (!hits.length) return false;
      hits.sort((x, y) => Math.abs(x - pos) - Math.abs(y - pos));
      pos = hits[0]; miss = 0; pending = null; emit(true); return true;
    }

    function onChord(c, sim) {                                // one classified strum
      if (!hist.length || hist[hist.length - 1] !== c) hist.push(c);
      if (hist.length > 12) hist.shift();
      const cur = pos >= 0 ? ev[pos].chord : null;
      if (c === cur) { miss = 0; pending = null; return; }
      let j = -1;
      for (let k = pos + 1; k <= Math.min(ev.length - 1, pos + W); k++) if (ev[k].chord === c) { j = k; break; }
      if (j >= 0) {
        if (pos < 0 || sim >= 0.75 || (pending !== null && pending === j)) { pos = j; pending = null; miss = 0; emit(false); }
        else pending = j;                                     // wait for a second strum to confirm
        return;
      }
      if (++miss >= 5) relocalise();
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

    function tick() {
      if (!an) return;
      an.getFloatTimeDomainData(td);
      let s = 0; for (let k = td.length - 1024; k < td.length; k++) s += td[k] * td[k];
      const rms = Math.sqrt(s / 1024), now = performance.now(), prevSlow = slow;
      slow = slow * 0.85 + rms * 0.15;
      if (rms > 0.012 && rms > prevSlow * 1.6 && now - lastOnset > 180) { lastOnset = now; pendingAt = now + 170; }
      const ch = rms > 0.004 ? chromaNow() : null;
      if (ch && cands.length) { const r = classify(ch); heard = r.sim > 0.4 ? r.chord : ''; heardSim = r.sim; }
      else { heard = ''; heardSim = 0; }
      if (pendingAt && now >= pendingAt) {
        pendingAt = 0;
        if (ch && cands.length) { const r = classify(ch); if (r.sim > 0.45 && r.margin > 0.04) onChord(r.chord, r.sim); }
      }
      if (opts.onStatus) opts.onStatus({ heard, sim: heardSim, i: pos, rms, running: true });
    }

    async function start() {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
      ctx = new (root.AudioContext || root.webkitAudioContext)(); await ctx.resume();
      const src = ctx.createMediaStreamSource(stream);
      an = ctx.createAnalyser(); an.fftSize = 8192; an.smoothingTimeConstant = 0; src.connect(an);
      freq = new Float32Array(an.frequencyBinCount); td = new Float32Array(an.fftSize);
      slow = 0; lastOnset = 0; pendingAt = 0; startedAt = performance.now(); running = true;
      timer = setInterval(tick, 40);
    }
    function stop() {
      running = false; clearInterval(timer); timer = 0;
      if (stream) stream.getTracks().forEach(t => t.stop());
      if (ctx) ctx.close().catch(() => {});
      ctx = stream = an = null; heard = '';
    }
    return { setEvents, setPos, start, stop, pos: () => pos, state: () => ({ pos, miss, heard, heardSim, running, startedAt, hist: hist.slice(), cands }) };
  }

  root.UkeFollow = { create, chordTemplate };
})(typeof window !== 'undefined' ? window : globalThis);
