// Adaptive-resampling PCM player. The board streams audio at ~85-90% of real time (the
// ÷4 clock-enable adds per-sample handshake overhead), and the rate varies, so a fixed
// playback rate under/overruns -> periodic glitches. We buffer samples in a ring and read
// them with a fractional step that TRACKS THE MEASURED ARRIVAL RATE (via currentTime), plus
// a tiny buffer-level trim. Consumption then matches arrival at any rate -> smooth, no
// glitches. Trade-off: pitch tracks the board's real-time rate (slightly flat when it can't
// keep up) — a board/UART-throughput limit no client resampler can undo.
// `sampleRate` and `currentTime` are globals in the AudioWorklet scope.
const N = 1 << 16;                          // per-channel ring size (power of two) ~1.5 s
class PCMPlayer extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ringL = new Float32Array(N);       // stereo: one ring per channel sharing ONE rpos, so
    this.ringR = new Float32Array(N);       // L/R stay phase-locked through the resampler
    this.wpos = 0; this.rpos = 0;           // absolute write / (fractional) read positions
    this.target = Math.round(0.20 * 28000); // ~200 ms of buffered source (board ~28 kHz)
    this.playing = false;
    this.arr = 0; this.lastT = 0; this.lastW = 0;   // arrival-rate estimator (samples/s)
    this.availAvg = 0;                              // smoothed buffer level (kills per-chunk jitter)
    this.port.onmessage = (e) => {
      const d = e.data;
      if (d === 'reset') { this.wpos = 0; this.rpos = 0; this.playing = false; this.arr = 0; this.lastT = 0; this.availAvg = 0; return; }
      const L = d.L, R = d.R, n = L.length;         // de-interleaved upstream in app.js
      for (let k = 0; k < n; k++) {
        this.ringL[this.wpos & (N - 1)] = L[k];
        this.ringR[this.wpos & (N - 1)] = R[k];
        this.wpos++;
      }
      if (this.wpos - this.rpos > N - 4) this.rpos = this.wpos - (N - 4);   // overflow: drop oldest
    };
  }
  process(inputs, outputs) {
    const outL = outputs[0][0], outR = outputs[0][1] || outputs[0][0];
    if (!outL) return true;
    if (this.lastT === 0) { this.lastT = currentTime; this.lastW = this.wpos; }
    const dt = currentTime - this.lastT;
    if (dt >= 0.5) {                          // slow, heavily-smoothed rate estimate -> stable step
      const r = (this.wpos - this.lastW) / dt;
      this.arr = this.arr ? this.arr * 0.9 + r * 0.1 : r;
      this.lastT = currentTime; this.lastW = this.wpos;
    }
    const avail = this.wpos - this.rpos;
    if (!this.playing) {
      if (avail >= this.target) this.playing = true;
      else { outL.fill(0); outR.fill(0); return true; }
    }
    this.availAvg = this.availAvg ? this.availAvg * 0.995 + avail * 0.005 : avail;
    const base = (this.arr > 1000 ? this.arr : 28000) / sampleRate;   // match measured arrival
    const err = Math.max(-1, Math.min(1, (this.availAvg - this.target) / this.target));
    const step = base * (1 + 0.004 * err);   // very gentle trim -> pitch variation imperceptible
    for (let i = 0; i < outL.length; i++) {
      if (this.wpos - this.rpos < 2) { outL.fill(0, i); outR.fill(0, i); this.playing = false; break; }
      const i0 = Math.floor(this.rpos), frac = this.rpos - i0;
      const aL = this.ringL[i0 & (N - 1)], bL = this.ringL[(i0 + 1) & (N - 1)];
      const aR = this.ringR[i0 & (N - 1)], bR = this.ringR[(i0 + 1) & (N - 1)];
      outL[i] = aL + (bL - aL) * frac;
      outR[i] = aR + (bR - aR) * frac;
      this.rpos += step;
    }
    return true;
  }
}
registerProcessor('pcm-player', PCMPlayer);
