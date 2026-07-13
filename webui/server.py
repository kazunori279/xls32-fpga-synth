#!/usr/bin/env python3
"""Local bridge between the browser P5 UI and the Basys 3 synth over USB-UART.

Owns the single serial port (nothing else can while this runs). One reader thread
continuously drains the FTDI RX buffer (a dropped byte misaligns the 16-bit stream),
locks byte-alignment, and fans signed-16 PCM frames out to every connected browser.
The browser sends raw MIDI bytes up the same WebSocket; we write them straight to the
board -> real-time MIDI input. Run:  uv run python webui/server.py  (then open :8765)
"""
import os, sys, asyncio, threading, time, array, json, contextlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "host"))       # import the project's uartaudio helpers
sys.path.insert(0, str(HERE.parent / "presetgen"))  # import the demo generator (for /api/demo)
import uartaudio                                # noqa: E402
import synthspec                                # noqa: E402
import build_demos                              # noqa: E402  (make_random for the DEMO "replace")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request  # noqa: E402
from fastapi.responses import JSONResponse                    # noqa: E402
from fastapi.staticfiles import StaticFiles                   # noqa: E402
import uvicorn                                                # noqa: E402

# LOCAL PLAY (optional): server plays the board's audio on THIS machine's audio device and reads a
# local MIDI keyboard directly -> far lower latency than the browser WS + AudioWorklet round-trip.
# Degrades gracefully if the native backends aren't present (the UI just won't offer local mode).
try:
    import numpy as np
    import sounddevice as sd
except Exception:
    np = sd = None
try:
    import mido
except Exception:
    mido = None

FRAME = 512          # STEREO frames per PCM chunk pushed to clients (~18 ms @ 28 kHz)
GLITCH = 18000       # sample-to-sample jump that signals a dropout/misalignment


class Aligner:
    """Align the continuous STEREO 16-bit stream to its 4-byte frame boundary (Llo Lhi Rlo Rhi)
    and forward the RAW aligned bytes (the browser de-interleaves + decodes). The board stamps a
    1-bit channel marker in each sample's LSB (L=0, R=1), so the correct frame offset is the one
    whose de-interleaved samples show LSBs 0,1,0,1,... (L,R,L,R). This nails BOTH byte-alignment
    (odd offsets scramble the pattern) AND L/R order (offset 2 would start on R) unambiguously.
    Re-checked periodically so a mid-stream byte drop self-heals within ~0.1 s."""
    def __init__(self):
        self.buf = bytearray()
        self.locked = False
        self.since = 0

    def _score(self, off):
        # fraction of samples whose LSB marker mismatches the expected L,R,L,R (0,1,0,1) pattern
        b = self.buf; end = min(off + 4000, len(b) - 1)
        vals = [b[i] | (b[i + 1] << 8) for i in range(off, end, 2)]
        if len(vals) < 4:
            return 1e12
        bad = sum(1 for k, v in enumerate(vals) if (v & 1) != (k & 1))
        return bad / len(vals)

    def feed(self, data: bytes) -> bytes:
        self.buf += data
        if not self.locked:
            if len(self.buf) < 4096:
                return b""
            best = min(range(4), key=self._score)
            if best:
                del self.buf[:best]
            self.locked = True
        self.since += len(data)
        if self.since >= 8192 and len(self.buf) >= 4100:   # periodic re-lock (heals a byte drop)
            self.since = 0
            best = min(range(4), key=self._score)
            if best != 0 and self._score(best) < self._score(0) * 0.5:
                del self.buf[:best]
        n = len(self.buf) & ~3        # whole 4-byte stereo frames only
        if n < 4:
            return b""
        out = bytes(self.buf[:n]); del self.buf[:n]
        return out


