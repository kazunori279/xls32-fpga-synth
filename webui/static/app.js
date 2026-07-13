// XLS32 web front-end. Talks to the FastAPI bridge over one WebSocket: MIDI bytes up
// (on-screen / computer keyboard / Web-MIDI device), 16-bit PCM frames down (played via
// an AudioWorklet). Knobs & switches send MIDI CCs; presets send a full CC burst.

const VERSION = 'v70-label';  // bump on each front-end change; shown in the header + cache-busts the worklet
const SR = 32000;
let spec = null, ws = null, ctx = null, node = null, analyser = null;
let powered = false, framesRecv = 0, resampleRatio = 1, audioEl = null;
let localPlay = false;            // LOCAL play: server plays audio + reads MIDI on the host (low latency)
let masterVol = 64, mvolKnob = null, masterGainNode = null;   // header MASTER OUTPUT volume (final-mix gain)
const ctlEl = {};                 // id -> {set(v), get()}
const NPARTS = 4;                 // MULTITIMBRAL: 4 parts on MIDI channels 0-3
let activeCh = 0;                 // the part the KNOBS edit (edit focus, single)
let playSet = new Set([0]);       // the parts that PLAY incoming notes (layer set, any number)
let partValues = [];              // per-part control state; values -> partValues[activeCh]
let values = {};                  // id -> current raw value (alias of partValues[activeCh])
let partPreset = [];              // per-part {cat, name, index} of the last-loaded preset (for the name bar)
let globalIds = new Set();        // control ids shared by all parts (effects, LFO rate) — see spec.global
const ccById = {};                // id -> cc number
const EFFECT_IDS = ['reverb', 'room', 'chorusd', 'echod', 'dtime'];  // shared effect state saved per demo song
const activeNotes = new Map();    // note -> [channels it was triggered on] (for correct note-off)
let activeDrag = null;            // the in-progress knob/wheel drag {move(e), end()}, ended globally
let baseOct = 4, curUserSlot = 1;

window.__stats = { ctx: 'off', frames: 0, rms: 0, notes: 0, connected: false };

// ---------- MIDI out (to board via WS) ----------
function sendMidi(bytes) {
  if (ws && ws.readyState === 1) ws.send(new Uint8Array(bytes));
}
function playChans() { return playSet.size ? [...playSet] : []; }   // parts that play the note (layer)
function noteOn(n, vel = 100) {
  if (n < 0 || n > 127) return;
  const chans = playChans(); if (!chans.length) return;
  for (const ch of chans) sendMidi([0x90 | ch, n, vel]);            // stack the note across the layer
  activeNotes.set(n, chans); highlightKey(n, true);
  window.__stats.notes = activeNotes.size;
}
function noteOff(n) {
  const chans = activeNotes.get(n) || playChans();                 // off to the SAME parts it started on
  for (const ch of chans) sendMidi([0x80 | ch, n, 0]);
  activeNotes.delete(n); highlightKey(n, false);
  window.__stats.notes = activeNotes.size;
}
function sendCC(cc, val) { sendMidi([0xB0 | activeCh, cc & 0x7f, val & 0x7f]); }   // knob edits -> focused part
function sendCCch(cc, val, ch) { sendMidi([0xB0 | ch, cc & 0x7f, val & 0x7f]); }   // to a specific part
function sendPerfCC(cc, val) { for (const ch of playChans()) sendMidi([0xB0 | ch, cc & 0x7f, val & 0x7f]); }  // mod wheel etc -> layer
function sendBend(norm) {
  const b = Math.max(0, Math.min(16383, 8192 + Math.round(norm * 8191)));
  for (const ch of playChans()) sendMidi([0xE0 | ch, b & 0x7f, (b >> 7) & 0x7f]);   // bend the whole layer
}

// ---------- control state ----------
function setValue(id, v, send = true) {
  values[id] = v;
  if (globalIds.has(id)) for (const pv of partValues) pv[id] = v;   // global (fx/LFO rate): keep every part in sync
  if (ctlEl[id]) ctlEl[id].set(v);
  if (send && id in ccById) sendCC(ccById[id], v);
}
// ---------- header MASTER OUTPUT volume: scales the FINAL audio (not per-part), so it stays put
//           across demos/presets. WEB -> browser GainNode; LOCAL -> server output gain. ----------
function renderMasterVol(v) { masterVol = v; if (mvolKnob) mvolKnob.style.transform = `rotate(${-135 + (v / 127) * 270}deg)`; }
function setMasterVolume(v) {
  masterVol = v;
  const g = v / 127;                                // linear final-output gain (0..1, 127 = unity)
  if (masterGainNode) masterGainNode.gain.value = g;              // WEB: browser output gain
  fetch('/api/gain', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ gain: g }) }).catch(() => {});         // LOCAL: server output gain
  renderMasterVol(v);
}
function initMasterVol() {
  mvolKnob = document.getElementById('mvolknob'); if (!mvolKnob) return;
  mvolKnob.addEventListener('pointerdown', (e) => {
    const sy = e.clientY, sv = masterVol;
    beginDrag((ev) => { const v = Math.max(0, Math.min(127, Math.round(sv + (sy - ev.clientY) * 0.9))); if (v !== masterVol) setMasterVolume(v); });
    e.preventDefault();
  });
  mvolKnob.addEventListener('dblclick', () => setMasterVolume(127));
  renderMasterVol(masterVol);
}
function applyValues(vals, send) {
  for (const id in vals) setValue(id, vals[id], send);
}

