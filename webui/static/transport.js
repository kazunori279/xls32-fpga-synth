// The board link, in the browser. M31.
//
// Until now this file did not exist, because the link was a Python process: the page held one
// WebSocket to `webui/server.py`, pushed MIDI bytes up it, and got 16-bit PCM frames back down.
// The bridge owned the hardware -- a PortAudio stream on the Tiliqua's UAC2 device, a file
// descriptor on the Basys 3's UART -- and the browser owned nothing but pixels.
//
// Chrome has both halves natively now, so the hop buys nothing and costs a server. Web MIDI
// reaches the Tiliqua's USB MIDI endpoint directly and `getUserMedia` reads its UAC2 input as a
// capture device; Web Serial opens the Basys 3's FTDI port at 2 Mbaud. Measured before writing any
// of this, on Chrome 150: a note-on sent from the page and picked back up by the page's own
// analyser moved RMS 0.00004 -> 0.10008 -> 0.00316 with no Python running anywhere.
//
// Everything here is behind a user gesture on purpose. `requestMIDIAccess`, `getUserMedia` and
// `serial.requestPort` all prompt, and two of the three only prompt from inside a gesture, so the
// entry point is `connect()` and the caller is a click.
//
// The interface app.js codes against:
//
//     kind          'tiliqua' | 'basys3'
//     label         what to show in the status line
//     sr            nominal wire rate (48000 / 32000) -- seeds the AudioContext and the worklet
//     timed         true if sendMidi() honours `when` in hardware rather than by timer
//     board         0-based index among the boards of this kind (Tiliqua: 0..3)
//     connect()     open, throw on refusal
//     sendMidi(b, when)   `when` is a performance.now() timestamp; omit for "now"
//     cancelPending()     drop anything scheduled but not yet sent (demo stop)
//     attachAudio(ctx)    -> an AudioNode carrying the board's stereo output, or null if this
//                            board has no capture device to give (see discoverTiliquas)
//     close()
//
// The prompting entry point is the registry's `connectAll()`, and the caller is a click. It hands
// back a list because four Tiliquas on four USB cables is a supported rig -- 16 parts, 128 voices.

// ---------- Web MIDI, shared ----------
// One MIDIAccess for the whole page: the transport wants outputs, app.js wants inputs, and asking
// twice means two permission prompts for one permission.
let _midiAccess = null;
function midiAccess() {
  if (!navigator.requestMIDIAccess) return Promise.reject(new Error('this browser has no Web MIDI'));
  if (!_midiAccess) _midiAccess = navigator.requestMIDIAccess({ sysex: false });
  return _midiAccess;
}

// The full iProduct from boards/tiliqua/gateware/usb_iface.py, not just "Tiliqua": the vendor's
// own bitstreams enumerate as a bare "Tiliqua" with the same 4-in/4-out UAC2 shape, and a looser
// match opens one of those, streams silence, and looks exactly like a dead synth.
const TILIQUA_MATCH = 'tiliqua xls32';
const matches = (name) => (name || '').toLowerCase().includes(TILIQUA_MATCH);

class TiliquaTransport {
  // A transport is handed its hardware rather than going to look for it: finding the boards is
  // `discoverTiliquas()`'s job, below, because with more than one plugged in it is a question
  // about the *set* and cannot be answered one instance at a time.
  constructor(out, deviceId, board = 0) {
    this.kind = 'tiliqua'; this.label = 'Tiliqua'; this.sr = 48000; this.timed = true;
    this.out = out; this.stream = null; this.deviceId = deviceId; this.board = board;
  }

  async connect() {
    if (!this.out) throw new Error('no "Tiliqua XLS32" MIDI output — is the bitstream loaded?');
    await this.out.open();
    this.label = 'Tiliqua · USB MIDI + UAC2' + (this.deviceId ? '' : ' (no audio in)');
  }

  sendMidi(bytes, when) {
    if (!this.out) return;
    // `send(data, when)` hands the timestamp to the MIDI service, which is the whole reason the
    // sequencer can be a lazy look-ahead loop: jitter in our setTimeout does not reach the wire.
    try { this.out.send(bytes, when); } catch (e) { /* port closed under us */ }
  }

  // Nothing to cancel, on Chrome. `MIDIOutput.clear()` is in the Web MIDI spec and is the only way
  // to retract a message already handed to the browser's scheduler, and Blink does not implement it
  // -- `typeof out.clear` is 'undefined' on Chrome 141/macOS, so this used to throw into an empty
  // catch and report success. It is still called where it exists, but a caller must assume that
  // everything it scheduled WILL be delivered: app.js `flushScheduled()` is what actually stops a
  // song, by releasing the notes this could not recall.
  cancelPending() { if (this.out && this.out.clear) { try { this.out.clear(); } catch (e) {} } }