class Bridge:
    def __init__(self):
        self.fd = None
        self.dev = None
        self.wlock = threading.Lock()
        self.clients = set()                      # asyncio.Queue per websocket
        self.loop = None
        self.frames_sent = 0
        # LOCAL PLAY state: when on, the reader feeds the local audio device instead of the WS,
        # and a local MIDI keyboard is routed straight to the board, fanned out to every part in
        # local_chans (the play/layer set) so a stack of parts sounds the same note.
        self.local_mode = False
        self.local_chans = [0]
        self._astream = None
        self._abuf = bytearray()
        self._alock = threading.Lock()
        self._midi_ins = []
        self._acap = 16384                        # ~128 ms cap (safety for bursty FTDI reads)
        self._aprime = 2560                       # ~20 ms cushion before playback -> low latency
        self._primed = False
        self.audio_dev = None                     # local output device index (None = system default)
        self._gain = 0.5                          # master OUTPUT gain for LOCAL play (final mix, 0..1); default half
        # own 32 kHz -> device-rate linear resampler (PortAudio's internal SRC distorted the audio)
        self._ratio = 1.0                         # 32000 / device_samplerate
        self._rpos = 0.0                          # fractional read phase carried across callbacks
        self._ibuf = None                         # decoded float32 stereo input buffer (persists)
        self._caprate = 44100
        self._capbuf = None                       # debug: capture resampled output to a WAV
        self._demo_stop = None                    # server-side demo sequencer (tight timing, no browser jitter)
        self._demo_thread = None
        self._under = self._over = self._maxfill = 0

    def open(self):
        try:
            self.dev, self.fd = uartaudio.open_port(rw=True)
            print(f"[bridge] serial open: {self.dev}")
        except SystemExit as e:
            print(f"[bridge] no board ({e}); UI will serve without audio/MIDI")
            self.fd = None

    def write_midi(self, data: bytes):
        if self.fd is None:
            return
        with self.wlock:
            with contextlib.suppress(BlockingIOError, OSError):
                os.write(self.fd, data)

    def _reader(self):
        aln = Aligner()
        pend = bytearray()
        fbytes = FRAME * 4        # stereo: 4 bytes/frame (Llo Lhi Rlo Rhi)
        while True:
            if self.fd is None:
                time.sleep(0.2); continue
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                time.sleep(0.0005); continue
            except OSError:
                time.sleep(0.2); continue
            if not data:
                time.sleep(0.0005); continue
            try:                                   # never let a transient error kill the reader
                pend += aln.feed(data)             # (a dead reader = frozen audio in BOTH modes)
                while len(pend) >= fbytes:
                    chunk = bytes(pend[:fbytes]); del pend[:fbytes]
                    if self.local_mode:            # LOCAL: play on this machine's audio device
                        self._feed_local(chunk)
                    else:                          # WEB: stream to the browser over the WebSocket
                        self._broadcast(chunk)
            except Exception as e:
                print(f"[bridge] reader hiccup (continuing): {e}"); time.sleep(0.01)

    def _broadcast(self, frame: bytes):
        self.frames_sent += 1
        if not self.clients or self.loop is None:
            return
        for q in list(self.clients):
            self.loop.call_soon_threadsafe(self._offer, q, frame)

    @staticmethod
    def _offer(q, frame):
        if q.qsize() > 64:                        # slow client: drop oldest to bound latency
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()
        q.put_nowait(frame)

    def start(self, loop):
        self.loop = loop
        self.open()
        threading.Thread(target=self._reader, daemon=True).start()

    # ---- LOCAL PLAY: audio out + MIDI in on this machine ----
    def _feed_local(self, chunk: bytes):
        self.frames_sent += 1
        with self._alock:
            self._abuf += chunk
            if len(self._abuf) > self._acap:      # cap: drop oldest to bound latency
                del self._abuf[:len(self._abuf) - self._acap]
                self._over += 1
            if len(self._abuf) > self._maxfill:
                self._maxfill = len(self._abuf)

    def _sample_offset(self, buf):
        # find the 2-byte sample phase (0..3) whose L/R markers read 0,1,0,1 (board stamps sample
        # LSB: L=0, R=1). The stream is often 1 byte off; decoding at 0 straddles samples -> noise.
        m = min(len(buf) - 1, 512)
        if m < 8:
            return 0
        scores = []
        for off in range(4):
            vals = [(buf[off + 2 * i] | (buf[off + 2 * i + 1] << 8)) for i in range((m - off) // 2)]
            bad = sum(1 for k, v in enumerate(vals) if (v & 1) != (k & 1))
            scores.append(bad / max(1, len(vals)))
        best = min(range(4), key=lambda o: scores[o])
        return best if scores[best] < scores[0] * 0.5 else 0   # only shift if clearly better

    def _audio_cb(self, outdata, frames, t, status):
        ratio = self._ratio
        need_in = int(self._rpos + frames * ratio) + 2      # input frames needed this call
        with self._alock:
            if not self._primed:                  # wait for a cushion before starting -> no startup gaps
                if len(self._abuf) < self._aprime:
                    outdata[:] = 0; return
                self._primed = True
            off = self._sample_offset(self._abuf)  # self-heal byte-misalignment (else -> HF noise)
            if off:
                del self._abuf[:off]
            want = max(0, need_in - len(self._ibuf))
            avail = len(self._abuf) // 4
            take = min(want, avail)
            chunk = bytes(self._abuf[:take * 4]); del self._abuf[:take * 4]
        if take:                                  # board streams UNSIGNED-centered(32768) LE stereo
            dec = (np.frombuffer(chunk, dtype="<u2").astype(np.float32) - 32768.0).reshape(-1, 2)
            self._ibuf = np.concatenate([self._ibuf, dec]) if len(self._ibuf) else dec
        n = len(self._ibuf)
        if n < 2:
            outdata[:] = 0; self._under += 1; return
        outpos = self._rpos + np.arange(frames, dtype=np.float64) * ratio    # linear-interp positions
        if outpos[-1] > n - 1:                    # underrun: not enough input -> hold last sample
            self._under += 1
            outpos = np.minimum(outpos, n - 1.0)
        idx = np.floor(outpos).astype(np.int64)
        frac = (outpos - idx).astype(np.float32)[:, None]
        i1 = np.minimum(idx + 1, n - 1)
        out = (self._ibuf[idx] * (1.0 - frac) + self._ibuf[i1] * frac) * self._gain   # master output gain
        outdata[:] = np.clip(out, -32768.0, 32767.0).astype(np.int16)
        if self._capbuf is not None:              # debug: record exactly what goes to the device
            self._capbuf.append(outdata.copy())
        nxt = self._rpos + frames * ratio         # advance phase; drop consumed input frames
        drop = min(int(np.floor(nxt)), n)
        self._ibuf = self._ibuf[drop:]
        self._rpos = nxt - drop

    def _on_local_midi(self, msg):
        b = bytearray(msg.bytes())
        if not b:
            return
        if 0x80 <= b[0] < 0xf0:                    # channel-voice msg -> fan out to every playing part
            status = b[0] & 0xf0
            for ch in (self.local_chans or []):
                b[0] = status | (ch & 0x0f)
                self.write_midi(bytes(b))          # (drop clock/active-sensing spam)

    def _open_astream(self):                       # (re)open the output stream on self.audio_dev
        if self._astream is not None:
            with contextlib.suppress(Exception):
                self._astream.stop(); self._astream.close()
            self._astream = None
        with contextlib.suppress(Exception):        # drop buffered backlog -> start on the current stream
            import termios
            termios.tcflush(self.fd, termios.TCIFLUSH)
        with self._alock:
            self._abuf = bytearray()
        self._primed = False
        self._under = self._over = self._maxfill = 0
        # open at the DEVICE's native rate and resample 32kHz->native ourselves (clean linear,
        # like the browser worklet) instead of relying on PortAudio's internal SRC (distorted).
        def _open():
            dev_rate = 44100
            with contextlib.suppress(Exception):
                info = sd.query_devices(self.audio_dev if self.audio_dev is not None else sd.default.device[1])
                dev_rate = int(round(info["default_samplerate"]))
            self._ratio = 32000.0 / dev_rate
            self._caprate = dev_rate
            self._rpos = 0.0
            self._ibuf = np.zeros((0, 2), dtype=np.float32)
            s = sd.OutputStream(device=self.audio_dev, samplerate=dev_rate, channels=2,
                                dtype="int16", blocksize=512, callback=self._audio_cb)
            s.start()
            return s
        try:
            self._astream = _open()
        except Exception as e:
            # PortAudio caches the device list at init; while the server runs for hours, devices
            # come and go (Bluetooth, virtual audio) and the cache goes stale -> "Internal PortAudio
            # error [-9986]" on open even though the device is fine. Reinitialize PortAudio and retry.
            print(f"[bridge] OutputStream open failed ({e}); reinitializing PortAudio and retrying")
            with contextlib.suppress(Exception):
                sd._terminate(); sd._initialize()
            self._astream = _open()

    def set_local(self, on: bool, ch=None, device="keep", chans=None):
        if chans is not None:                          # the play/layer set (list of part indices)
            self.local_chans = [int(c) & 0x0f for c in chans] or [0]
        elif ch is not None:                           # back-compat: single part
            self.local_chans = [int(ch) & 0x0f]
        dev_changed = device != "keep" and device != self.audio_dev
        if device != "keep":
            self.audio_dev = device                # int index, or None = system default
        if on:
            if sd is None or np is None:
                raise RuntimeError("sounddevice/numpy not installed")
            if not self.local_mode:
                self._open_astream()
                self._midi_ins = []
                if mido is not None:                   # host MIDI in is optional (needs python-rtmidi);
                    with contextlib.suppress(Exception):   # LOCAL audio still works without it
                        for name in mido.get_input_names():
                            with contextlib.suppress(Exception):
                                self._midi_ins.append(mido.open_input(name, callback=self._on_local_midi))
                self.local_mode = True
                print(f"[bridge] LOCAL play ON: audio -> {self._devname()}; midi in: {[p.name for p in self._midi_ins]}")
            elif dev_changed:
                self._open_astream()               # hot-switch output device (MIDI unchanged)
                print(f"[bridge] LOCAL audio device -> {self._devname()}")
        elif self.local_mode:
            self.local_mode = False
            for p in self._midi_ins:
                with contextlib.suppress(Exception):
                    p.close()
            self._midi_ins = []
            if self._astream is not None:
                with contextlib.suppress(Exception):
                    self._astream.stop(); self._astream.close()
            self._astream = None
            print("[bridge] LOCAL play OFF -> back to browser (WS) audio")
        return self.local_state()

    def _devname(self):
        if sd is None:
            return None
        with contextlib.suppress(Exception):
            i = self.audio_dev if self.audio_dev is not None else sd.default.device[1]
            return sd.query_devices(i)["name"]
        return None

    # ---- server-side DEMO sequencer: browser-independent timing (no setInterval/WS jitter) ----
    def start_demo(self, setup, events, loop_ms):
        self.stop_demo()
        ev = threading.Event()
        self._demo_stop = ev
        self._demo_thread = threading.Thread(target=self._demo_run, args=(setup, events, loop_ms, ev), daemon=True)
        self._demo_thread.start()

    def stop_demo(self):
        if self._demo_stop is not None:
            self._demo_stop.set()
        t = self._demo_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=1.0)
        self._demo_stop = self._demo_thread = None
        for ch in range(4):                        # explicit note-offs so nothing hangs
            for note in range(128):                # (the engine doesn't implement CC123 all-notes-off)
                self.write_midi(bytes([0x80 | ch, note, 0]))

    def _demo_run(self, setup, events, loop_ms, stop):
        for m in setup:                            # apply the (customized) part patches + effects
            self.write_midi(bytes(m))
        if stop.wait(0.24):                        # let the patch burst land before the first note
            return
        loop_s = loop_ms / 1000.0
        while not stop.is_set():
            base = time.monotonic()
            for t_ms, m in events:                 # emit each note at its exact time (monotonic clock)
                dt = base + t_ms / 1000.0 - time.monotonic()
                if dt > 0 and stop.wait(dt):
                    return
                if stop.is_set():
                    return
                self.write_midi(bytes(m))
            rem = base + loop_s - time.monotonic()  # hold the bar length, then loop
            if rem > 0 and stop.wait(rem):
                return

    def local_state(self):
        devs = []
        with contextlib.suppress(Exception):
            if sd:
                for i, d in enumerate(sd.query_devices()):
                    if d["max_output_channels"] > 0:
                        devs.append({"index": i, "name": d["name"]})
        ins = []
        with contextlib.suppress(Exception):
            ins = mido.get_input_names() if mido else []
        return {"on": self.local_mode, "ch": (self.local_chans[0] if self.local_chans else 0),
                "chans": list(self.local_chans), "available": bool(sd and np),
                "device": self.audio_dev, "audio_device": self._devname(),
                "output_devices": devs, "midi_inputs": ins}


bridge = Bridge()


@contextlib.asynccontextmanager
async def lifespan(app):
    bridge.start(asyncio.get_running_loop())
    yield


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def revalidate_assets(request, call_next):
    # Serve the UI assets with `no-cache` (revalidate each load; 304 when unchanged) so a code
    # change always reaches the browser — the old fixed ?v query in index.html could pin stale JS.
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.endswith((".html", ".js", ".css")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/api/spec")
async def api_spec():
    return JSONResponse(synthspec.spec())


@app.get("/api/demo")
async def api_demo(genre: str, seed: int = 0):
    # a freshly generated procedural song in `genre` (for the DEMO player's per-song replace)
    return JSONResponse(build_demos.make_random(genre, seed))


@app.post("/api/demo_save")
async def api_demo_save(req: Request):
    # persist an edited demo song straight into demos.json — the single source of truth.
    # The client sends the whole song (name, notes, parts, effects); we upsert by name.
    data = await req.json()
    song = data.get("song")
    if not isinstance(song, dict) or not song.get("name"):
        return JSONResponse({"ok": False, "error": "need song object with a name"}, status_code=400)
    path = HERE / "static" / "demos.json"
    bank = json.loads(path.read_text()) if path.exists() else {"songs": []}
    songs = bank.setdefault("songs", [])
    for i, s in enumerate(songs):                              # replace if the name exists, else append
        if s.get("name") == song["name"]:
            songs[i] = song
            break
    else:
        songs.append(song)
    path.write_text(json.dumps(bank, indent=1))
    return {"ok": True, "saved": song["name"], "count": len(songs)}


@app.post("/api/gain")
async def api_gain(req: Request):
    # master OUTPUT gain for LOCAL play (final mix). WEB play scales in the browser's GainNode.
    d = await req.json()
    bridge._gain = max(0.0, min(1.0, float(d.get("gain", 1.0))))
    return {"gain": bridge._gain}


@app.post("/api/demo_play")
async def api_demo_play(req: Request):
    # server-side demo playback: the browser sends the (customized) setup CCs + timed note events;
    # the server sequences them with a monotonic clock -> steady timing regardless of the browser.
    d = await req.json()
    bridge.start_demo(d.get("setup", []), d.get("events", []), float(d.get("loop_ms", 4000)))
    return {"ok": True}


@app.post("/api/demo_stop")
async def api_demo_stop():
    bridge.stop_demo()
    return {"ok": True}


@app.get("/api/local")
async def api_local_get():
    # current local-play state + the machine's audio out / MIDI in (for the UI toggle)
    return bridge.local_state()


@app.post("/api/local")
async def api_local_set(req: Request):
    # toggle LOCAL play (server plays audio + reads MIDI here) vs WEB play (browser over WS)
    d = await req.json()
    try:
        return bridge.set_local(bool(d.get("on")), d.get("ch"), d.get("device", "keep"), d.get("chans"))
    except Exception as e:
        return JSONResponse({"on": bridge.local_mode, "error": str(e)}, status_code=500)


@app.post("/api/capture")
async def api_capture(req: Request):
    d = await req.json()
    secs = float(d.get("secs", 3))
    bridge._capbuf = []
    await asyncio.sleep(secs)
    buf = bridge._capbuf; bridge._capbuf = None
    import soundfile as sf
    y = np.concatenate(buf) if buf else np.zeros((1, 2), np.int16)
    sf.write("/tmp/local_out.wav", y, int(bridge._caprate))
    return {"frames": int(len(y)), "rate": int(bridge._caprate), "path": "/tmp/local_out.wav"}


@app.get("/api/status")
async def api_status():
    return {"connected": bridge.fd is not None, "device": bridge.dev,
            "clients": len(bridge.clients), "frames": bridge.frames_sent,
            "local": bridge.local_mode, "under": bridge._under, "over": bridge._over,
            "maxfill": bridge._maxfill, "acap": bridge._acap}


@app.websocket("/ws")
async def ws(socket: WebSocket):
    await socket.accept()
    q: asyncio.Queue = asyncio.Queue()
    bridge.clients.add(q)

    async def pump():                             # server -> client: PCM frames
        try:
            while True:
                frame = await q.get()
                await socket.send_bytes(frame)
        except (WebSocketDisconnect, RuntimeError):
            pass

    task = asyncio.create_task(pump())
    try:
        while True:                               # client -> server: MIDI bytes (or JSON)
            msg = await socket.receive()
            if msg.get("bytes") is not None:
                bridge.write_midi(msg["bytes"])
            elif msg.get("text") is not None:
                with contextlib.suppress(Exception):
                    d = json.loads(msg["text"])
                    if isinstance(d, list):       # [[status,d1,d2], ...] batch of MIDI msgs
                        for m in d:
                            bridge.write_midi(bytes(m))
    except WebSocketDisconnect:
        pass
    finally:
        bridge.clients.discard(q)
        task.cancel()
        with contextlib.suppress(Exception):
            await task


app.mount("/", StaticFiles(directory=str(HERE / "static"), html=True), name="static")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    # bind to localhost by default; set HOST=0.0.0.0 (or a specific LAN/Tailscale IP) to
    # reach it from other devices on the network
    host = os.environ.get("HOST", "127.0.0.1")
    # Web Audio's AudioWorklet needs a secure context (HTTPS or localhost). To reach the
    # UI from another device (e.g. over Tailscale), serve HTTPS by pointing SSL_CERT/SSL_KEY
    # at a cert (self-signed is fine — accept the one-time browser warning).
    ssl = {}
    if os.environ.get("SSL_CERT") and os.environ.get("SSL_KEY"):
        ssl = {"ssl_certfile": os.environ["SSL_CERT"], "ssl_keyfile": os.environ["SSL_KEY"]}
    uvicorn.run(app, host=host, port=port, log_level="info", **ssl)