// ---------- widgets ----------
function makeKnob(c) {
  const wrap = document.createElement('div'); wrap.className = 'ctl';
  const knob = document.createElement('div'); knob.className = 'knob';
  const label = document.createElement('div'); label.className = 'clabel'; label.textContent = c.label;
  if (c.global) { wrap.classList.add('global'); wrap.title = 'global — shared by all parts'; }
  const val = document.createElement('div'); val.className = 'cval';
  wrap.append(knob, label, val);
  const render = (v) => { knob.style.transform = `rotate(${-135 + (v / 127) * 270}deg)`; val.textContent = v; };
  ctlEl[c.id] = { set: (v) => render(v), get: () => values[c.id] };
  // Drag tracked at the document level (see beginDrag) — NOT setPointerCapture — so a missed/
  // cancelled pointerup can never leave this knob capturing every click (froze the whole UI).
  knob.addEventListener('pointerdown', (e) => {
    const sy = e.clientY, sv = values[c.id];
    beginDrag((ev) => {
      const v = Math.max(0, Math.min(127, Math.round(sv + (sy - ev.clientY) * 0.9)));
      if (v !== values[c.id]) setValue(c.id, v, true);
    });
    e.preventDefault();
  });
  knob.addEventListener('dblclick', () => setValue(c.id, c.default, true));
  return wrap;
}
function makeSelect(c) {
  const wrap = document.createElement('div'); wrap.className = 'sel sel-' + c.id;
  const segs = document.createElement('div'); segs.className = 'segs';
  const cols = c.options.length <= 3 ? c.options.length : Math.ceil(c.options.length / 2);  // ~2 rows max
  segs.dataset.cols = cols; segs.style.gridTemplateColumns = `repeat(${cols}, auto)`;
  const label = document.createElement('div'); label.className = 'clabel'; label.textContent = c.label;
  if (c.global) { wrap.classList.add('global'); wrap.title = 'global — shared by all parts'; }
  const btns = [];
  c.options.forEach((o) => {
    const b = document.createElement('div'); b.className = 'seg'; b.textContent = o.label;
    b.addEventListener('click', () => setValue(c.id, o.value, true));
    segs.append(b); btns.push({ b, v: o.value });
  });
  wrap.append(segs, label);
  ctlEl[c.id] = { set: (v) => btns.forEach((x) => x.b.classList.toggle('on', x.v === v)), get: () => values[c.id] };
  return wrap;
}
// osc/filter stay flex: a full-width break drops the next control to a new row.
// (lfo/unison/cross-mod/effects use a column grid instead — see CSS — so they don't need breaks)
const ROW_BREAK = new Set(['detune', 'fmode']);
function buildPanel() {
  globalIds = new Set(spec.controls.filter((c) => c.global).map((c) => c.id));
  const panel = document.getElementById('panel');
  spec.sections.forEach((s) => {
    const sec = document.createElement('section');
    sec.className = 'sec sec-' + s.toLowerCase().replace(/ /g, '-');
    const h = document.createElement('h3'); h.textContent = s; sec.append(h);
    const box = document.createElement('div'); box.className = 'ctrls'; sec.append(box);
    spec.controls.filter((c) => c.section === s).forEach((c) => {
      ccById[c.id] = c.cc; values[c.id] = c.default;
      // force a new row before these controls (detune+sub, filter mode, reverb+size)
      if (ROW_BREAK.has(c.id)) { const br = document.createElement('div'); br.className = 'rowbreak'; box.append(br); }
      box.append(c.kind === 'select' ? makeSelect(c) : makeKnob(c));
    });
    panel.append(sec);
  });
  applyValues(spec.defaults, false);   // reflect defaults in the UI (no send yet)
  // MULTITIMBRAL: each of the 4 parts starts as a copy of the defaults; `values` aliases the active one
  partValues = Array.from({ length: NPARTS }, () => ({ ...values }));
  values = partValues[activeCh];
  equalizeSegs();
}
// give every button group ONE fixed width (= its widest button) so siblings line up in a grid
function equalizeSegs() {
  document.querySelectorAll('.segs').forEach((segs) => {
    const btns = [...segs.querySelectorAll('.seg')];
    if (!btns.length) return;
    let w = 0;
    btns.forEach((b) => { b.style.width = ''; w = Math.max(w, b.getBoundingClientRect().width); });
    const cols = segs.dataset.cols || btns.length;
    segs.style.gridTemplateColumns = `repeat(${cols}, ${Math.ceil(w)}px)`;
  });
}
// ---------- parts (multitimbral) ----------
// Two independent things: PLAY (which parts sound the incoming note — the layer, any number) and
// EDIT FOCUS (which single part the knobs edit). Clicking a chip's name focuses it (and lights its
// PLAY so edits are audible); clicking its LED toggles play/layer membership.
function refreshPartUI() {
  document.querySelectorAll('#parts .partchip').forEach((chip, ch) => {
    chip.classList.toggle('editing', ch === activeCh);
    chip.querySelector('.partled').classList.toggle('on', playSet.has(ch));
  });
}
function postLocalChans() {
  if (!localPlay) return;
  fetch('/api/local', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ on: true, chans: [...playSet] }) }).catch(() => {});
}
function setPlay(ch, on) {
  if (on) playSet.add(ch);
  else {
    playSet.delete(ch);
    for (const [n, chans] of activeNotes) if (chans.includes(ch)) sendMidi([0x80 | ch, n, 0]);  // release held on this part
  }
  refreshPartUI(); postLocalChans();
}
function setPart(ch) {                            // edit focus (+ ensure it's audible)
  activeCh = ch;
  values = partValues[ch];                        // repoint; panel + knob sends now target this part
  for (const id in values) if (ctlEl[id]) ctlEl[id].set(values[id]);   // refresh knobs (no send)
  if (!playSet.has(ch)) playSet.add(ch), postLocalChans();  // focusing a part lights its PLAY
  refreshPartUI();
  const pp = partPreset[ch];                       // restore this part's patch name + browse position
  if (pp) { setBar(pp.cat, pp.name); curIndex = pp.index; }
  else { setBar('—', 'Init'); curIndex = -1; }
  document.querySelectorAll('#blist .bitem').forEach((el, i) => el.classList.toggle('on', i === curIndex));
}
function buildParts() {
  const box = document.getElementById('parts');
  if (!box) return;
  box.innerHTML = '';
  for (let ch = 0; ch < NPARTS; ch++) {
    const chip = document.createElement('button'); chip.className = 'partchip';
    chip.title = 'click = edit + play this part · double-click = disable';
    const led = document.createElement('span'); led.className = 'partled';
    const name = document.createElement('span'); name.className = 'partname'; name.textContent = 'Part ' + (ch + 1);
    chip.append(led, name);
    chip.addEventListener('click', () => setPart(ch));         // edit focus + play
    chip.addEventListener('dblclick', () => setPlay(ch, false));  // disable (mute + drop from layer)
    box.append(chip);
  }
  refreshPartUI();
}
function syncAllParts() {   // push every part's full patch to its channel (board <- UI on connect)
  for (let ch = 0; ch < NPARTS; ch++)
    spec.controls.forEach((c) => sendCCch(c.cc, partValues[ch][c.id], ch));
}