  async attachAudio(ctx) {
    if (!this.deviceId) return null;             // discovery found fewer inputs than outputs
    // Every default-on processing block has to go off explicitly. Voice processing on a synth
    // feed is not a subtle degradation -- AGC alone rides the level of a held pad down to nothing.
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { deviceId: { exact: this.deviceId }, channelCount: 2,
               echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    });
    // The board streams 4 channels: ch0/1 audio, ch2/3 a gray-coded clock counter (xls_core.py).
    // Chrome asks for 4, is given 2, and the 2 it gets are the audio -- confirmed by the same
    // measurement quoted at the top: -88 dBFS at rest, which a folded-in counter could not be.
    const src = ctx.createMediaStreamSource(this.stream);
    // xls_core.py pads the output 6 dB on the way out (ASQ full scale is +-8.192 V, Eurorack
    // levels), so undo it here. host/transport/usbaudio.py does the same *2 for the same reason;
    // this keeps browser levels equal to every measurement in test/.
    const pad = ctx.createGain(); pad.gain.value = 2;
    src.connect(pad);
    return pad;
  }

  close() {
    if (this.stream) { this.stream.getTracks().forEach((t) => t.stop()); this.stream = null; }
    if (this.out) { try { this.out.close(); } catch (e) {} this.out = null; }
  }
}

// Every Tiliqua on the bus, as connected transports in board order. Four boards on four USB cables
// is the supported way to play more than four parts: the engine folds MIDI channels onto parts with
// `ch = ps[0:2]` (core/synth.x), so every board owns channels 1-4 of its own cable, and the panel
// stacks them into 16 parts. Nothing in the gateware knows this is happening.
//
// Board order is `MIDIPort.id` order. The order itself means nothing -- it is not slot order, not
// cable order, not the order they were plugged in -- but it is *stable* for an unchanged USB
// topology, so Board 3 is still Board 3 after a reload. Which physical box that is, the panel's
// IDENTIFY button answers by ear.
//
// The audio pairing is arbitrary and cannot be otherwise: `MIDIOutput` comes from CoreMIDI and
// `MediaDeviceInfo` from CoreAudio, the browser exposes nothing that spans the two, and all four
// boards enumerate under the same name. It does not matter. Every board's stream is summed into
// one output, so "my stream" and "the next board's stream" are the same sound arriving twice; the
// only property that has to hold is that the assignment is a bijection -- which the single `find`
// this replaced got wrong, handing all four transports one deviceId and capturing one board four
// times while three played to nobody.
async function discoverTiliquas() {
  const access = await midiAccess();
  const outs = [...access.outputs.values()].filter((o) => matches(o.name))
    .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  if (!outs.length) throw new Error('no "Tiliqua XLS32" MIDI output — is the bitstream loaded?');
  // Ask for audio before the picker closes, so both prompts land in the same gesture. Labels are
  // empty until some mic permission exists, which is why this opens a throwaway default stream
  // first and only then goes looking for the boards by name. One probe covers all of them.
  const probe = await navigator.mediaDevices.getUserMedia({ audio: true });
  probe.getTracks().forEach((t) => t.stop());
  const ins = (await navigator.mediaDevices.enumerateDevices())
    .filter((d) => d.kind === 'audioinput' && matches(d.label))
    .sort((a, b) => (a.deviceId < b.deviceId ? -1 : a.deviceId > b.deviceId ? 1 : 0));
  if (!ins.length) throw new Error('no "Tiliqua XLS32" audio input — check the OS sound settings');
  // Fewer inputs than outputs means something else holds one (another tab, a DAW). That board
  // still plays -- it is only unheard through the browser -- so it is kept, without a deviceId.
  const links = outs.map((o, i) => new TiliquaTransport(o, ins[i] ? ins[i].deviceId : null, i));
  for (const l of links) await l.connect();
  return links;
}

// How many boards, without asking for anything. Same contract as `detectTiliqua()` below: this runs
// at page load, so a prompt here would be the page demanding MIDI access from a passer-by. 0 is
// what a first visit gets, and the panel draws one board's worth until POWER says otherwise.
async function countTiliquas() {
  if (!navigator.requestMIDIAccess) return 0;
  try {
    if ((await navigator.permissions.query({ name: 'midi' })).state !== 'granted') return 0;
  } catch (e) { return 0; }                // no 'midi' permission name here; asking is the only way
  const access = await midiAccess();       // already granted, so this resolves without a prompt
  return [...access.outputs.values()].filter((o) => matches(o.name)).length;
}

