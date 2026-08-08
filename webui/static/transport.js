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
//     connect()     prompt, open, throw on refusal
//     sendMidi(b, when)   `when` is a performance.now() timestamp; omit for "now"
//     cancelPending()     drop anything scheduled but not yet sent (demo stop)
//     attachAudio(ctx)    -> an AudioNode carrying the board's stereo output
//     close()

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
  constructor() {
    this.kind = 'tiliqua'; this.label = 'Tiliqua'; this.sr = 48000; this.timed = true;
    this.out = null; this.stream = null; this.deviceId = null;
  }

  async connect() {
    const access = await midiAccess();
    for (const o of access.outputs.values()) if (matches(o.name)) { this.out = o; break; }
    if (!this.out) throw new Error('no "Tiliqua XLS32" MIDI output — is the bitstream loaded?');
    await this.out.open();
    // Ask for audio before the picker closes, so both prompts land in the same gesture. Labels
    // are empty until some mic permission exists, which is why this opens a throwaway default
    // stream first and only then goes looking for the board by name.
    const probe = await navigator.mediaDevices.getUserMedia({ audio: true });
    probe.getTracks().forEach((t) => t.stop());
    const devs = await navigator.mediaDevices.enumerateDevices();
    const hit = devs.find((d) => d.kind === 'audioinput' && matches(d.label));
    if (!hit) throw new Error('no "Tiliqua XLS32" audio input — check the OS sound settings');
    this.deviceId = hit.deviceId;
    this.label = 'Tiliqua · USB MIDI + UAC2';
  }

  sendMidi(bytes, when) {
    if (!this.out) return;
    // `send(data, when)` hands the timestamp to the MIDI service, which is the whole reason the
    // sequencer can be a lazy look-ahead loop: jitter in our setTimeout does not reach the wire.
    try { this.out.send(bytes, when); } catch (e) { /* port closed under us */ }
  }

  cancelPending() { if (this.out) { try { this.out.clear(); } catch (e) {} } }

  async attachAudio(ctx) {
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

// ---------- Basys 3: Web Serial ----------

// Align the continuous stereo stream to its 4-byte frame boundary and hand back whole frames.
// A verbatim port of `Aligner` in host/transport/uart.py:241 -- same evidence, same thresholds,
// same re-lock interval. The board stamps a 1-bit channel marker in each sample's LSB (L=0, R=1),
// so the offset whose de-interleaved samples read 0,1,0,1,... fixes byte alignment and L/R order
// with one piece of evidence. Re-locking is not belt-and-braces: the frame phase demonstrably
// shifts mid-stream on this link (the M28a rail bug), and a stream locked once and never
// re-checked decodes every byte after the shift as uniform full-scale noise.
class Aligner {
  constructor() { this.buf = new Uint8Array(0); this.locked = false; this.since = 0; }

  _score(off) {
    // fraction of samples whose LSB marker mismatches the expected L,R,L,R (0,1,0,1) pattern
    const b = this.buf, end = Math.min(off + 4000, b.length - 1);
    let n = 0, bad = 0;
    for (let i = off, k = 0; i < end; i += 2, k++) {
      if (((b[i] | (b[i + 1] << 8)) & 1) !== (k & 1)) bad++;
      n++;
    }
    return n < 4 ? 1e12 : bad / n;
  }

  _best() {
    let best = 0, bs = this._score(0);
    for (let o = 1; o < 4; o++) { const s = this._score(o); if (s < bs) { bs = s; best = o; } }
    return best;
  }

  feed(data) {
    const merged = new Uint8Array(this.buf.length + data.length);
    merged.set(this.buf); merged.set(data, this.buf.length);
    this.buf = merged;
    if (!this.locked) {
      if (this.buf.length < 4096) return null;      // too little to score four phases honestly
      const best = this._best();
      if (best) this.buf = this.buf.subarray(best);
      this.locked = true;
    }
    this.since += data.length;
    if (this.since >= 8192 && this.buf.length >= 4100) {
      this.since = 0;
      // Only move on strong evidence. A marginal win is noise, and stepping the phase costs a
      // frame every time; halving the score is the same bar the Python uses.
      const best = this._best();
      if (best !== 0 && this._score(best) < this._score(0) * 0.5) this.buf = this.buf.subarray(best);
    }
    const n = this.buf.length & ~3;                 // whole 4-byte stereo frames only
    if (n < 4) return null;
    const out = this.buf.slice(0, n);
    this.buf = this.buf.subarray(n);
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

const TRANSPORTS = {
  tiliqua: { make: () => new TiliquaTransport(), name: 'Tiliqua',
             hint: 'USB MIDI out + UAC2 audio in · 48 kHz',
             ok: () => !!navigator.requestMIDIAccess && !!navigator.mediaDevices,
             detect: detectTiliqua },
  basys3:  { make: () => new Basys3Transport(), name: 'Basys 3',
             hint: 'USB serial, 2 Mbaud · 32 kHz',
             ok: () => !!navigator.serial,
             detect: detectBasys3 },
};

// The whole registry at once: { key: 'yes'|'no'|'unknown' }. Both probes are independent and one
// of them may be waiting on a MIDIAccess, so they run together.
async function detectBoards() {
  const keys = Object.keys(TRANSPORTS);
  const states = await Promise.all(keys.map((k) =>
    (TRANSPORTS[k].ok() ? TRANSPORTS[k].detect() : Promise.resolve('no')).catch(() => 'unknown')));
  return Object.fromEntries(keys.map((k, i) => [k, states[i]]));
}

window.XLS32 = { TRANSPORTS, detectBoards, midiAccess, Aligner };