// ---------- presets (Serum/Vital-style browser: 128 factory by category + 128 user) ----------
const USER_SLOTS = 128;
let bank = 'factory', bcat = 'All', bquery = '', flatList = [], curIndex = -1;
function userKey(n) { return 'synth.user.' + n; }
function readUser(n) { try { return JSON.parse(localStorage.getItem(userKey(n))); } catch (e) { return null; } }
function firstEmptyUser() { for (let i = 1; i <= USER_SLOTS; i++) if (!readUser(i)) return i; return 1; }

// current bank ('user' or a source name) -> normalized list of {name, category, values, slot?, empty?}
function bankList() {
  if (bank === 'user') {
    const out = [];
    for (let i = 1; i <= USER_SLOTS; i++) {
      const s = readUser(i);
      out.push(s ? { name: s.name, category: 'User', values: s.values, slot: i }
                 : { name: 'U' + i, category: 'User', slot: i, empty: true });
    }
    return out;
  }
  return spec.factory.filter((p) => p.source === bank).map((p) => ({ ...p }));
}
function filtered() {
  const q = bquery.trim().toLowerCase();
  return bankList().filter((p) =>
    (bank === 'user' || bcat === 'All' || p.category === bcat) &&
    (!q || p.name.toLowerCase().includes(q)));
}
function setBar(cat, name) {
  document.getElementById('curcat').textContent = cat;
  document.getElementById('curname').textContent = name;
}
function selectPreset(p, list, idx) {
  if (list) { flatList = list; curIndex = idx; }
  if (p.empty) { if (p.slot) curUserSlot = p.slot; return; }   // empty user slot: just target it
  if (p.slot) curUserSlot = p.slot;
  applyValues(p.values, powered);
  setBar(p.category, p.name);
  partPreset[activeCh] = { cat: p.category, name: p.name, index: curIndex };   // remember for this part
  document.querySelectorAll('#blist .bitem').forEach((el, i) =>
    el.classList.toggle('on', i === curIndex));
}
function stepPreset(d) {
  if (!flatList.length) { flatList = filtered(); curIndex = -1; }
  const playable = flatList.filter((p) => !p.empty);
  if (!playable.length) return;
  // step within the playable subset
  let pos = playable.indexOf(flatList[curIndex]);
  pos = (pos + d + playable.length) % playable.length;
  const p = playable[pos];
  selectPreset(p, flatList, flatList.indexOf(p));
}
function renderCats() {
  const box = document.getElementById('bcats');
  box.style.display = (bank === 'user') ? 'none' : '';
  if (bank === 'user') { box.innerHTML = ''; return; }
  box.innerHTML = '';
  ['All', ...spec.categories].forEach((c) => {
    const el = document.createElement('div');
    el.className = 'bcat' + (c === bcat ? ' on' : ''); el.textContent = c;
    el.addEventListener('click', () => { bcat = c; renderCats(); renderList(); });
    box.append(el);
  });
}
function renderList() {
  const box = document.getElementById('blist'); box.innerHTML = '';
  const list = filtered();
  list.forEach((p, i) => {
    const el = document.createElement('div');
    el.className = 'bitem' + (p.empty ? ' empty' : '') +
                  (flatList[curIndex] && p === flatList[curIndex] ? ' on' : '');
    el.textContent = p.name;
    el.addEventListener('click', () => selectPreset(p, list, i));
    box.append(el);
  });
}
function openBrowser() { document.getElementById('browser').classList.remove('hidden'); renderCats(); renderList(); }
function closeBrowser() { document.getElementById('browser').classList.add('hidden'); }
const SRC_LABEL = { nsynth: 'NSynth', soundfont: 'SoundFont', freesound: 'Freesound', factory: 'Factory', fm: 'FM' };
function setBank(b) {
  bank = b; bcat = 'All';
  document.querySelectorAll('#btabs .btab').forEach((t) => t.classList.toggle('on', t.dataset.bank === b));
  renderCats(); renderList();
}
function buildTabs() {
  const box = document.getElementById('btabs'); box.innerHTML = '';
  const add = (id, label) => {
    const b = document.createElement('button'); b.className = 'btab'; b.dataset.bank = id; b.textContent = label;
    b.addEventListener('click', () => setBank(id)); box.append(b);
  };
  (spec.sources || []).forEach((s) => add(s, SRC_LABEL[s] || s.toUpperCase()));
  add('user', 'USER');
}
function buildPresets() {
  document.getElementById('browse').addEventListener('click', openBrowser);
  document.getElementById('bclose').addEventListener('click', closeBrowser);
  document.getElementById('browser').addEventListener('click', (e) => { if (e.target.id === 'browser') closeBrowser(); });
  document.getElementById('bsearch').addEventListener('input', (e) => { bquery = e.target.value; renderList(); });
  document.getElementById('prev').addEventListener('click', () => stepPreset(-1));
  document.getElementById('next').addEventListener('click', () => stepPreset(1));
  buildTabs();
  bank = (spec.sources && spec.sources[0]) || 'user';   // default to the first source bank
  setBank(bank);
  curUserSlot = firstEmptyUser();
}
function saveUser() {
  const def = curUserSlot || firstEmptyUser();
  const raw = prompt('Save current patch to USER slot (1-' + USER_SLOTS + '):', def);
  if (raw === null) return;
  const slotN = Math.max(1, Math.min(USER_SLOTS, parseInt(raw, 10) || def));
  const ex = readUser(slotN);
  const name = prompt('Patch name:', ex ? ex.name : 'User ' + slotN);
  if (name === null) return;
  const vals = {}; spec.controls.forEach((c) => vals[c.id] = values[c.id]);
  localStorage.setItem(userKey(slotN), JSON.stringify({ name, values: vals }));
  curUserSlot = slotN; setBar('User', name);
  if (bank === 'user' && !document.getElementById('browser').classList.contains('hidden')) renderList();
}

