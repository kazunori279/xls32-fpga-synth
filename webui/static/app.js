// XLS32 web front-end. Talks to the board directly — no server: MIDI bytes out (on-screen /
// computer keyboard / Web-MIDI device) and audio back in, over Web MIDI + UAC2 on the Tiliqua or
// Web Serial on the Basys 3. See transport.js. Knobs & switches send MIDI CCs; presets send a
// full CC burst; the DEMO player sequences songs here rather than in a Python thread.

const VERSION = 'v91-panic';  // bump on each front-end change; shown in the header + cache-busts the worklet
window.VERSION = VERSION;          // transport.js cache-busts the worklet with it too
let SR = 32000;                   // frame rate on the wire; the transport sets it on connect
                                  // (Basys 3 32 kHz, Tiliqua 48 kHz — see M27). The engine ticks at
                                  // 32 kHz on both; this is the interface rate the board pushes at.
let spec = null, link = null, ctx = null, node = null, analyser = null;
let links = [];                   // every connected board, in board order; `link` is links[0]
let audioNodes = [];              // one per board that gave us a capture device (see startAudio)
let powered = false, audioEl = null;
let masterVol = 64, mvolKnob = null, masterGainNode = null;   // header MASTER OUTPUT volume (final-mix gain)
const ctlEl = {};                 // id -> {set(v), get()}
// MULTITIMBRAL, across as many boards as are plugged in. One board is 4 parts on MIDI channels 0-3,
// because the engine folds the channel down to two bits (`ch = ps[0:2]`, core/synth.x). Four boards
// on four USB cables are 16 parts and 128 voices, and the only thing that knows it is this file:
// each board is sent channels 0-3 on its own cable and cannot tell it has neighbours.
const PPB = 4;                    // parts per board — the engine's two channel bits
let NBOARDS = 1;                  // set from the transports at POWER, guessed at boot
let NPARTS = PPB;                 // = NBOARDS * PPB. Part numbers below are always GLOBAL (0..15)
let activePart = 0;                 // the PRIMARY selected part: the one the knobs edit
let selSet = new Set([0]);        // every selected part = what live notes play (⇧-click a chip to layer)
let playSet = new Set([0]);       // parts whose LED is lit = the parts a DEMO sounds (the mute set)
let partValues = [];              // per-part control state; values -> partValues[activePart]
let values = {};                  // id -> current raw value (alias of partValues[activePart])
let partPreset = [];              // per-part {cat, name, index} of the last-loaded preset (for the name bar)
let globalIds = new Set();        // control ids shared by all parts (effects, LFO rate) — see spec.global
const ccById = {};                // id -> cc number
const EFFECT_IDS = ['reverb', 'room', 'chorusd', 'echod', 'dtime'];  // shared effect state saved per demo song
const activeNotes = new Map();    // note -> [channels it was triggered on] (for correct note-off)
let activeDrag = null;            // the in-progress knob/wheel drag {move(e), end()}, ended globally
let baseOct = 4, curUserSlot = 1;

window.__stats = { ctx: 'off', frames: 0, rms: 0, notes: 0, connected: false };

// ---------- MIDI out (straight to the boards) ----------
// Every byte this page emits goes through here, and here is the only place that knows a part
// number is not a MIDI channel. Part p lives on board `p >> 2` as that board's channel `p & 3`:
// the caller writes the part into the status byte as if there were one board, and the low nibble
// is rewritten on the way out. Callers therefore never index `links` themselves.
//
// `when` is a performance.now() timestamp and only the sequencer passes one; everything driven by
// a finger wants the byte gone now. Silently dropped before a board is chosen, which is the same
// thing the closed WebSocket used to do and is what makes every caller here gate-free.
function sendToBoard(b, bytes, when) {
  const l = links[b];
  if (l) l.sendMidi(bytes, when);
}
function sendPart(p, bytes, when) {                // p is a GLOBAL part number, 0..NPARTS-1
  sendToBoard(p >> 2, [(bytes[0] & 0xf0) | (p & 3), ...bytes.slice(1)], when);
}
function sendAll(bytes, when) {                    // the same message to every board, verbatim
  for (let b = 0; b < NBOARDS; b++) sendToBoard(b, bytes, when);
}
function noteParts() { return selSet.size ? [...selSet] : [activePart]; }   // LIVE notes -> the selected part(s)
function noteOn(n, vel = 100) {
  if (n < 0 || n > 127) return;
  const parts = noteParts(); if (!parts.length) return;
  for (const p of parts) sendPart(p, [0x90, n, vel]);               // stack it across the layer
  activeNotes.set(n, parts); highlightKey(n, true);
  window.__stats.notes = activeNotes.size;
}
function noteOff(n) {
  const parts = activeNotes.get(n) || noteParts();                 // off to the SAME parts it started on
  //                                                                  (the selection may have moved since)
  for (const p of parts) sendPart(p, [0x80, n, 0]);
  activeNotes.delete(n); highlightKey(n, false);
  window.__stats.notes = activeNotes.size;
}
function sendCC(cc, val) { sendPart(activePart, [0xB0, cc & 0x7f, val & 0x7f]); }   // knob edits -> focused part
function sendCCpart(cc, val, p) { sendPart(p, [0xB0, cc & 0x7f, val & 0x7f]); }     // to a specific part
function sendPerfCC(cc, val) { for (const p of noteParts()) sendPart(p, [0xB0, cc & 0x7f, val & 0x7f]); }  // mod wheel etc
function sendBend(norm) {
  const b = Math.max(0, Math.min(16383, 8192 + Math.round(norm * 8191)));
  for (const p of noteParts()) sendPart(p, [0xE0, b & 0x7f, (b >> 7) & 0x7f]);   // bend follows the notes
}

