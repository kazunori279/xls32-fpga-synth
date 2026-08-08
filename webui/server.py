#!/usr/bin/env python3
"""Local bridge between the browser P5 UI and whichever board is selected.

Owns the board's link (nothing else can while this runs) and fans stereo PCM frames out
to every connected browser. The browser sends raw MIDI bytes up the same WebSocket; we
write them straight to the board -> real-time MIDI input.

    uv run python webui/server.py                     # Basys 3, over the 2 Mbaud UART
    XLS32_BOARD=tiliqua uv run python webui/server.py # Tiliqua, over USB Audio Class 2

Board-agnostic since M27. Until then this file opened /dev/cu.usbserial-* itself, ran its
own reader thread, and guessed the UART's byte alignment inline -- so the UI worked on
exactly one board while the graded suite already ran on two. All of that now lives behind
`Transport.stream_start` (host/transport/base.py); what is left here is the part that was
never board-specific. The one wire format that does stay fixed is the browser's:
interleaved L,R unsigned 16-bit LE centred 32768, converted in `_on_frames`.
"""
import os, sys, asyncio, threading, time, json, contextlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "host"))       # import the project's host helpers
sys.path.insert(0, str(HERE.parent / "presetgen"))  # import the demo generator (for /api/demo)
from synth import BOARD, open_transport         # noqa: E402  (the board's audio/MIDI link)
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

FRAME = 512          # STEREO frames per PCM chunk pushed to clients (~16 ms @ 32 kHz)
GLITCH = 18000       # sample-to-sample jump that signals a dropout/misalignment


class Bridge:
    def __init__(self):
        self.tp = None                            # host/transport Transport, or None if no board
        self.sr = BOARD.sr                        # what the browser has to resample from
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
        self._acap = 16384                        # ~128 ms cap (safety for bursty reads)
        self._aprime = 2560                       # ~20 ms cushion before playback -> low latency
        self._primed = False
        self.audio_dev = None                     # local output device index (None = system default)
        self._gain = 0.5                          # master OUTPUT gain for LOCAL play (final mix, 0..1); default half
        # own board-rate -> device-rate linear resampler (PortAudio's internal SRC distorted the audio)
        self._ratio = 1.0                         # board sr / device_samplerate
        self._rpos = 0.0                          # fractional read phase carried across callbacks
        self._ibuf = None                         # decoded float32 stereo input buffer (persists)
        self._caprate = 44100
        self._capbuf = None                       # debug: capture resampled output to a WAV
        self._demo_stop = None                    # server-side demo sequencer (tight timing, no browser jitter)
        self._demo_thread = None
        self._demo_mute = set()                   # parts with their UI LED off: drop their note-ons mid-song
        self._under = self._over = self._maxfill = 0

    def open(self):
        try:
            self.tp = open_transport().open()
            self.sr = self.tp.sr
            print(f"[bridge] {BOARD.name} open over {BOARD.transport} at {self.sr} Hz")
        except (SystemExit, Exception) as e:       # find_port() exits; PortAudio/mido raise
            print(f"[bridge] no board ({e}); UI will serve without audio/MIDI")
            self.tp = None

    def write_midi(self, data: bytes):
        if self.tp is None:
            return
        with self.wlock:
            with contextlib.suppress(Exception):
                self.tp.send_midi(data)

    def _on_frames(self, frames):
        """One chunk off the transport -> the browser's wire format, and nowhere else.

        `frames` is (n, 2) signed; the browser has always read interleaved L,R unsigned
        16-bit LE centred 32768, and keeping that is why app.js and /api/capture did not
        have to change when this stopped being a UART.
        """
        chunk = (np.clip(frames, -32768, 32767) + 32768).astype("<u2").tobytes()
        if self.local_mode:                        # LOCAL: play on this machine's audio device
            self._feed_local(chunk)
        else:                                      # WEB: stream to the browser over the WebSocket
            self._broadcast(chunk)

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
        if self.tp is None:
            return
        if np is None:                             # both transports decode with numpy anyway
            print("[bridge] numpy missing; MIDI will work, audio will not")
            return
        self.tp.stream_start(self._on_frames, chunk=FRAME)

    def stop(self):
        self.stop_demo()
        if self.tp is not None:
            with contextlib.suppress(Exception):
                self.tp.close()
            self.tp = None

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

    def _audio_cb(self, outdata, frames, t, status):
        ratio = self._ratio
        need_in = int(self._rpos + frames * ratio) + 2      # input frames needed this call
        with self._alock:
            if not self._primed:                  # wait for a cushion before starting -> no startup gaps
                if len(self._abuf) < self._aprime:
                    outdata[:] = 0; return
                self._primed = True
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
        with self._alock:                           # drop buffered backlog -> start on the current stream
            self._abuf = bytearray()
        self._primed = False
        self._under = self._over = self._maxfill = 0
        # open at the DEVICE's native rate and resample board-rate->native ourselves (clean linear,
        # like the browser worklet) instead of relying on PortAudio's internal SRC (distorted).
        def _open():
            dev_rate = 44100
            with contextlib.suppress(Exception):
                info = sd.query_devices(self.audio_dev if self.audio_dev is not None else sd.default.device[1])
                dev_rate = int(round(info["default_samplerate"]))
            self._ratio = float(self.sr) / dev_rate
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

    def _rescan_midi(self):
        """Open any host MIDI input that has appeared, close any that has gone.

        Ports used to be enumerated exactly once, on the OFF->ON transition of LOCAL play. A
        keyboard plugged in after that was never opened, so nothing arrived for `_on_local_midi`
        to re-address -- which from the UI is indistinguishable from the PART chips being
        ignored. Called on every /api/local hit (GET included), so plugging in and then touching
        anything in the UI is enough.
        """
        if mido is None:                          # host MIDI in needs python-rtmidi; audio works without
            return
        names = []
        with contextlib.suppress(Exception):
            names = mido.get_input_names()
        have = {p.name for p in self._midi_ins}
        for name in names:
            if name in have:
                continue
            with contextlib.suppress(Exception):
                self._midi_ins.append(mido.open_input(name, callback=self._on_local_midi))
                print(f"[bridge] host MIDI in opened: {name} -> parts {self.local_chans}")
        for p in list(self._midi_ins):
            if p.name not in names:               # unplugged: drop it or the next scan reopens a dead port
                with contextlib.suppress(Exception):
                    p.close()
                self._midi_ins.remove(p)
                print(f"[bridge] host MIDI in gone: {p.name}")

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
                self._rescan_midi()
                self.local_mode = True
                print(f"[bridge] LOCAL play ON: audio -> {self._devname()}; midi in: {[p.name for p in self._midi_ins]}")
            else:
                self._rescan_midi()                # hot-plug: the UI posts here on every part change
                if dev_changed:
                    self._open_astream()           # hot-switch output device (MIDI unchanged)
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
    def set_demo_mute(self, mute):
        """Parts whose LED the UI turned off. The browser gates its own keyboard notes with the
        same set; this is the half the browser can't do, since the song plays from _demo_run."""
        new = {int(c) & 0x0f for c in mute}
        for ch in new - self._demo_mute:           # newly muted: kill whatever it is holding right now
            for note in range(128):                # (the engine has no CC123 all-notes-off)
                self.write_midi(bytes([0x80 | ch, note, 0]))
        self._demo_mute = new

    def start_demo(self, setup, events, loop_ms, mute=()):
        self.stop_demo()
        self._demo_mute = {int(c) & 0x0f for c in mute}
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
                b = bytes(m)
                if (b[0] & 0xF0) == 0x90 and (b[0] & 0x0F) in self._demo_mute:
                    continue                       # muted part: swallow the note-on (offs still go, so nothing hangs)
                self.write_midi(b)
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
        # `midi_inputs` is what the machine offers; `midi_open` is what this bridge actually holds
        # and re-addresses to `chans`. The UI shows the second one -- a port that exists but is not
        # open is exactly the case that looks like the PART chips are broken.
        return {"on": self.local_mode, "ch": (self.local_chans[0] if self.local_chans else 0),
                "chans": list(self.local_chans), "available": bool(sd and np),
                "device": self.audio_dev, "audio_device": self._devname(),
                "output_devices": devs, "midi_inputs": ins,
                "midi_open": [p.name for p in self._midi_ins]}