// ---------- keyboard ----------
const WHITE = [0, 2, 4, 5, 7, 9, 11], BLACK = [1, 3, 6, 8, 10];
function buildKeyboard() {
  const kb = document.getElementById('keyboard'); kb.innerHTML = '';
  const octaves = 2, semis = octaves * 12 + 1;   // +1 -> top C
  let nWhite = 0; for (let i = 0; i < semis; i++) if (WHITE.includes(i % 12)) nWhite++;
  const wpc = 100 / nWhite, bpc = wpc * 0.62;
  let wi = 0;
  for (let i = 0; i < semis; i++) {
    const isWhite = WHITE.includes(i % 12);
    const note = 12 * (baseOct + 1) + i;         // baseOct 4 -> C=60
    if (isWhite) {
      const k = document.createElement('div'); k.className = 'wkey'; k.dataset.note = note;
      kb.append(k); wi++;
    } else {
      const k = document.createElement('div'); k.className = 'bkey'; k.dataset.note = note;
      k.style.width = bpc + '%'; k.style.left = (wi * wpc - bpc / 2) + '%';
      kb.append(k);
    }
  }
  kb.querySelectorAll('.wkey,.bkey').forEach((k) => {
    const n = +k.dataset.note;
    const off = () => noteOff(n);                 // idempotent: safe to fire from several events
    k.addEventListener('pointerdown', (e) => { try { k.setPointerCapture(e.pointerId); } catch (_) {} noteOn(n); e.preventDefault(); });
    k.addEventListener('pointerup', off);
    k.addEventListener('pointercancel', off);     // touch gesture / scroll steals the pointer
    k.addEventListener('lostpointercapture', off);
    k.addEventListener('pointerleave', (e) => { if (e.buttons) off(); });
  });
}
function highlightKey(note, on) {
  const k = document.querySelector(`#keyboard [data-note="${note}"]`);
  if (k) k.classList.toggle('down', on);
}