// ---------- control state ----------
function setValue(id, v, send = true) {
  values[id] = v;
  if (globalIds.has(id)) for (const pv of partValues) pv[id] = v;   // global (fx/LFO rate): keep every part in sync
  if (ctlEl[id]) ctlEl[id].set(v);
  if (!send || !(id in ccById)) return;
  // A global control is one setting per BOARD, not per part: the effects live in the shell, on the
  // summed mix, and `fx.py`'s sniffer matches `(b & 0xF0) == 0xB0` -- it never looks at the channel.
  // So one message per board is right, and the channel it rides on is free. Spending that freedom
  // on the focused part's own channel is what keeps a single-board rig's byte stream unchanged.
  if (globalIds.has(id)) {
    for (let b = 0; b < NBOARDS; b++)
      sendPart((b << 2) | (activePart & 3), [0xB0, ccById[id] & 0x7f, v & 0x7f]);
    return;
  }
  sendCC(ccById[id], v);
}
// ---------- header MASTER OUTPUT volume: scales the FINAL audio (not per-part), so it stays put
//           across demos/presets. A GainNode on the way to the speakers — it does not touch the
//           board, so the line out and the headphone jack keep their own level. ----------
function renderMasterVol(v) { masterVol = v; if (mvolKnob) mvolKnob.style.transform = `rotate(${-135 + (v / 127) * 270}deg)`; }
function setMasterVolume(v) {
  masterVol = v;
  if (masterGainNode) masterGainNode.gain.value = v / 127;   // linear (0..1, 127 = unity)
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
// ---------- header OUT: which speaker the mix lands in ----------
// A sink choice on the AudioContext, not a rewiring — `analyser → ctx.destination` is untouched
// and `setSinkId` moves the destination itself. Remembered across reloads, because which box is
// plugged into the desk is a property of the desk and not of the session.
//
// Two things the browser's list cannot say for itself. Output **labels** stay empty strings until
// the page holds a media permission, and this page is only granted one when POWER opens the UAC2
// capture — so before the first POWER the entries are numbered, and the list is rebuilt when the
// names arrive. And every Tiliqua enumerates as an *output* as well as an input, because macOS
// opens both directions together; nothing in the gateware consumes host-to-device audio
// (`boards/tiliqua/gateware/top.py:394` drains it to keep the stream from stalling), so choosing
// one is a way to hear nothing at all. Those entries are marked rather than dropped: it is the
// browser's device list, and a filter that silently removes a device the player can see in the
// system panel is harder to understand than a label.
//
// **No sound** is the spec's `{type:'none'}` sink, not a gain of zero. The context keeps rendering
// into it — the meter and the analyser go on reading the same signal — so it silences the room
// without silencing the panel, and without touching MASTER VOL, which is a mix setting you would
// have to remember to put back. Useful when the board is going out of its own jacks.
const SINK_KEY = 'xls32.sink';
const SINK_NONE = 'none';                           // sentinel: a value no deviceId can collide with
let sinkId = localStorage.getItem(SINK_KEY) || '';
const canPickSink = typeof AudioContext !== 'undefined' && 'setSinkId' in AudioContext.prototype;
const isBoardOutput = (label) => /tiliqua/i.test(label);
async function applySink() {
  const sel = document.getElementById('outdev');
  if (!ctx || !canPickSink) return;                 // before POWER there is nothing to move yet
  try { await ctx.setSinkId(sinkId === SINK_NONE ? { type: 'none' } : sinkId); if (sel) sel.title = OUT_TITLE; }
  catch (e) {                                       // device vanished mid-session, or refused
    sinkId = ''; localStorage.removeItem(SINK_KEY);
    try { await ctx.setSinkId(''); } catch (_) {}
    if (sel) { sel.value = ''; sel.title = 'that output refused (' + e.name + ') — back to the system default'; }
  }
}
const OUT_TITLE = 'Where the mix comes out. Applies live, and is remembered across reloads.';
async function refreshOutputs() {
  const sel = document.getElementById('outdev'); if (!sel) return;
  if (!canPickSink) {                               // Firefox/Safari: AudioContext has no sink
    sel.disabled = true; sel.innerHTML = '<option>this browser picks the output</option>';
    sel.title = 'Choosing an output needs AudioContext.setSinkId — use the system sound settings.';
    return;
  }
  sel.title = OUT_TITLE;
  let devs = [];
  try { devs = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'audiooutput'); }
  catch (_) { /* no permission yet: "System default" is still the whole truth */ }
  const add = (id, text) => { const o = document.createElement('option'); o.value = id; o.textContent = text; sel.append(o); };
  sel.innerHTML = '';
  add('', 'System default');
  add(SINK_NONE, 'No sound');                       // always offered: it needs no device to exist
  devs.forEach((d, i) => {
    if (!d.deviceId || d.deviceId === 'default') return;   // the browser's own 'default' duplicates ours
    const name = d.label || 'Output ' + (i + 1);
    add(d.deviceId, isBoardOutput(name) ? name + ' — takes no audio' : name);
  });
  if (![...sel.options].some((o) => o.value === sinkId)) {  // remembered device is not on the desk today
    sinkId = ''; localStorage.removeItem(SINK_KEY);
  }
  sel.value = sinkId;
}
function initOutputPicker() {
  const sel = document.getElementById('outdev'); if (!sel) return;
  sel.addEventListener('change', async () => {
    sinkId = sel.value;
    if (sinkId) localStorage.setItem(SINK_KEY, sinkId); else localStorage.removeItem(SINK_KEY);
    await applySink();
  });
  navigator.mediaDevices?.addEventListener('devicechange', refreshOutputs);
  refreshOutputs();
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
  // MULTITIMBRAL: every part starts as a copy of the defaults; `values` aliases the active one
  partValues = Array.from({ length: NPARTS }, () => ({ ...values }));
  values = partValues[activePart];
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
// Two independent things: the SELECTION (which parts the keys play — clicking a chip's name) and
// the DEMO MUTE set (which parts a playing song sounds — clicking a chip's LED).
//
// Layering is explicit: a plain click selects that part ALONE, so the keyboard always auditions
// exactly the tone the knobs are editing. ⇧-click (or ⌘/Ctrl-click) adds a part to the selection
// and the keys then stack the note across every selected part; ⇧-click a layered part again drops
// it. The last part clicked is the PRIMARY one — full amber, and the one the knobs edit; the rest
// of the layer sits at half amber. Selecting a part also lights its LED, so a part you pull into
// the layer mid-song is audible.
function refreshPartUI() {
  document.querySelectorAll('#parts .partchip').forEach((chip, p) => {
    chip.classList.toggle('editing', p === activePart);
    chip.classList.toggle('layered', selSet.has(p) && p !== activePart);
    chip.querySelector('.partled').classList.toggle('on', playSet.has(p));
  });
  renderMidiIn();                     // the footer names the part a hardware keyboard now plays
  if (trsFollow) sendPartSelect();    // keep an existing claim in step; never make one here
}
// A keyboard on the Tiliqua's TRS jack sends on its own channel and reaches the FPGA without
// passing through any of this, so the PART chips cannot re-address it the way they do the
// on-screen keys. The gateware does it instead (midi_arb.py MidiPartSelect) and CC103 is how it
// is told which part. Only the primary part: the TRS stream is one stream, so layering it across
// parts would mean the arbiter replicating messages, which it does not do.
//
// *When* the panel claims the jack matters, because the board keeps that state until it is told
// otherwise or power-cycled -- there is nothing to read back and nothing that expires. So claiming
// is a gesture and not a side effect: a click on a PART chip is the player saying "play this one",
// and that is the only thing that claims. Everything else that moves `activePart` -- a demo
// starting, a preset restoring a part -- only keeps an existing claim in step, so it cannot take
// the jack away from someone who is choosing parts with their keyboard's channel knob instead.
// A fresh link hands it back (`syncBoard`), which is also the board's own reset default.
//
// Every board has its own TRS jack, and CC103 is the one message here that is not routed by part:
// its *value* is a part number in the board's own terms, so it goes out explicitly rather than
// through `sendPart`. Only the focused part's board is claimed. A keyboard plugged into board 3
// must not lose its jack because someone clicked a chip belonging to board 1 -- those are two
// players at two instruments. Releasing is the other way round: `syncBoard` hands back every
// board's jack, because a fresh link knows nothing about any of them.
const TRS_RELEASE = 127;            // CC103 >= 16 = override off, the keyboard's channel decides
let lastTrsPart = -1;               // GLOBAL part number the claim currently points at
let trsFollow = false;              // has this panel claimed the TRS jack?
function sendPartSelect(force = false) {
  const p = noteParts()[0];
  if (p === lastTrsPart && !force) return;      // refreshPartUI runs on plenty that is not a part change
  lastTrsPart = p;
  sendToBoard(p >> 2, [0xB0 | (p & 3), 103, p & 3]);
}
function claimTrs()   { trsFollow = true;  sendPartSelect(true); renderMidiIn(); }
function releaseTrs() { trsFollow = false; lastTrsPart = -1; sendAll([0xB0, 103, TRS_RELEASE]); renderMidiIn(); }
// The mute set is read live by the sequencer (see demoTick), so toggling an LED mid-song takes
// effect on the next note without anything having to be told about it.
function setPlay(p, on) {   // the LED = this part's demo mute
  if (on) playSet.add(p);
  else {
    playSet.delete(p);
    for (const [n, parts] of activeNotes) if (parts.includes(p)) sendPart(p, [0x80, n, 0]);  // release held on this part
    sweepPart(p);                                                // and whatever the demo is holding there
    flushScheduled(new Set([p]));                                // incl. the note-ons already scheduled ahead
  }
  refreshPartUI();
}
function sameSet(a, b) { return a.size === b.size && [...a].every((v) => b.has(v)); }
function setPart(p, layer = false) {   // click = this part alone · ⇧-click = add it to / drop it from the layer
  let next;
  if (!layer) next = new Set([p]);
  else { next = new Set(selSet); if (next.has(p) && next.size > 1) next.delete(p); else next.add(p); }
  if (!sameSet(next, selSet))                     // the layer moved: release first, or notes strand on the
    for (const n of Array.from(activeNotes.keys())) noteOff(n);   // parts that just left it
  // The same hazard on the TRS jack, and this page cannot fix it the same way: those notes never
  // reach the browser, and the gateware rewrites the channel at the arbiter's *output*, so a key
  // held across this click gets its note-off addressed to the part we are moving to and sticks on
  // the part we are leaving. Sweep whatever the jack might be sounding instead -- one part if we
  // already own it, all of them if we are taking it over from a channel we never knew. Skipped
  // while a demo runs: the sweep would cut the song's own notes, and stopDemo clears everything.
  const stale = trsFollow ? [lastTrsPart] : [...Array(NPARTS).keys()];
  selSet = next;
  focusPart(next.has(p) ? p : [...next][0]);      // knobs follow the clicked part (or what's left of the layer)
  const arriving = noteParts()[0];
  if (!demoPlaying) for (const s of stale) if (s >= 0 && s !== arriving) sweepPart(s);
  claimTrs();                                     // an explicit part gesture: the TRS jack follows it too
}
function focusPart(p) {                           // the PRIMARY part: the one the knobs edit
  activePart = p;
  values = partValues[p];                         // repoint; panel + knob sends now target this part
  for (const id in values) if (ctlEl[id]) ctlEl[id].set(values[id]);   // refresh knobs (no send)
  for (const c of selSet) playSet.add(c);         // a part you selected is never left demo-muted
  refreshPartUI();
  const pp = partPreset[p];                        // restore this part's patch name + browse position
  if (pp) { setBar(pp.cat, pp.name); curIndex = pp.index; }
  else { setBar('—', 'Init'); curIndex = -1; }
  document.querySelectorAll('#blist .bitem').forEach((el, i) => el.classList.toggle('on', i === curIndex));
}
// One row of 4 chips per board. With a single board there is no row heading and no board number
// anywhere: the panel looks exactly as it did when four parts was all there was, which is what the
// overwhelmingly common rig should get. The headings appear only when there is something to
// distinguish, and they bring IDENTIFY with them, because four identical boxes on a desk enumerate
// under one name and nothing on screen can say which is which.
function buildParts() {
  const box = document.getElementById('parts');
  if (!box) return;
  box.innerHTML = '';
  box.classList.toggle('multi', NBOARDS > 1);
  box.closest('.topbar')?.classList.toggle('multi', NBOARDS > 1);   // the bar sheds its caption, see style.css
  for (let b = 0; b < NBOARDS; b++) {
    const row = document.createElement('div'); row.className = 'brow';
    if (NBOARDS > 1) {
      const id = document.createElement('button'); id.className = 'bident';
      id.textContent = 'BOARD ' + (b + 1) + ' 🔊';
      id.title = 'play a short arpeggio on this board only — the one you hear is this row';
      id.addEventListener('click', () => identifyBoard(b));
      row.append(id);
    }
    for (let i = 0; i < PPB; i++) {
      const p = b * PPB + i;
      const chip = document.createElement('button'); chip.className = 'partchip';
      chip.title = 'click the name = play this part alone (and edit it) · ⇧-click = layer it with the others (across boards too) · click the LED = mute it in a demo';
      const led = document.createElement('span'); led.className = 'partled';
      led.title = 'green = a demo sounds this part · click to mute/unmute';
      const name = document.createElement('span'); name.className = 'partname';
      name.textContent = NBOARDS > 1 ? 'P' + (p + 1) : 'Part ' + (p + 1);
      chip.append(led, name);
      chip.addEventListener('click', (e) => setPart(p, e.shiftKey || e.metaKey || e.ctrlKey));  // ⇧ = layer
      led.addEventListener('click', (e) => {                    // LED is the MUTE, and only the mute
        e.stopPropagation(); setPlay(p, !playSet.has(p));       // (don't let it bubble up and re-focus/re-enable)
      });
      row.append(chip);
    }
    box.append(row);
  }
  refreshPartUI();
}
// Grow or shrink the panel to `n` boards. Called at boot from a silent port count and again at
// POWER from what actually opened, so the two can disagree and the second one wins.
function rebuildParts(n) {
  n = Math.max(1, Math.min(4, n | 0));
  if (n === NBOARDS && partValues.length === n * PPB) return;
  NBOARDS = n; NPARTS = n * PPB;
  // A part that appears starts from the defaults, but inherits the global (effects) settings --
  // those are one setting for the whole rig, and a new row must not claim reverb is off.
  const fresh = () => { const o = { ...spec.defaults }; globalIds.forEach((id) => (o[id] = values[id])); return o; };
  while (partValues.length < NPARTS) partValues.push(fresh());
  partValues.length = NPARTS; partPreset.length = NPARTS;
  const clamp = (s) => { const t = new Set([...s].filter((p) => p < NPARTS)); return t.size ? t : new Set([0]); };
  selSet = clamp(selSet); playSet = clamp(playSet);
  if (activePart >= NPARTS) activePart = 0;
  values = partValues[activePart];
  for (const id in values) if (ctlEl[id]) ctlEl[id].set(values[id]);
  buildParts();
}
// Four boxes, one name, no serial number in the descriptor: which row is which board is a question
// only the ear can answer. A short arpeggio on this board's part 1 answers it.
async function identifyBoard(b) {
  if (!powered) { await powerOn(); if (!powered) return; }
  const p = b * PPB, t0 = performance.now() + 30;
  [60, 64, 67, 72].forEach((n, i) => {
    sendPart(p, [0x90, n, 100], t0 + i * 140);
    sendPart(p, [0x80, n, 0], t0 + i * 140 + 130);
  });
}
function syncAllParts() {   // push every part's full patch to its board (boards <- UI on connect)
  for (let p = 0; p < NPARTS; p++)
    spec.controls.forEach((c) => sendCCpart(c.cc, partValues[p][c.id], p));
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
  partPreset[activePart] = { cat: p.category, name: p.name, index: curIndex };   // remember for this part
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
// The preset rows used to carry two audition buttons -- `T` played the corpus sample the fit was
// aiming at, `E` played engine.py's render of it, both from presetgen/build_previews.py. They
// answered "how far is this from its target?" in the browser, which was the question while the
// fitting pipeline was being built. That question is settled offline now (bank_compare.py,
// ab_render.py), and what is left in a shipped panel is a preset list that a listener browses by
// name -- so the buttons, and the clip loading behind them, are gone. build_previews.py still
// writes the clips; nothing serves them.
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
// Tab labels. `soundfont` is the corpus the bank was FITTED to, which is a fact about how the
// presets were made and not something a player needs at the moment of picking one -- next to USER
// the useful distinction is simply "the ones that shipped", so it reads `Preset`. The id stays
// `soundfont` everywhere else (spec.sources, presets_soundfont.json, the whole presetgen tree).
const SRC_LABEL = { nsynth: 'NSynth', soundfont: 'Preset', freesound: 'Freesound', factory: 'Factory', fm: 'FM' };
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
// The laptop keys are a piano an octave and a fourth wide: the home row A S D F G H J K L ; ' is
// the white keys C D E F G A B C D E F, the row above it W E · T Y U · O P the black ones in
// between. Keyed off `e.code` (the PHYSICAL key), not `e.key`, so it still plays with a kana IME
// switched on or on a non-QWERTY layout — `e.key` is 'Process'/'ち' there and would never match.
// (Semicolon/Quote are positional too: on a JIS board they're the same two keys right of L.)
const KMAP = {
  KeyA: 0, KeyW: 1, KeyS: 2, KeyE: 3, KeyD: 4, KeyF: 5, KeyT: 6,
  KeyG: 7, KeyY: 8, KeyH: 9, KeyU: 10, KeyJ: 11,
  KeyK: 12, KeyO: 13, KeyL: 14, KeyP: 15, Semicolon: 16, Quote: 17,
};
const OCT_DOWN = 'KeyZ', OCT_UP = 'KeyX';
const held = new Map();           // e.code -> the exact MIDI note sent (so a later octave shift
//                                   can't make keyup release the wrong note and strand the original)
function typingInField(el) {      // the preset search box owns the letters while it has focus
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable);
}
document.addEventListener('keydown', (e) => {
  if (e.repeat || e.metaKey || e.ctrlKey || e.altKey) return;
  if (typingInField(e.target)) return;
  const code = e.code;
  if (code === 'Escape') { allSoundOff(); e.preventDefault(); return; }   // the same thing PANIC does
  if (code === OCT_DOWN) { baseOct = Math.max(0, baseOct - 1); octLabel(); buildKeyboard(); return; }
  if (code === OCT_UP) { baseOct = Math.min(8, baseOct + 1); octLabel(); buildKeyboard(); return; }
  if (code in KMAP && !held.has(code)) { const note = 12 * (baseOct + 1) + KMAP[code]; held.set(code, note); noteOn(note); e.preventDefault(); }
});
document.addEventListener('keyup', (e) => {
  const code = e.code;
  if (held.has(code)) { noteOff(held.get(code)); held.delete(code); }
});
window.addEventListener('blur', () => { for (const n of held.values()) noteOff(n); held.clear(); });  // keyup lands elsewhere
function octLabel() { document.getElementById('octlabel').textContent = 'oct ' + baseOct; }

// ---------- panic / stuck-note & stuck-drag safety net ----------
function beginDrag(move, end) { activeDrag = { move, end: end || (() => {}) }; }
function endDrag() { if (activeDrag) { const d = activeDrag; activeDrag = null; d.end(); } }
function panic() {                                  // release everything still held or dragging
  for (const n of Array.from(activeNotes.keys())) noteOff(n);
  held.clear();
  endDrag();
}
// `panic` only knows the notes this page started. Notes from the Tiliqua's TRS jack never pass
// through the browser at all, so the only thing that can silence one is a message addressed to
// the engine itself. Since M34 that is a channel mode message, three bytes per part.
//
// The 128-note sweep stays anyway, and not out of superstition: a board flashed with a
// pre-M34 bitstream drops 120-127 in `apply_cc`'s catch-all, and PANIC going quietly useless
// against last month's firmware is exactly the kind of failure nobody reports. 512 note-offs
// cost nothing next to a button a player presses when something has already gone wrong.
function sweepPart(p) { for (let n = 0; n < 128; n++) sendPart(p, [0x80, n, 0]); }
function sweepAllParts() { for (let p = 0; p < NPARTS; p++) sweepPart(p); }
// CC120, not CC123. All Sound Off cuts the envelope dead where All Notes Off lets it fall through
// the release, and -- the part that decides it -- 120 is the only one of the two that reaps a
// voice already *in* its release. Both sweep the same set otherwise ("this part, not already
// off"), so sending 123 as well would repeat a finished job rather than catch anything. PANIC
// clicks. That is what a panic button is for; the musical stop is the note-off sweep below.
function allSoundOff() {
  panic();
  for (let p = 0; p < NPARTS; p++) sendPart(p, [0xB0, 120, 0]);   // every part of every board
  sweepAllParts();
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
// A hardware keyboard transmits on whatever channel it feels like -- usually 1, always the same
// one -- so its channel is DISCARDED here and the note is re-addressed to the selected part(s),
// exactly as the on-screen keys are. That makes the PART chips steer both the same way.
//
// The readout in the footer exists because the failure this routing has is silent: if no port is
// bound, the chips look like they are being ignored when in fact nothing ever arrived to route.
let midiPorts = [];              // Web-MIDI inputs bound in this tab
function partsLabel() { return noteParts().map((c) => 'P' + (c + 1)).join('+'); }
// Two different inputs, and the footer has to keep them apart. `midiPorts` are the *host's* MIDI
// devices; this page re-addresses their notes to the selected parts before forwarding them, so
// they always land on `partsLabel()`. The Tiliqua's TRS jack never passes through here at all --
// the gateware routes it, and the one thing the panel knows is whether it has claimed it. Say
// which, because it is the state a player cannot otherwise see and it survives closing this page.
//
// "claimed", not "following PART". Since M34 the claim is not the last word: MidiChanWatch drops
// it the moment the keyboard sends on a channel it was not sending on before, so a player who
// reaches for the channel knob takes the decision back and this label goes stale until the next
// render. Saying what the panel *asked for* is a claim the panel can actually stand behind.
function trsLabel() {
  // With several boards there are several jacks, and which one was claimed is the whole point.
  const p = noteParts()[0];
  const jack = NBOARDS > 1 ? 'TRS jack (Board ' + ((p >> 2) + 1) + ')' : 'TRS jack';
  return trsFollow ? jack + ' → P' + (p + 1) + ', until the keyboard changes channel'
                   : jack + ' → its own MIDI channel';
}
function renderMidiIn() {
  const el = document.getElementById('midiin'); if (!el) return;
  el.textContent = 'MIDI in: ' + (midiPorts.length ? midiPorts.join(', ') : 'none') + ' → ' + partsLabel()
                 + (link && link.kind === 'tiliqua' ? ' · ' + trsLabel() : '');
  el.classList.toggle('none', !midiPorts.length);
}
function bindMidiInput(inp) {
  if (inp.__xls32) return;              // re-scanning must not stack a second handler -> double notes
  inp.__xls32 = true;
  midiPorts.push(inp.name || 'MIDI');
  inp.onmidimessage = (e) => {
    const d = Array.from(e.data);                  // re-address voice messages to the selected part
    const st = d[0] & 0xf0;
    // Notes go through noteOn/noteOff rather than straight out. They used to be forwarded raw,
    // which meant `activeNotes` never knew they were held -- so a part change re-addressed the
    // note-off to the part you had just switched to and left the original sounding forever.
    if (st === 0x90 && d[2] > 0) noteOn(d[1], d[2]);
    else if (st === 0x80 || st === 0x90) noteOff(d[1]);       // vel-0 note-on is a note-off
    // The keyboard's own panic button. `allSoundOff` is called rather than forwarding the CC,
    // because a message on one channel would silence one part and leave the other three -- and
    // the page's own `activeNotes` would still believe it was holding notes that no longer sound.
    else if (st === 0xB0 && (d[1] === 120 || d[1] === 123)) allSoundOff();
    else if (d[0] >= 0x80 && d[0] < 0xf0) { for (const p of noteParts()) sendPart(p, [st, ...d.slice(1)]); }
    else sendAll(d);        // system messages carry no channel, so every board gets them unchanged
  };
}
async function initWebMidi() {
  if (!navigator.requestMIDIAccess) return;
  try {
    // Shared with transport.js, which wants the *outputs* off the same grant.
    const access = await window.XLS32.midiAccess();
    const scan = () => {
      midiPorts = [];                              // rebuilt from scratch; `__xls32` keeps binds unique
      access.inputs.forEach(bindMidiInput);
      renderMidiIn();
      countBoards();
    };
    scan();
    // `inputs` used to be walked exactly once, at boot. A keyboard plugged in after the page
    // loaded therefore never got a handler and was simply inert -- which reads as "the PART
    // buttons don't work for MIDI" rather than as "this port was never opened".
    access.onstatechange = scan;
  } catch (e) { /* no Web-MIDI permission */ }
}

// How many boards to draw, before anything is opened. `countTiliquas` never prompts, so on a first
// visit it says 0 and the panel draws one board -- which is both the honest guess and the layout
// that needs no explanation. On every later visit the rows are right from the first paint.
//
// Before POWER, the row count follows the bus: unplug a board and its row goes. Afterwards it does
// not. Re-binding a live rig means re-opening MIDI ports and re-attaching capture streams, and
// POWER off/on already does exactly that and is already tested; a second, subtly different path
// that only runs when someone yanks a cable mid-session is not worth having.
async function countBoards() {
  let n = 0;
  try { n = await window.XLS32.countTiliquas(); } catch (e) { return; }
  if (!n || n === NBOARDS) return;
  if (links.length) {                             // already bound: say so, do not rebuild under them
    const el = document.getElementById('statustext');
    if (el) el.textContent = 'board count changed — POWER off/on to rebind';
    return;
  }
  rebuildParts(n);
}

// ---------- audio + the board link ----------
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
  analyser = ctx.createAnalyser(); analyser.fftSize = 1024;
  // Unity gain node (kept as an easy volume tap). The board output already saturates ≤1.0
  // and per-note levels are conservative (~0.2 peak), so no attenuation is needed.
  masterGainNode = ctx.createGain(); masterGainNode.gain.value = masterVol / 127;   // header VOL drives this
  // The transport decides what each source is: a MediaStream off a Tiliqua's UAC2 input, or the
  // resampling worklet fed by the Basys 3's UART. Every board's node lands on the same gain, so
  // four boards are summed here and the whole 16-part rig comes out of the computer's speakers --
  // no mixer on the desk. Each UAC2 stream free-runs on its own board's clock; the drift between
  // them is Web Audio's problem, and it is the one part of this that hardware could still refute.
  audioNodes = [];
  for (const l of links) {
    const n = await l.attachAudio(ctx);          // null = that board had no capture device to give
    if (n) { n.connect(masterGainNode); audioNodes.push(n); }
  }
  node = audioNodes[0] || null;
  masterGainNode.connect(analyser);
  analyser.connect(ctx.destination);              // clean output path (no MediaStream processing)
  await refreshOutputs();                         // the real device names, now that a capture is open
  await applySink();                              // and the remembered output, now that there is a context
                                                  // (in that order: a device that is gone is dropped
                                                  // from the list before it can be asked for)
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
  window.__stats.ctx = ctx.state + '@' + ctx.sampleRate;
  const buf = new Float32Array(analyser.fftSize);
  setInterval(() => {
    if (!analyser) return;
    analyser.getFloatTimeDomainData(buf);
    let s = 0; for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
    window.__stats.rms = Math.sqrt(s / buf.length);
    window.__stats.ctx = ctx.state + '@' + ctx.sampleRate;
    const dbg = document.getElementById('dbg');
    const boards = links.length > 1 ? `${links.length} boards · audio ${audioNodes.length}/${links.length}`
                                    : (link ? link.label : 'no board');
    if (dbg) dbg.textContent = `${ctx.state}@${ctx.sampleRate} · ${boards}`
      + ` · rms ${window.__stats.rms.toFixed(3)}`
      + (audioEl ? ` · el ${audioEl.paused ? 'paused' : 'play'}` : '');
  }, 150);
}
// Everything the board needs told after a fresh link: it may have been power-cycled since the
// page loaded, and it keeps no state we can read back.
function syncBoard() {
  sweepAllParts();
  syncAllParts();
  releaseTrs();               // a fresh link cannot know what the player is doing with the TRS
                              // jack, so hand it back rather than seize it -- otherwise a board
                              // driven from the panel once stays pinned to that part until it is
                              // power-cycled. The first PART click claims it again.
}
function currentAll() { const v = {}; spec.controls.forEach((c) => v[c.id] = values[c.id]); return v; }
function setStatus(on) {
  window.__stats.connected = on;
  document.getElementById('dot').classList.toggle('on', on);
  document.getElementById('statustext').textContent = on ? 'live' : 'off';
}

// ---------- picking a board ----------
// Web MIDI, getUserMedia and serial.requestPort all prompt, and the last two only prompt from
// inside a user gesture, so POWER is what starts this. Nothing here can be done at boot on the
// page's behalf, which is the one real cost of dropping the server: it could open a device because
// it was told to on a command line.
function chooseBoard(det) {
  const overlay = document.getElementById('boardbox');
  const list = document.getElementById('boardlist');
  list.innerHTML = '';
  return new Promise((resolve) => {
    const close = (v) => { overlay.classList.add('hidden'); resolve(v); };
    for (const [key, t] of Object.entries(window.XLS32.TRANSPORTS)) {
      const el = document.createElement('div'); el.className = 'bitem';
      const ok = t.ok();
      const state = det && det[key];
      // 'no' is a real answer -- permission is held and the board is not on the bus -- so say so
      // rather than offer it. It stays clickable: the check can only see hardware the browser has
      // been shown before, and being wrong about that must not lock anyone out.
      const note = !ok ? 'not supported by this browser'
                 : state === 'yes' ? t.hint + ' · detected'
                 : state === 'no' ? t.hint + ' · not found'
                 : t.hint;
      el.innerHTML = `<b>${t.name}</b><span class="dhint"> — ${note}</span>`;
      if (state === 'yes') el.classList.add('found');
      if (!ok) el.classList.add('off');
      else el.addEventListener('click', () => close(key));
      list.append(el);
    }
    document.getElementById('boardclose').onclick = () => close(null);
    overlay.classList.remove('hidden');
  });
}

async function openBoard(key) {
  const ts = await window.XLS32.TRANSPORTS[key].connectAll();   // plural: up to 4 Tiliquas
  if (!ts.length) return false;
  links = ts; link = ts[0]; SR = link.sr;
  rebuildParts(links.length);
  setStatus(true);
  return true;
}

async function connectBoard() {
  // Skip the picker when the answer is not in doubt. Detection is silent and only ever sees boards
  // the browser has already been given permission for, so this helps on the second visit onwards --
  // exactly the visits where being asked the same question again is noise.
  const det = await window.XLS32.detectBoards();
  const found = Object.keys(det).filter((k) => det[k] === 'yes');
  if (found.length === 1) {
    try {
      return await openBoard(found[0]);
    } catch (e) {
      // Detected but would not open (bitstream swapped, sound device claimed, wrong FTDI channel).
      // Fall through to the picker rather than dead-end on an alert: the other board may be there.
      console.warn('auto-connect failed:', e);
    }
  }
  const key = await chooseBoard(det);
  if (!key) return false;
  try {
    return await openBoard(key);
  } catch (e) {
    document.getElementById('statustext').textContent = 'no board';
    alert('Could not open the board: ' + (e && e.message ? e.message : e));
    return false;
  }
}

// Powering on takes about a second of real work -- MIDI + audio permission, the serial or UAC2
// open, the AudioWorklet module, then ~200 CC per part to tell the board what the panel is showing.
// None of it can be started before the click (they are all user-gesture gated), so the wait is not
// removable; being silent through it is. The button dims and the status LED says `starting` for the
// whole of it, and the flag is also a re-entrancy guard: the second click on an unresponsive button
// used to run the whole connect a second time against the board the first one was still opening.
let powering = false;
async function togglePower() {
  if (powering) return;
  if (powered) return powerOff();
  const btn = document.getElementById('power');
  const st = document.getElementById('statustext');
  powering = true;
  btn.classList.add('busy');
  st.textContent = 'starting';
  try {
    await powerOn();
  } finally {
    powering = false;
    btn.classList.remove('busy');
    // Only clear what this wrote: powerOn() reports its own failures there ('no board', 'need
    // https', 'audio error') and setStatus() writes 'live' on success.
    if (st.textContent === 'starting') st.textContent = powered ? 'live' : 'off';
  }
}
function powerOff() {
  powered = false;
  if (masterGainNode) { try { masterGainNode.disconnect(); } catch (_) {} }   // mute browser output
  for (const n of Array.from(activeNotes.keys())) noteOff(n);                          // release held notes
  document.getElementById('power').classList.remove('on');
}
async function powerOn() {
  if (powered) return;
  if (ctx) {                                    // already initialized -> re-power: reconnect output
    if (masterGainNode && analyser) { try { masterGainNode.connect(analyser); } catch (_) {} }
    if (ctx.state !== 'running') ctx.resume().catch(() => {});
    powered = true;
    document.getElementById('power').classList.add('on');
    return;
  }
  // Web MIDI, Web Serial and AudioWorklet are all secure-context only. Over plain http://<ip>
  // they are simply absent, so say that rather than fail three different ways.
  if (!window.isSecureContext || !(window.AudioContext || window.webkitAudioContext)) {
    document.getElementById('statustext').textContent = 'need https';
    alert('This page needs a secure context. Open it over HTTPS or from localhost — e.g. ' +
          'http://127.0.0.1:8765 — not plain http://<ip>, and not file://.');
    return;
  }
  if (!link && !await connectBoard()) return;
  try {
    await startAudio();        // needs the user gesture (the POWER click)
  } catch (e) {
    document.getElementById('statustext').textContent = 'audio error';
    alert('Audio init failed: ' + (e && e.message ? e.message : e));
    return;
  }
  syncBoard();                 // the board may have rebooted since the page loaded
  powered = true;
  document.getElementById('power').classList.add('on');
}

// ---------- demo player (4-part authored songs, played live to the board) ----------
// M31 moved the sequencer here from a Python thread (webui/server.py `_demo_run`). The shape
// changed with the move: a thread can sleep until each note is due, a tab cannot -- setTimeout
// is throttled, and coarse besides. So this schedules AHEAD instead, handing every event a
// performance.now() timestamp a quarter-second early and letting the transport hold it. On the
// Tiliqua that means the MIDI service emits it, so the timing survives a busy main thread; on
// the Basys 3 there is no hardware scheduler and it degrades to a timer per event.
const LOOKAHEAD_MS = 250;        // how far past `now` each tick schedules
const TICK_MS = 60;              // and how often it does so -- comfortably inside the look-ahead
//
// A song is written for 4 parts, which is exactly one board, so with several plugged in it has to
// be told which. It plays on the board the focused part belongs to: start a demo with a chip of
// board 3 selected and board 3 plays it, leaving the other twelve parts free to play over the top.
// The song data never learns any of this -- its channels 0-3 are offset into global part numbers
// as the events are built, and the router takes them from there.
let demos = { songs: [] }, demoIdx = -1, demoPlaying = false, demoBoard = 0;
let demoEvents = [], demoLoopMs = 0, demoBase = 0, demoPos = 0, demoTimer = null;
// Every note-on handed to a transport with a future timestamp that has not been given its note-off
// yet, keyed part<<8|note, plus the latest timestamp handed out. This is the look-ahead's debt: see
// `flushScheduled`.
let demoSched = new Set(), demoSchedTo = 0;

function demoTick() {
  demoTimer = null;
  if (!demoPlaying) return;
  const horizon = performance.now() + LOOKAHEAD_MS;
  for (;;) {
    if (demoPos >= demoEvents.length) {           // end of the bar: wrap and keep going
      if (demoBase + demoLoopMs > horizon) break;
      demoBase += demoLoopMs; demoPos = 0;
      continue;
    }
    const [tms, m] = demoEvents[demoPos];
    const when = demoBase + tms;
    if (when > horizon) break;
    demoPos++;
    // A muted part swallows its note-ons and still gets its note-offs, so muting mid-note
    // releases what is already sounding instead of stranding it.
    if ((m[0] & 0xF0) === 0x90 && !playSet.has(m[0] & 0x0F)) continue;
    const part = m[0] & 0x0F;                  // the low nibble is a GLOBAL part, offset at build time
    if ((m[0] & 0xF0) === 0x90) demoSched.add((part << 8) | m[1]);
    else if ((m[0] & 0xF0) === 0x80) demoSched.delete((part << 8) | m[1]);
    demoSchedTo = Math.max(demoSchedTo, when);
    sendPart(part, m, when);
  }
  demoTimer = setTimeout(demoTick, TICK_MS);
}
// The other half of the look-ahead, and the reason STOP used to leave notes sounding.
// `cancelPending()` retracts what the PAGE still holds -- which on the Basys 3 is everything, since
// its scheduler is a setTimeout per message. On the Tiliqua the scheduler is the browser's: a
// message goes to the MIDI service with a timestamp the moment the tick runs, and the only way to
// take it back is `MIDIOutput.clear()`, which Chrome does not implement (`typeof out.clear ===
// 'undefined'`, so the call throws into an empty catch and cancels nothing). So up to LOOKAHEAD_MS
// of note-ons are already gone and still coming, and their note-offs -- which the cancelled loop
// never reached -- are not. The 128-note sweep cannot help: it lands BEFORE the stray note-on.
//
// So every note-on the sequencer scheduled without a matching note-off is released here, stamped
// past the last timestamp handed out. The MIDI service delivers in timestamp order, so it arrives
// after the stray note-on rather than racing it.
function flushScheduled(parts = null) {
  const at = Math.max(performance.now(), demoSchedTo) + 2;
  for (const k of Array.from(demoSched)) {
    const p = k >> 8;
    if (parts && !parts.has(p)) continue;
    sendPart(p, [0x80, k & 0x7f, 0], at);
    demoSched.delete(k);
  }
}
// The demo transport's two states in one place, because they were drifting: the header label was
// set in `playDemo` and `stopDemo` separately, and 💾 TONES lived in the browser overlay and so was
// governed by nothing at all.
//
// `demoIdx` survives a stop on purpose. It is the song 💾 TONES writes to, and the tones themselves
// live in `partValues`, which stopping does not touch -- so "tweak, hit stop out of habit, save"
// has to keep working. It means "the loaded song", and only `playDemo` changes it.
function refreshDemoUI() {
  const b = document.getElementById('demo');
  if (b) b.textContent = demoPlaying ? '■ DEMO' : '▶ DEMO';
  const s = document.getElementById('demosave');
  if (s) {
    const song = demoIdx >= 0 ? demos.songs[demoIdx] : null;
    s.classList.toggle('hidden', !song);
    if (song) s.title = `save the four PART tones into "${song.name}"` +
                        (demoPlaying ? '' : ' (stopped, but the tones are still on the panel)');
  }
  document.querySelectorAll('#demolist .bitem').forEach((el, k) =>
    el.classList.toggle('on', demoPlaying && k === demoIdx));
}
function stopDemo() {
  demoPlaying = false;
  if (demoTimer) { clearTimeout(demoTimer); demoTimer = null; }
  for (const l of links) l.cancelPending();     // drop whatever was scheduled but not yet sent
  // A cancelled look-ahead has certainly dropped some note-offs on the floor. The sweep rather
  // than `allSoundOff` on purpose: stopping a song should not cut a note the player is holding by
  // hand on a part the song was not using, and `allSoundOff` sends CC120, which clicks.
  sweepAllParts();
  flushScheduled();                             // ...and the ones the sweep is too early to catch
  refreshDemoUI();
}
async function playDemo(idx) {
  if (idx === demoIdx && demoPlaying) { stopDemo(); return; }   // toggle off if same song
  if (!powered) { await powerOn(); if (!powered) return; }
  stopDemo();
  const song = demos.songs[idx]; if (!song) return;
  demoIdx = idx;
  // Get out of the way. This browser is a full-screen overlay (position:fixed, inset:0, plus a
  // scrim), and the moment a song is playing the panel behind it is the thing worth looking at --
  // the four PART chips light up, and every knob is live on whichever part is focused. Closing it
  // costs nothing: the header button has already flipped to `■ DEMO` and stops the song from there.
  const dbox = document.getElementById('demobox'); if (dbox) dbox.classList.add('hidden');
  // the song's shared effect state (mode + reverb/room/chorus/delay); default any it omits (old songs)
  const fxState = {};
  EFFECT_IDS.forEach((id) => { fxState[id] = (song[id] != null) ? song[id] : spec.defaults[id]; });
  demoBoard = Math.min(activePart >> 2, NBOARDS - 1);   // the song plays on the focused part's board
  const base = demoBoard * PPB;
  // load the song's 4 part patches + effects into the multitimbral editor so each PART can be tweaked live
  song.parts.forEach((p, ch) => { if (ch < PPB) partValues[base + ch] = { ...spec.defaults, ...p, ...fxState }; });
  playSet = new Set([base, base + 1, base + 2, base + 3]);  // a song sounds all 4: light every LED, then click one to mute that part
  activePart = base; selSet = new Set([base]); values = partValues[base];  // the song plays all 4; your keys play its part 1
  for (const id in values) if (ctlEl[id]) ctlEl[id].set(values[id]);           // reflect part 1 on the panel
  refreshPartUI();
  // build the setup MIDI from the CURRENT (customized) state: shared effects + each part's patch.
  // Each entry is [globalPart, message]; the effects are per-board and go to every board, because a
  // song's reverb should not stop at the edge of the board it happens to be playing on.
  const setup = [];
  EFFECT_IDS.forEach((id) => {
    if (!(id in ccById)) return;
    for (let b = 0; b < NBOARDS; b++) setup.push([b * PPB, [0xB0, ccById[id], fxState[id] & 0x7f]]);
  });
  for (let ch = 0; ch < PPB; ch++)
    for (const id in partValues[base + ch])
      if ((id in ccById) && !globalIds.has(id))
        setup.push([base + ch, [0xB0, ccById[id], partValues[base + ch][id] & 0x7f]]);
  // build timed note events (ms), sorted so the look-ahead loop can walk them with one cursor
  const beatMs = 60000 / song.bpm, loopMs = song.bars * 4 * beatMs;
  const events = [];
  song.notes.forEach(([t, dur, ch, note, vel]) => {
    events.push([t * beatMs, [0x90 | (base + ch), note, vel]]);
    events.push([(t + dur) * beatMs, [0x80 | (base + ch), note, 0]]);
  });
  events.sort((a, b) => a[0] - b[0]);
  for (const [p, m] of setup) sendPart(p, m);  // untimed: these want to land before the clock starts
  demoEvents = events; demoLoopMs = loopMs; demoPos = 0;
  // 240 ms of head start, as the Python sequencer had: the setup burst is ~200 CC messages and the
  // first downbeat must not race the patch that shapes it.
  demoBase = performance.now() + 240;
  demoPlaying = true;
  demoTick();
  setBar(song.genre, song.name);
  refreshDemoUI();
}
async function saveDemoTones() {
  const btn = document.getElementById('demosave'); const label = btn.textContent;
  const flash = (t) => { btn.textContent = t; setTimeout(() => (btn.textContent = label), 1300); };
  // Unreachable now that the button is hidden without a loaded song, but the button is one
  // `classList.toggle` away from being wrong and this writes a file.
  if (demoIdx < 0 || !demos.songs[demoIdx]) { flash('play a demo first'); return; }
  const song = demos.songs[demoIdx];
  const parts = [];
  for (let ch = 0; ch < PPB; ch++) {       // the 4 parts of the board the song is playing on
    const pv = partValues[demoBoard * PPB + ch];
    const o = {}; spec.controls.forEach((c) => { if (!globalIds.has(c.id)) o[c.id] = pv[c.id]; });
    parts.push(o);
  }
  const fxState = {}; EFFECT_IDS.forEach((id) => { fxState[id] = partValues[activePart][id]; });   // full effect state
  song.parts = parts; Object.assign(song, fxState);           // update in memory
  // The server used to patch demos.json in place. With no server the page cannot write into its
  // own directory, so it hands the whole file back and you drop it into webui/static/ yourself.
  // Whole file, not the one song: demos.json is the single source of truth and a diff of one song
  // is not something you can put back without a tool.
  const blob = new Blob([JSON.stringify(demos, null, 1)], { type: 'application/json' });
  try {
    if (window.showSaveFilePicker) {
      const h = await window.showSaveFilePicker({ suggestedName: 'demos.json',
        types: [{ description: 'JSON', accept: { 'application/json': ['.json'] } }] });
      const w = await h.createWritable(); await w.write(blob); await w.close();
    } else {                                     // Firefox / Safari: fall back to a plain download
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = 'demos.json'; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    }
    btn.textContent = '✓ saved';
  } catch (e) { btn.textContent = (e && e.name === 'AbortError') ? label : '✗ err'; }
  setTimeout(() => (btn.textContent = label), 1300);
}
function buildDemo() {
  const box = document.getElementById('demolist');
  (demos.songs || []).forEach((s, idx) => {
    const el = document.createElement('div'); el.className = 'bitem demoitem';
    const lbl = document.createElement('span'); lbl.className = 'dlabel'; lbl.textContent = s.genre + ' · ' + s.name;
    el.append(lbl);
    el.addEventListener('click', () => playDemo(idx));
    box.append(el);
  });
  const overlay = document.getElementById('demobox');
  // The label already flips to `■ DEMO` while a song plays, so make the button do what it says:
  // stop. The picker only opens when nothing is playing -- otherwise the one control that looks
  // like a stop button is the one control that cannot stop anything.
  document.getElementById('demo').addEventListener('click', () => {
    if (demoPlaying) { stopDemo(); return; }
    overlay.classList.remove('hidden');
  });
  document.getElementById('democlose').addEventListener('click', () => overlay.classList.add('hidden'));
  document.getElementById('demosave').addEventListener('click', saveDemoTones);
  overlay.addEventListener('click', (e) => { if (e.target.id === 'demobox') overlay.classList.add('hidden'); });
}

// ---------- boot ----------
// M31 deleted the LOCAL play mode from here (host audio device + host MIDI in, `/api/local`, and
// the 3 s poll that rescanned CoreMIDI). It existed because the server owned the hardware and
// could play with lower latency than a WebSocket round trip; with the page owning the hardware
// there is no round trip left to avoid, and the host has nothing the browser cannot reach.
async function boot() {
  document.getElementById('ver').textContent = VERSION;
  // Both baked at build time now (presetgen/build_spec.py, presetgen/build_demos.py) -- fetched as
  // plain files, so any static host serves the whole app.
  spec = await (await fetch('spec.json?' + VERSION)).json();
  demos = await fetch('demos.json?' + VERSION).then((r) => r.json()).catch(() => ({ songs: [] }));
  buildPanel(); buildParts(); buildPresets(); buildKeyboard(); setupWheels(); octLabel(); initWebMidi(); buildDemo();
  setBar('—', 'Init');
  initMasterVol(); initOutputPicker();
  document.getElementById('power').addEventListener('click', togglePower);
  document.getElementById('save').addEventListener('click', saveUser);
  document.getElementById('panic').addEventListener('click', allSoundOff);
  document.getElementById('init').addEventListener('click', () => {
    applyValues(spec.defaults, powered); setBar('—', 'Init'); curIndex = -1;
    document.querySelectorAll('#blist .bitem.on').forEach((el) => el.classList.remove('on'));
  });
}

// The one thing `webui/route_check.html` cannot do from outside: `links` and `NBOARDS` are `let`
// bindings and never become properties of the global object, so a four-board panel cannot be
// staged from the parent frame. Everything else the check needs it takes without help -- the top
// -level function declarations here are global properties, so it records by replacing
// `sendToBoard`. This hook exists for the check and is harmless otherwise: it binds no hardware,
// and the fake links it installs discard every byte handed to them.
window.__xls32 = {
  testMode({ boards = 1, powered: pw } = {}) {
    links = Array.from({ length: boards }, (_, b) => ({
      kind: 'tiliqua', sr: 48000, board: b, label: 'test board ' + b,
      sendMidi() {}, cancelPending() {}, attachAudio() { return null; }, close() {},
    }));
    link = links[0];
    rebuildParts(boards);
    if (pw !== undefined) powered = pw;
  },
};
boot();