// ---------- Basys 3: Web Serial ----------

// Align the continuous stereo stream to its 4-byte frame boundary and hand back whole frames.
// A port of `Aligner` in host/transport/uart.py -- same evidence, same thresholds, same re-lock
// interval. The board stamps a 1-bit channel marker in each sample's LSB (L=0, R=1), so the offset
// whose de-interleaved samples read 0,1,0,1,... fixes byte alignment and L/R order with one piece
// of evidence. Re-locking is not belt-and-braces: the frame phase demonstrably shifts mid-stream
// on this link (the M28a rail bug), and a stream locked once and never re-checked decodes every
// byte after the shift as uniform full-scale noise.
//
// "A port" used to be a claim and nothing more. `webui/check_aligner.py` and `aligner_check.html`
// now run both sides over the same capture of real board bytes -- one with a genuine mid-stream
// phase shift in it -- and compare the SHA-256 of the aligned output, so the two agree byte for
// byte or the check fails.
class Aligner {
  constructor() {
    this.buf = new Uint8Array(0);
    this.locked = false;
    this.since = 0;
    this.tail = new Uint8Array(0);                  // rolling copy of what went out, to re-score
    this.skip = 0;                                  // bytes still owed to a phase step
  }

  _score(off, b) {
    // fraction of samples whose LSB marker mismatches the expected L,R,L,R (0,1,0,1) pattern
    const end = Math.min(off + 4000, b.length - 1);
    let n = 0, bad = 0;
    for (let i = off, k = 0; i < end; i += 2, k++) {
      if (((b[i] | (b[i + 1] << 8)) & 1) !== (k & 1)) bad++;
      n++;
    }
    return n < 4 ? 1e12 : bad / n;
  }

  _best(b) {                                        // first minimum wins, as Python's min() does
    let best = 0, bs = this._score(0, b);
    for (let o = 1; o < 4; o++) { const s = this._score(o, b); if (s < bs) { bs = s; best = o; } }
    return best;
  }

  // The newest 2 kB of the stream, frame-aligned at index 0, for the re-lock to score. `tail` is
  // what has gone out and `buf` what has not; both begin on a frame boundary and they are
  // contiguous, so joined they are one view of the stream at the phase in force. Taking the END of
  // it is what makes the heal quick -- a window anchored at the front is still mostly pre-shift
  // bytes and votes to stay where it is.
  _window() {
    const w = new Uint8Array(this.tail.length + this.buf.length);
    w.set(this.tail); w.set(this.buf, this.tail.length);
    return w.length > 2048 ? w.subarray((w.length - 2048) & ~3) : w;
  }

  feed(data) {
    if (this.skip) {                                // a step the last check could not fully take
      const k = Math.min(this.skip, data.length);
      data = data.subarray(k); this.skip -= k;
    }
    const merged = new Uint8Array(this.buf.length + data.length);
    merged.set(this.buf); merged.set(data, this.buf.length);
    this.buf = merged;
    if (!this.locked) {
      if (this.buf.length < 4096) return null;      // too little to score four phases honestly
      const best = this._best(this.buf);
      if (best) this.buf = this.buf.subarray(best);
      this.locked = true;
    }
    this.since += data.length;
    if (this.since >= 4096) {                       // periodic re-lock (heals a byte drop)
      const w = this._window();
      if (w.length >= 512) {        // 128 samples; below that the four scores are not separated
        this.since = 0;
        // Only move on strong evidence. A window straddling the shift scores badly at every
        // offset, and stepping on that would cost a frame and land nowhere; halving the score is
        // a bar only a window wholly past the shift clears.
        const s0 = this._score(0, w), best = this._best(w);
        if (best !== 0 && this._score(best, w) < s0 * 0.5) {
          const k = Math.min(best, this.buf.length);
          this.buf = this.buf.subarray(k); this.skip = best - k;
          this.tail = new Uint8Array(0);            // scored, and stale: next check wants post-step
        }
      }
    }
    const n = this.buf.length & ~3;                 // whole 4-byte stereo frames only
    if (n < 4) return null;
    const out = this.buf.slice(0, n);
    this.buf = this.buf.subarray(n);
    const t = new Uint8Array(this.tail.length + out.length);
    t.set(this.tail); t.set(out, this.tail.length);
    this.tail = t.length > 2048 ? t.slice(t.length - 2048) : t;
    return out;
  }
}