// ---------- computer keyboard ----------
const KMAP = { a: 0, w: 1, s: 2, e: 3, d: 4, f: 5, t: 6, g: 7, y: 8, h: 9, u: 10, j: 11, k: 12, o: 13, l: 14, p: 15 };
const held = new Map();           // key char -> the exact MIDI note sent (so a later octave shift
//                                   can't make keyup release the wrong note and strand the original)
document.addEventListener('keydown', (e) => {
  if (e.repeat || e.metaKey || e.ctrlKey) return;
  const key = e.key.toLowerCase();
  if (key === 'z') { baseOct = Math.max(0, baseOct - 1); octLabel(); buildKeyboard(); return; }
  if (key === 'x') { baseOct = Math.min(8, baseOct + 1); octLabel(); buildKeyboard(); return; }
  if (key in KMAP && !held.has(key)) { const note = 12 * (baseOct + 1) + KMAP[key]; held.set(key, note); noteOn(note); e.preventDefault(); }
});
document.addEventListener('keyup', (e) => {
  const key = e.key.toLowerCase();
  if (held.has(key)) { noteOff(held.get(key)); held.delete(key); }
});
function octLabel() { document.getElementById('octlabel').textContent = 'oct ' + baseOct; }

// ---------- panic / stuck-note & stuck-drag safety net ----------
function beginDrag(move, end) { activeDrag = { move, end: end || (() => {}) }; }
function endDrag() { if (activeDrag) { const d = activeDrag; activeDrag = null; d.end(); } }
function panic() {                                  // release everything still held or dragging
  for (const n of Array.from(activeNotes.keys())) noteOff(n);
  held.clear();
  endDrag();
}
document.addEventListener('pointermove', (e) => { if (activeDrag) activeDrag.move(e); });
document.addEventListener('pointerup', endDrag);           // a release ANYWHERE ends the drag
document.addEventListener('pointercancel', endDrag);       // gesture/scroll steals the pointer
window.addEventListener('blur', panic);                    // alt-tab/focus loss: don't strand notes
document.addEventListener('visibilitychange', () => { if (document.hidden) panic(); });

// ---------- wheels ----------
function wheel(id, opts) {
  const el = document.getElementById(id), nub = el.querySelector('.wnub');
  const H = 96, nubH = 14, span = H - nubH;
  const place = (t) => { nub.style.top = (span * (1 - t)) + 'px'; };   // t 0..1 bottom..top
  place(opts.center);
  el.addEventListener('pointerdown', (e) => {
    move(e);
    beginDrag(move, () => { if (opts.spring) { place(opts.center); opts.onEnd && opts.onEnd(); } });
    e.preventDefault();
  });
  function move(e) {
    const r = el.getBoundingClientRect();
    let t = 1 - Math.max(0, Math.min(1, (e.clientY - r.top - nubH / 2) / span));
    place(t); opts.onMove(t);
  }
}
function setupWheels() {
  wheel('pitchwheel', { center: 0.5, spring: true, onMove: (t) => sendBend((t - 0.5) * 2), onEnd: () => sendBend(0) });
  wheel('modwheel', { center: 0, onMove: (t) => sendPerfCC(1, Math.round(t * 127)) });
}

// ---------- Web MIDI ----------
async function initWebMidi() {
  if (!navigator.requestMIDIAccess) return;
  try {
    const access = await navigator.requestMIDIAccess();
    access.inputs.forEach((inp) => inp.onmidimessage = (e) => {
      const d = Array.from(e.data);                  // fan voice messages out to the whole play/layer set
      if (d[0] >= 0x80 && d[0] < 0xf0) { const st = d[0] & 0xf0; for (const ch of playChans()) sendMidi([st | ch, ...d.slice(1)]); }
      else sendMidi(d);
      const [st, d1] = e.data;                       // reflect notes on the on-screen keys
      if ((st & 0xf0) === 0x90 && e.data[2] > 0) highlightKey(d1, true);
      else if ((st & 0xf0) === 0x80 || ((st & 0xf0) === 0x90)) highlightKey(d1, false);
    });
  } catch (e) { /* no Web-MIDI permission */ }
}

// ---------- audio + websocket ----------
function silentWavURL() {                          // 1 s of silence as a WAV blob (iOS unlock)
  const sr = 8000, n = sr, b = new ArrayBuffer(44 + n * 2), dv = new DataView(b);
  const w = (o, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(o + i, s.charCodeAt(i)); };
  w(0, 'RIFF'); dv.setUint32(4, 36 + n * 2, true); w(8, 'WAVE'); w(12, 'fmt ');
  dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
  dv.setUint32(24, sr, true); dv.setUint32(28, sr * 2, true); dv.setUint16(32, 2, true);
  dv.setUint16(34, 16, true); w(36, 'data'); dv.setUint32(40, n * 2, true);  // samples default 0
  return URL.createObjectURL(new Blob([b], { type: 'audio/wav' }));
}