bridge = Bridge()


@contextlib.asynccontextmanager
async def lifespan(app):
    bridge.start(asyncio.get_running_loop())
    yield
    bridge.stop()                 # the Tiliqua's UAC2 device wedges if it is not released cleanly


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def revalidate_assets(request, call_next):
    # Serve the UI assets with `no-cache` (revalidate each load; 304 when unchanged) so a code
    # change always reaches the browser — the old fixed ?v query in index.html could pin stale JS.
    resp = await call_next(request)
    p = request.url.path
    # .json covers demos.json / presets_*.json, which the UI itself writes back to.
    if p == "/" or p.endswith((".html", ".js", ".css", ".json")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/api/spec")
async def api_spec():
    # `sr` rides along because the browser cannot guess it: the front-end hardcoded 32 kHz
    # until M27, which is right for the Basys 3 and 2/3 of the truth on the Tiliqua. Take it
    # from the open transport rather than from BOARD, so what the worklet is told is the rate
    # frames are actually arriving at.
    return JSONResponse({**synthspec.spec(), "sr": bridge.sr})


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
    bridge.start_demo(d.get("setup", []), d.get("events", []), float(d.get("loop_ms", 4000)),
                      d.get("mute", []))
    return {"ok": True}


@app.post("/api/demo_mute")
async def api_demo_mute(req: Request):
    # live per-part mute for the running song: the UI's part LEDs, applied to the sequencer.
    d = await req.json()
    bridge.set_demo_mute(d.get("mute", []))
    return {"ok": True}


@app.post("/api/demo_stop")
async def api_demo_stop():
    bridge.stop_demo()
    return {"ok": True}


@app.get("/api/local")
async def api_local_get():
    # current local-play state + the machine's audio out / MIDI in (for the UI toggle).
    # The UI polls this, so it doubles as the hot-plug scan: a keyboard connected mid-session
    # is picked up here rather than only on the next LOCAL off/on cycle.
    if bridge.local_mode:
        bridge._rescan_midi()
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
    return {"connected": bridge.tp is not None, "board": BOARD.name, "sr": bridge.sr,
            "device": getattr(bridge.tp, "dev", None) or BOARD.transport,
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
            # A closing client arrives as a `websocket.disconnect` *message*, not an exception;
            # looping round to receive() again is what raises RuntimeError and logs an ASGI
            # traceback on every tab close. Leave on the message instead.
            if msg["type"] == "websocket.disconnect":
                break
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
        # CancelledError is a BaseException since 3.8, so `suppress(Exception)` does not catch
        # the one thing `await task` is guaranteed to raise here.
        with contextlib.suppress(asyncio.CancelledError):
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