class Basys3Transport {
  constructor() {
    this.kind = 'basys3'; this.label = 'Basys 3'; this.sr = 32000; this.timed = false;
    this.port = null; this.writer = null; this.reader = null;
    this.align = new Aligner(); this.node = null; this.pending = new Set(); this.running = false;
  }

  // The Basys 3's FT2232H enumerates TWO serial ports: channel A is JTAG, channel B is our UART.
  // `getInfo()` returns only vendor/product ids, identical on both, so nothing in Web Serial can
  // tell them apart -- and channel A opens perfectly happily, then does nothing at all. Picking it
  // is silent: no audio, no LEDs, no error. So the port is identified by BEHAVIOUR. The gateware
  // streams unconditionally (silence is still 32000 centred frames a second), so a port that sends
  // nothing for 400 ms is the wrong one.
  async _isStreaming(port) {
    try {
      await port.open({ baudRate: 2000000, dataBits: 8, stopBits: 1,
                        parity: 'none', flowControl: 'none' });
    } catch (e) { return false; }               // already open elsewhere, or gone
    const reader = port.readable.getReader();
    let n = 0;
    const timer = setTimeout(() => reader.cancel().catch(() => {}), 400);
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        n += value.length;
        if (n >= 4096) break;                   // ~32 ms of audio: nothing else on this bus talks
      }
    } catch (e) { /* fall through with whatever arrived */ }
    clearTimeout(timer);
    try { reader.releaseLock(); } catch (e) {}
    if (n >= 4096) return true;
    await port.close();
    return false;
  }

  async connect() {
    if (!navigator.serial) throw new Error('this browser has no Web Serial');
    // A port already granted in an earlier session comes back without a picker; only the first
    // visit needs one, and a picker cannot be opened outside a gesture anyway. Both FTDI channels
    // may be granted, so try them all rather than trusting the order.
    // 2 Mbaud is 100 MHz / 50 -- fast enough for 32 kHz stereo in real time, and not a rate
    // macOS termios can express, which is why the Python side needs an IOSSIOSPEED ioctl.
    // Web Serial takes the number and does whatever the platform needs underneath.
    this.port = null;
    for (const p of await navigator.serial.getPorts()) {
      if (await this._isStreaming(p)) { this.port = p; break; }
      // A remembered grant for the JTAG channel would win the race on every future visit.
      if (p.forget) await p.forget().catch(() => {});
    }
    if (!this.port) {
      const p = await navigator.serial.requestPort();
      if (!await this._isStreaming(p)) {
        if (p.forget) await p.forget().catch(() => {});
        throw new Error('that port is silent — the Basys 3 shows two, and only the second ' +
                        '(channel B) is the UART; the other is JTAG. Try the other one.');
      }
      this.port = p;
    }
    this.writer = this.port.writable.getWriter();
    this.label = 'Basys 3 · UART 2 Mbaud';
  }

  sendMidi(bytes, when) {
    // No hardware scheduler on this path, so `when` is honoured by timer and the jitter is the
    // main thread's. Audible only if a tab is starved; the alternative is a worker holding the
    // port, which would move every write off the thread that owns the UI state it reads.
    const dt = when ? when - performance.now() : 0;
    if (dt <= 1) { this._write(bytes); return; }
    const id = setTimeout(() => { this.pending.delete(id); this._write(bytes); }, dt);
    this.pending.add(id);
  }

  cancelPending() { for (const id of this.pending) clearTimeout(id); this.pending.clear(); }

  _write(bytes) {
    if (!this.writer) return;
    // Fire and forget: back-pressure on 3 bytes at 2 Mbaud is not a real condition, and awaiting
    // here would serialise note-ons behind whatever the OS buffer is doing.
    this.writer.write(new Uint8Array(bytes)).catch(() => {});
  }

  async attachAudio(ctx) {
    await ctx.audioWorklet.addModule('worklet.js?' + (window.VERSION || ''));
    this.node = new AudioWorkletNode(ctx, 'pcm-player',
                                     { outputChannelCount: [2], processorOptions: { sr: this.sr } });
    this.running = true;
    this._pump();                                   // reads until close(); never awaited
    return this.node;
  }

  async _pump() {
    while (this.running && this.port && this.port.readable) {
      this.reader = this.port.readable.getReader();
      try {
        for (;;) {
          const { value, done } = await this.reader.read();
          if (done) break;
          const frames = this.align.feed(value);
          if (!frames || !this.node) continue;
          const n = frames.length >> 2;
          const L = new Float32Array(n), R = new Float32Array(n);
          for (let i = 0; i < n; i++) {             // raw unsigned 16-bit LE, centred 32768
            L[i] = ((frames[4 * i]     | (frames[4 * i + 1] << 8)) - 32768) / 32768;
            R[i] = ((frames[4 * i + 2] | (frames[4 * i + 3] << 8)) - 32768) / 32768;
          }
          this.node.port.postMessage({ L, R }, [L.buffer, R.buffer]);
        }
      } catch (e) {
        // A device unplugged mid-read throws here. Fall out of the loop rather than spin.
        this.running = false;
      } finally {
        try { this.reader.releaseLock(); } catch (e) {}
        this.reader = null;
      }
    }
  }

  async close() {
    this.running = false;
    this.cancelPending();
    if (this.reader) { try { await this.reader.cancel(); } catch (e) {} }
    if (this.writer) { try { this.writer.releaseLock(); } catch (e) {} this.writer = null; }
    if (this.port) { try { await this.port.close(); } catch (e) {} this.port = null; }
  }
}