async function startAudio() {
  ctx = new AudioContext({ sampleRate: SR });
  // iOS Safari drops the context into 'interrupted'/'suspended' (silent switch, focus loss,
  // another audio source) → silence even though the worklet keeps running. Re-resume on any
  // state change and on any user gesture (resume must run inside a gesture on iOS).
  ctx.onstatechange = () => { if (ctx.state !== 'running') ctx.resume().catch(() => {}); };
  const resume = () => {
    if (ctx && ctx.state !== 'running') ctx.resume().catch(() => {});
    if (audioEl && audioEl.paused) audioEl.play().catch(() => {});
  };
  ['pointerdown', 'touchend', 'keydown'].forEach(ev => document.addEventListener(ev, resume));
  await ctx.audioWorklet.addModule('worklet.js?' + VERSION);   // cache-bust (Safari caches worklets)
  node = new AudioWorkletNode(ctx, 'pcm-player', { outputChannelCount: [2] });
  analyser = ctx.createAnalyser(); analyser.fftSize = 1024;
  // Unity gain node (kept as an easy volume tap). The board output already saturates ≤1.0
  // and per-note levels are conservative (~0.2 peak), so no attenuation is needed.
  masterGainNode = ctx.createGain(); masterGainNode.gain.value = masterVol / 127;   // header VOL drives this
  node.connect(masterGainNode); masterGainNode.connect(analyser);
  analyser.connect(ctx.destination);              // clean output path (no MediaStream processing)
  // iOS mutes the Web Audio API on the ringer/silent switch even when 'running'. Play a
  // looping *silent* clip: that flips iOS's audio session to 'playback', so ctx.destination
  // sounds through the switch — without routing through a MediaStream (which iOS distorts with
  // voice-processing). Harmless on other browsers.
  const isIOS = /iP(ad|hone|od)/.test(navigator.userAgent) ||
                (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  if (isIOS) {
    audioEl = new Audio(silentWavURL());
    audioEl.loop = true; audioEl.setAttribute('playsinline', '');
    await audioEl.play().catch(() => {});
  }
  resampleRatio = SR / ctx.sampleRate;              // ~1 if the browser honored 32 kHz
  window.__stats.ctx = ctx.state + '@' + ctx.sampleRate;
  const buf = new Float32Array(analyser.fftSize);
  setInterval(() => {
    if (!analyser) return;
    analyser.getFloatTimeDomainData(buf);
    let s = 0; for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
    window.__stats.rms = Math.sqrt(s / buf.length);
    window.__stats.frames = framesRecv; window.__stats.ctx = ctx.state + '@' + ctx.sampleRate;
    const dbg = document.getElementById('dbg');
    if (dbg) dbg.textContent = `${ctx.state}@${ctx.sampleRate} · ws ${ws && ws.readyState === 1 ? 'up' : 'down'} · rx ${framesRecv} · rms ${window.__stats.rms.toFixed(3)}`
      + (audioEl ? ` · el ${audioEl.paused ? 'paused' : 'play'}` : '');
  }, 150);
}
function onPCM(ab) {
  framesRecv++;
  const u16 = new Uint16Array(ab);               // interleaved L,R unsigned 16-bit LE, centered 32768
  const n = u16.length >> 1;                      // stereo frames
  const L = new Float32Array(n), R = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    L[i] = (u16[2 * i]     - 32768) / 32768;
    R[i] = (u16[2 * i + 1] - 32768) / 32768;
  }
  node.port.postMessage({ L, R }, [L.buffer, R.buffer]);   // worklet: dual-ring resample → stereo
}
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';   // match page scheme
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => {                                   // clear stale board state, then sync all parts to UI
    setStatus(true);
    for (let ch = 0; ch < NPARTS; ch++) for (let n = 0; n < 128; n++) sendMidi([0x80 | ch, n, 0]);
    syncAllParts();
  };
  ws.onclose = () => { setStatus(false); if (powered) setTimeout(connectWS, 1200); };   // auto-reconnect
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
  ws.onmessage = (e) => { if (e.data instanceof ArrayBuffer) onPCM(e.data); };
}
function currentAll() { const v = {}; spec.controls.forEach((c) => v[c.id] = values[c.id]); return v; }
function setStatus(on) {
  window.__stats.connected = on;
  document.getElementById('dot').classList.toggle('on', on);
  document.getElementById('statustext').textContent = on ? 'live' : 'off';
}

function togglePower() { return powered ? powerOff() : powerOn(); }
function powerOff() {
  powered = false;
  if (masterGainNode) { try { masterGainNode.disconnect(); } catch (_) {} }   // mute browser output
  for (const n of Array.from(activeNotes.keys())) noteOff(n);                          // release held notes
  document.getElementById('power').classList.remove('on');
  setStatus(false);
}
async function powerOn() {
  if (powered) return;
  if (ctx) {                                    // already initialized -> re-power: reconnect output
    if (masterGainNode && analyser) { try { masterGainNode.connect(analyser); } catch (_) {} }
    if (ctx.state !== 'running') ctx.resume().catch(() => {});
    powered = true;
    document.getElementById('power').classList.add('on');
    setStatus(ws && ws.readyState === 1);
    return;
  }
  // Web Audio's AudioWorklet only works in a secure context (HTTPS or localhost).
  // Over plain http://<ip> it's unavailable, so surface that instead of failing silently.
  if (!window.isSecureContext || !(window.AudioContext || window.webkitAudioContext)) {
    document.getElementById('statustext').textContent = 'need https';
    alert('Audio needs a secure context. Open this page over HTTPS or localhost — e.g. an ' +
          'HTTPS hostname like https://your-host.example (or a Tailscale name) — not plain http://<ip>.');
    return;
  }
  try {
    await startAudio();        // needs the user gesture (the POWER click)
  } catch (e) {
    document.getElementById('statustext').textContent = 'audio error';
    alert('Audio init failed: ' + (e && e.message ? e.message : e));
    return;
  }
  connectWS();
  powered = true;
  document.getElementById('power').classList.add('on');
}

// ---------- demo player (4-part authored songs, played live to the board) ----------
let demos = { songs: [] }, demoIdx = -1, demoPlaying = false;
function stopDemo() {
  demoPlaying = false; demoIdx = -1;
  fetch('/api/demo_stop', { method: 'POST' }).catch(() => {});   // server sequences + does all-notes-off
  document.querySelectorAll('#demolist .bitem').forEach((el) => el.classList.remove('on'));
  const b = document.getElementById('demo'); if (b) b.textContent = '▶ DEMO';
}
async function playDemo(idx) {
  if (idx === demoIdx && demoPlaying) { stopDemo(); return; }   // toggle off if same song
  if (!powered && !localPlay) { await powerOn(); if (!powered) return; }   // WEB needs browser audio; LOCAL plays on host
  stopDemo();
  const song = demos.songs[idx]; if (!song) return;
  demoPlaying = true; demoIdx = idx;
  // the song's shared effect state (mode + reverb/room/chorus/delay); default any it omits (old songs)
  const fxState = {};
  EFFECT_IDS.forEach((id) => { fxState[id] = (song[id] != null) ? song[id] : spec.defaults[id]; });
  // load the song's 4 part patches + effects into the multitimbral editor so each PART can be tweaked live
  song.parts.forEach((p, ch) => { if (ch < NPARTS) partValues[ch] = { ...spec.defaults, ...p, ...fxState }; });
  activeCh = 0; values = partValues[0];
  for (const id in values) if (ctlEl[id]) ctlEl[id].set(values[id]);           // reflect part 1 on the panel
  refreshPartUI();
  // build the setup MIDI from the CURRENT (customized) state: shared effects + each part's patch
  const setup = [];
  EFFECT_IDS.forEach((id) => { if (id in ccById) setup.push([0xB0, ccById[id], fxState[id] & 0x7f]); });
  for (let ch = 0; ch < NPARTS; ch++)
    for (const id in partValues[ch])
      if ((id in ccById) && !globalIds.has(id)) setup.push([0xB0 | ch, ccById[id], partValues[ch][id] & 0x7f]);
  // build timed note events (ms) and hand the whole sequence to the server to play with tight timing
  const beatMs = 60000 / song.bpm, loopMs = song.bars * 4 * beatMs;
  const events = [];
  song.notes.forEach(([t, dur, ch, note, vel]) => {
    events.push([t * beatMs, [0x90 | ch, note, vel]]);
    events.push([(t + dur) * beatMs, [0x80 | ch, note, 0]]);
  });
  events.sort((a, b) => a[0] - b[0]);
  fetch('/api/demo_play', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ setup, events, loop_ms: loopMs }) }).catch(() => {});
  document.querySelectorAll('#demolist .bitem').forEach((el, k) => el.classList.toggle('on', k === idx));
  setBar(song.genre, song.name);
  const b = document.getElementById('demo'); if (b) b.textContent = '■ DEMO';
}
async function replaceDemo(idx) {
  const genre = demos.songs[idx].genre;
  const el = document.querySelectorAll('#demolist .bitem')[idx];
  const lbl = el.querySelector('.dlabel'); const prev = lbl.textContent;
  lbl.textContent = genre + ' · …';                             // server composes a fresh one (midigen / PD)
  try {
    const s = await fetch('/api/demo?genre=' + encodeURIComponent(genre) + '&seed=' + Math.floor(Math.random() * 1e9)).then((r) => r.json());
    demos.songs[idx] = s; lbl.textContent = s.genre + ' · ' + s.name;
    if (demoPlaying && demoIdx === idx) { demoIdx = -1; playDemo(idx); }   // restart with the fresh song
  } catch (e) { lbl.textContent = prev; }
}
async function saveDemoTones() {
  const btn = document.getElementById('demosave'); const label = btn.textContent;
  const flash = (t) => { btn.textContent = t; setTimeout(() => (btn.textContent = label), 1300); };
  if (demoIdx < 0 || !demos.songs[demoIdx]) { flash('play a demo first'); return; }
  const song = demos.songs[demoIdx];
  const parts = [];
  for (let ch = 0; ch < NPARTS; ch++) {
    const o = {}; spec.controls.forEach((c) => { if (!globalIds.has(c.id)) o[c.id] = partValues[ch][c.id]; });
    parts.push(o);
  }
  const fxState = {}; EFFECT_IDS.forEach((id) => { fxState[id] = partValues[activeCh][id]; });   // full effect state
  song.parts = parts; Object.assign(song, fxState);           // update in memory
  try {                                                        // persist the whole song into demos.json (single source of truth)
    const r = await fetch('/api/demo_save', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ song }) }).then((x) => x.json());
    btn.textContent = r.ok ? '✓ saved' : '✗ err';
  } catch (e) { btn.textContent = '✗ err'; }
  setTimeout(() => (btn.textContent = label), 1300);
}
function buildDemo() {
  const box = document.getElementById('demolist');
  (demos.songs || []).forEach((s, idx) => {
    const el = document.createElement('div'); el.className = 'bitem demoitem';
    const lbl = document.createElement('span'); lbl.className = 'dlabel'; lbl.textContent = s.genre + ' · ' + s.name;
    const rep = document.createElement('button'); rep.className = 'drep'; rep.textContent = '⟳';
    rep.title = 'replace with a new ' + s.genre + ' song';
    rep.addEventListener('click', (e) => { e.stopPropagation(); replaceDemo(idx); });
    el.append(lbl, rep);
    el.addEventListener('click', () => playDemo(idx));
    box.append(el);
  });
  const overlay = document.getElementById('demobox');
  document.getElementById('demo').addEventListener('click', () => overlay.classList.remove('hidden'));
  document.getElementById('democlose').addEventListener('click', () => overlay.classList.add('hidden'));
  document.getElementById('demostop').addEventListener('click', stopDemo);
  document.getElementById('demosave').addEventListener('click', saveDemoTones);
  overlay.addEventListener('click', (e) => { if (e.target.id === 'demobox') overlay.classList.add('hidden'); });
}