// ---------- which board is plugged in ----------
// Each returns 'yes' | 'no' | 'unknown', and MUST NOT prompt: this runs before the user has said
// what they want, so a prompt here would be the page asking for MIDI access from someone who owns
// a Basys 3. 'unknown' is therefore the honest answer whenever the only way to find out is to ask
// -- most importantly on a first visit, when nothing has been granted yet and the picker is
// unavoidable. Both APIs go quiet in the same way: they will only describe hardware the user has
// already pointed at once.

async function detectTiliqua() {
  if (!navigator.requestMIDIAccess) return 'no';
  let granted = false;
  try {
    granted = (await navigator.permissions.query({ name: 'midi' })).state === 'granted';
  } catch (e) {
    return 'unknown';                    // no 'midi' permission name here; asking is the only way
  }
  if (!granted) return 'unknown';
  const access = await midiAccess();     // already granted, so this resolves without a prompt
  for (const o of access.outputs.values()) if (matches(o.name)) return 'yes';
  return 'no';                           // permission held and the board is genuinely not there
}

// The FT2232H, by its ids. Deliberately narrow: a false 'yes' would auto-connect the wrong device,
// where a false 'unknown' only costs the picker that was going to be shown anyway.
const FTDI_VID = 0x0403, FT2232H_PID = 0x6010;

async function detectBasys3() {
  if (!navigator.serial) return 'no';
  const ports = await navigator.serial.getPorts();
  for (const p of ports) {
    if (p.connected === false) continue;             // granted once, not plugged in now
    const i = p.getInfo() || {};
    if (i.usbVendorId === FTDI_VID && i.usbProductId === FT2232H_PID) return 'yes';
  }
  return 'unknown';                      // nothing granted -> the picker, then requestPort()
}

// `connectAll` returns connected transports, plural. Only the Tiliqua can be plural; the Basys 3
// answers with a list of one so app.js has a single shape to hold. Mixing the two is not offered
// and could not work: there is one AudioContext and it has one sample rate.
const TRANSPORTS = {
  tiliqua: { connectAll: discoverTiliquas, name: 'Tiliqua',
             hint: 'USB MIDI out + UAC2 audio in · 48 kHz · up to 4 boards',
             ok: () => !!navigator.requestMIDIAccess && !!navigator.mediaDevices,
             detect: detectTiliqua, count: countTiliquas },
  basys3:  { connectAll: async () => { const t = new Basys3Transport(); await t.connect(); return [t]; },
             name: 'Basys 3',
             hint: 'USB serial, 2 Mbaud · 32 kHz',
             ok: () => !!navigator.serial,
             detect: detectBasys3, count: async () => 0 },
};

// The whole registry at once: { key: 'yes'|'no'|'unknown' }. Both probes are independent and one
// of them may be waiting on a MIDIAccess, so they run together.
async function detectBoards() {
  const keys = Object.keys(TRANSPORTS);
  const states = await Promise.all(keys.map((k) =>
    (TRANSPORTS[k].ok() ? TRANSPORTS[k].detect() : Promise.resolve('no')).catch(() => 'unknown')));
  return Object.fromEntries(keys.map((k, i) => [k, states[i]]));
}

window.XLS32 = { TRANSPORTS, detectBoards, midiAccess, Aligner, countTiliquas };