// ---------- play mode (WEB = browser audio over WS · LOCAL = host audio device + MIDI in) ----------
function renderPlayMode() {
  const b = document.getElementById('playmode'); if (!b) return;
  b.textContent = localPlay ? '💻 LOCAL' : '🔊 WEB';
  b.classList.toggle('local', localPlay);
}
async function setPlayMode(on) {
  const dbg = document.getElementById('dbg');
  try {
    const r = await fetch('/api/local', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ on, chans: [...playSet] }) }).then((x) => x.json());
    localPlay = !!r.on;
    renderPlayMode();
    if (on && !localPlay) {                        // requested LOCAL but the server couldn't switch
      if (dbg) { dbg.textContent = 'LOCAL failed: ' + (r.error || 'audio device unavailable'); setTimeout(() => { if (dbg.textContent.startsWith('LOCAL failed')) dbg.textContent = ''; }, 6000); }
    } else if (dbg) { dbg.textContent = ''; }
    // WEB mode needs the browser audio powered on (a user gesture) to be heard; LOCAL plays on the host.
    if (!localPlay && !powered) powerOn();
  } catch (e) { if (dbg) dbg.textContent = 'LOCAL failed: ' + e; }
}
async function setAudioOut(v) {
  const device = (v === '') ? null : parseInt(v, 10);   // '' = system default
  try {
    await fetch('/api/local', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ on: localPlay, chans: [...playSet], device }) });
  } catch (e) {}
}
async function initPlayMode() {
  const b = document.getElementById('playmode'); const sel = document.getElementById('audioout');
  if (!b) return;
  try {
    const st = await fetch('/api/local').then((x) => x.json());
    if (!st.available) { b.style.display = 'none'; if (sel) sel.style.display = 'none'; return; }   // WEB only
    localPlay = !!st.on; renderPlayMode();
    if (sel) {
      sel.innerHTML = '';
      const def = document.createElement('option'); def.value = ''; def.textContent = '🔈 Default out'; sel.append(def);
      (st.output_devices || []).forEach((d) => {
        const o = document.createElement('option'); o.value = String(d.index); o.textContent = d.name; sel.append(o);
      });
      sel.value = (st.device == null) ? '' : String(st.device);
      sel.addEventListener('change', () => setAudioOut(sel.value));
    }
    b.title = 'WEB: audio to this browser · LOCAL: host plays audio + reads MIDI ('
      + ((st.midi_inputs || []).join(', ') || 'none') + ') — lower latency';
  } catch (e) {}
  b.addEventListener('click', () => setPlayMode(!localPlay));
}

// ---------- boot ----------
async function boot() {
  document.getElementById('ver').textContent = VERSION;
  fetch('/api/demo_stop', { method: 'POST' }).catch(() => {});   // clear any demo left looping on the server
  spec = await (await fetch('/api/spec')).json();
  demos = await fetch('/demos.json?' + VERSION).then((r) => r.json()).catch(() => ({ songs: [] }));  // single source of truth (tones saved back into it)
  buildPanel(); buildParts(); buildPresets(); buildKeyboard(); setupWheels(); octLabel(); initWebMidi(); buildDemo();
  setBar('—', 'Init');
  initMasterVol();
  initPlayMode();
  document.getElementById('power').addEventListener('click', togglePower);
  document.getElementById('save').addEventListener('click', saveUser);
  document.getElementById('init').addEventListener('click', () => {
    applyValues(spec.defaults, powered); setBar('—', 'Init'); curIndex = -1;
    document.querySelectorAll('#blist .bitem.on').forEach((el) => el.classList.remove('on'));
  });
}
boot();
