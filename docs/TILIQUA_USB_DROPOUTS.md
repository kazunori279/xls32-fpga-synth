# Tiliqua R5 — USB Audio Class 2 capture: report **retracted**

**Status:** ⛔ **RETRACTED.** An earlier version of this file was sent to apf.audio on 2026-08-03
and its headline findings were withdrawn the same day, after re-measuring on hardware.
**Written:** 2026-08-03. **Retracted:** 2026-08-03.

> ## The short version
>
> On the supported configuration — XBEAM v1.2.1 at 192 kHz, booted from the bootloader menu — this
> unit captures **cleanly**: 100.27% of expected frames, **zero** zero-frames, **zero** timeline
> jumps, at every block size from 128 to 2048 and over a 60-second capture. That matches what
> Sebastian Holzapfel measured on his own machines almost exactly.
>
> **None of the original report's findings reproduce.** The delivery shortfall, the zero frames,
> the timeline jumps, the block-size collapse and the open/close wedge are all absent. One
> technical claim (the USB clock source) was verified **wrong** against the vendor source. The
> hardware, the host, the cable and the port are unchanged from the failing runs.
>
> Everything below is kept as a record of what was claimed, what replaced it, and what is still
> genuinely unknown. **Do not cite the original numbers.**

## Contents

1. [What was measured after retraction](#what-was-measured-after-retraction)
2. [Claim-by-claim: what was withdrawn](#claim-by-claim-what-was-withdrawn)
3. [The clock claim was wrong — the real clock tree](#the-clock-claim-was-wrong--the-real-clock-tree)
4. [What the source says about the UAC2 endpoints](#what-the-source-says-about-the-uac2-endpoints)
5. [What still stands](#what-still-stands)
6. [What is still unexplained](#what-is-still-unexplained)
7. [Archived original evidence](#archived-original-evidence-unreproducible)
8. [Reproduction](#reproduction)
9. [What went wrong with the original report](#what-went-wrong-with-the-original-report)

## What was measured after retraction

All runs below: XBEAM v1.2.1 booted **from the bootloader menu** (so the SI5351 `clk0` is
programmed from that slot's own manifest), `MISC → usb-mode = enable` set before plugging the
host in, 4 channels, `int32`, same Mac mini, same USB-C cable, same port as every failing run.

| Run | Rate | Length | blocksize | delivered | zero frames | timeline jumps |
|---|---|---|---|---|---|---|
| matched to the vendor's own repro | 192 kHz | 10 s | auto | **100.27%** | 0 | 0 |
| repeat 2 | 192 kHz | 10 s | auto | 100.27% | 0 | 0 |
| repeat 3 | 192 kHz | 10 s | auto | 100.27% | 0 | 0 |
| block-size sweep | 192 kHz | 10 s | auto | 100.48% | 0 | 0 |
| " | 192 kHz | 10 s | 128 | 100.27% | 0 | 0 |
| " | 192 kHz | 10 s | 256 | 100.27% | 0 | 0 |
| " | 192 kHz | 10 s | 512 | 100.27% | 0 | 0 |
| " | 192 kHz | 10 s | **1024** | **100.27%** | 0 | 0 |
| " | 192 kHz | 10 s | 2048 | 100.27% | 0 | 0 |
| original run length | 192 kHz | **3 s** | auto | 100.27% | 0 | 0 |
| " (repeat) | 192 kHz | 3 s | auto | 100.27% | 0 | 0 |
| **endurance** | 192 kHz | **60 s** | auto | **100.34%** | **1** of 11,558,912 | 0 |

The single zero frame in the 60-second run is one isolated frame at index 8,359,562 — one sample
in which all four channels happened to read exactly zero. It is not a run, it is not at a
boundary, and one frame in 11.5 million is not a dropout signature.

For comparison, the vendor's own runs on XBEAM v1.2.1 at 192 kHz:

| Host | delivered | zero frames | timeline jumps |
|---|---|---|---|
| Linux / ALSA | ~100.0% | 46, **all at stream start** | 93, each exactly 1.0 ms |
| macOS 15 (Darwin 24.6.0) | 100.5% | 0 | 0 |
| **this unit, macOS 26.4.1 (Darwin 25.4.0)** | **100.27%** | **0** | **0** |

Two things follow from the vendor's Linux row, and both invalidate metrics the original report
relied on. Zero **count** means nothing without zero **position** — his 46 zeros were startup
settling. And timeline-jump **count** means nothing without jump **size** — 93 jumps of exactly
1.0 ms is a healthy stream. Only the original report's *jump sizes* (44–500 ms) were ever
diagnostic, and those no longer occur at all.

### The open/close wedge does not reproduce

`boards/tiliqua/probe/probe_wedge.py` runs a rested baseline, then nine consecutive
open/close cycles with no pause — the pattern the original report said drove the device into a
one-block-then-stop state — then measures again three times, then again after a five-second rest.

```
baseline (rested)          100.27%
9 rapid open/close cycles, no pause
  post-churn 1             100.27%
  post-churn 2             100.27%
  post-churn 3             100.27%
  after a 5 s rest         100.27%
VERDICT: no wedge -- churn does not degrade capture here
```

This also kills a *second* hypothesis, one raised during the retraction rather than in the
original report: that the archived `probe_gaps.py` sweep — which opens and closes the stream once
per (rate, latency, blocksize) with no pause, and which is where the 97.4% and ~14% figures came
from — had wedged the device and was measuring the wedge. It had not, and it was not.

## Claim-by-claim: what was withdrawn

| Original claim | Status | Replaced by |
|---|---|---|
| At 48 kHz only **67–69%** of frames are delivered | **withdrawn** | not reproducible; the configuration it was measured on does not ship (see below) |
| **2.5–5%** of delivered frames are all-zero | **withdrawn** | 0.000% across eleven runs; 1 frame in 11.5 M over 60 s |
| The ADC timeline jumps 44–500 ms, ~30× in 10 s | **withdrawn** | 0 jumps >0.5 ms in every run, including 60 s |
| XBEAM at 192 kHz delivers 97.4% with 2.56% zeros | **withdrawn** | 100.27% / 0.000%, including at the original 3-second run length |
| Forcing `blocksize=1024` collapses delivery to ~14% | **withdrawn** | 100.27% at 1024; identical at 128/256/512/2048 |
| Nine open/close cycles wedge the device to 1–8% | **withdrawn** | no degradation at all (above) |
| "48 kHz is worse than 192 kHz, so it is not bandwidth" | **withdrawn** | the premise is gone; there is no 48 kHz deficit to explain |
| The zero counts repeat with a ~256 ms period | **withdrawn** | rested on a 12-sample truncated window of an unreproducible run |
| The jump sizes are quantised (~44.6 ms, ~6.30 ms) | **withdrawn** | same; there are no jumps to quantise |
| "The USB side runs off the ULPI's own 60 MHz recovered clock" | **wrong** | see the next section — verified against vendor source |
| "It reproduces on stock v1.2.1 bitstreams" | **not supportable** | `usb_audio` is not in the shipped slot map; it was built locally and SRAM-loaded |
| Our own bitstream measured 0.45% zeros — "better than either vendor bitstream" | **withdrawn as a comparison** | there is no vendor deficit to be better than; the 99.84% control below is the useful part |

## The clock claim was wrong — the real clock tree

The original report asserted, in the hypothesis section and in the email that carried it:

> …the audio side is driven by the SI5351 …while the USB side runs off the ULPI's own 60 MHz
> recovered clock. Those are independent oscillators.

**The second half is wrong.** The ULPI is the USB PHY *interface*; it is not a clock source for
the design. From `gateware/src/tiliqua/pll.py:272-291` (`TiliquaDomainGeneratorPLLExternal`, which
`TiliquaR5SC3Platform` selects at `platform.py:517-527`):

```
[48 MHz OSC on the SoM] → [ECP5 PLL] → sync  60 MHz
                                     → usb   60 MHz
[25 MHz OSC on the mobo] → [si5351 PLL] → [clk0] → audio
```

So:

- `usb` and `sync` are **both** 60 MHz, from the **same** ECP5 PLL, from the SoM's 48 MHz
  oscillator.
- `audio` is the SI5351's `clk0`: 12.288 MHz at 48 kHz, 49.152 MHz at 192 kHz
  (`pll.py:19-45`, `AudioClock.FINE_48KHZ` / `FINE_192KHZ`), sourced from the **motherboard's**
  25 MHz oscillator.
- `clk0` is programmed per slot by the bootloader — `external_pll_config.clk0_hz` travels in the
  slot manifest (`build/cli.py:232`) and `configure_external_pll()` applies it
  (`top/bootloader/fw/src/main.rs:252`).

The *shape* of the original hypothesis survives — there really are two independent oscillators
either side of the audio FIFO — but it is 25 MHz (mobo) against 48 MHz (SoM), not SI5351 against
a recovered USB clock. Since the measurements the hypothesis was invented to explain have
themselves been withdrawn, the hypothesis is moot either way.

**This error was in the version sent to the vendor.** It is corrected here and in the follow-up
email.

## What the source says about the UAC2 endpoints

Two questions the original report asked the vendor were answerable from
`gateware/src/tiliqua/usb_audio/__init__.py` without needing a reply. Recording the answers here
so they are not asked again.

**Q: Does the endpoint implement asynchronous rate feedback?** For **playback** (host → device),
yes: EP1 IN is an explicit feedback endpoint, `wMaxPacketSize=4`, `bInterval=4`,
`USBUsageType.FEEDBACK` (`:199-207`). Its value is computed from `audio_clock_counter` over 32
SOFs, corrected by `dac_fifo_level >> 3` (`:424-441`).

For **capture** (device → host), there is no feedback endpoint, and that is **spec-correct**. EP2
IN is declared `ASYNC` with `bNumEndpoints=1` (`:246-256`). An asynchronous IN endpoint conveys
its rate by *varying the packet size it sends*; feedback endpoints exist for asynchronous OUT,
where the device must tell the host how fast to send. `adc_fifo_level` is exposed for debug and is
never used in rate control. The clock source advertises
`ClockAttributes.INTERNAL_FIXED_CLOCK` with `HOST_READ_ONLY` controls (`:119`) — there is no
resampler anywhere in the path, just a FIFO.

**One observation from the source that is still worth raising**, and is the only technical
question left standing. The capture packet size is derived by *counting bytes on the playback
stream* (`:316-329`):

```python
audio_in_frame_bytes = Signal(range(self.max_packet_size), reset=24 * self.nr_channels)
with m.If(ep1_out.stream.valid & ep1_out.stream.ready):     # EP1 OUT = playback
    with m.If(audio_in_frame_bytes_counting):
        m.d.usb += audio_in_frame_bytes.eq(audio_in_frame_bytes + 1)
    with m.If(ep1_out.stream.payload.first):
        m.d.usb += [audio_in_frame_bytes.eq(1), audio_in_frame_bytes_counting.eq(1)]
    with m.Elif(ep1_out.stream.payload.last):
        m.d.usb += audio_in_frame_bytes_counting.eq(0)
# ...
m.d.comb += ep2_in.bytes_in_frame.eq(audio_in_frame_bytes)  # EP2 IN = capture
```

The reset value is `24 * 4 = 96` bytes = 6 frames per microframe = 48,000 frames/s, **regardless
of the configured sample rate**. Read literally, an input-only host — one that opens capture but
never opens playback — would leave `audio_in_frame_bytes` at its reset value and get 48 kHz worth
of packets while asking for 192 kHz, i.e. 25% delivery.

**This is offered as a question, not a finding, because our own measurements contradict it.**
Every run in the table above is input-only, at 192 kHz, and delivers 100.27%. So either the host
opens the playback direction anyway, or the counter is driven from somewhere else in practice.
Worth confirming rather than asserting.

## What still stands

Three things from the original work survive re-measurement, and all three point *away* from a
device defect.

1. **Our own bitstream is a clean control.** `boards/tiliqua/gateware/usb_iface.py:108` defines
   `class XlsUsbInterface(USB2AudioInterface)` — it subclasses the vendor's interface and
   overrides `create_descriptors()` only, leaving `create_audio_control_interface_descriptor` and
   `create_{output,input}_channels_descriptor` untouched. **The UAC2 audio path is the vendor's
   own unmodified code.** It measures **99.84%** delivered, 0.000% zeros, 0 jumps at 48 kHz over
   10 s, on the same Mac, cable and port. This single measurement eliminates a macOS 26 regression
   *and* the cable, together.

2. **The cable is not a factor.** USB-C to USB-C, 50 cm, thin. High speed is negotiated
   (`Device Speed 2` in `ioreg`); `usb2` is a USB 2.0 port so there is no gen-speed question; and
   both the vendor bitstream and our own push ≥99.8% through it.

3. **The plug order for XBEAM is real and documented.** The interface enumerates unconditionally
   but no frames flow until `MISC → usb-mode = enable` is set, and the vendor documents setting it
   before attaching the host. Every clean run above used that order.

## What is still unexplained

Honest accounting of what the retraction does **not** resolve.

- **What the original runs were actually measuring is unknown.** The failing numbers were real
  output from real scripts; they are simply not reproducible on any configuration we can still
  construct. The best available explanation is the SRAM-load clock inheritance described below,
  but it does not fit quantitatively (see the caveat), so it is a lead, not an answer.

- **The `usb_audio` 48 kHz configuration was never re-tested, and cannot be tested as shipped.**
  `usb_audio` is **not** in the shipped slot map
  (`0 XBEAM · 1 POLYSYN · 2 MACRO-OSC · 3 SID · 4 SELFTEST · 5 SAMPLER · 6 DSP-MDIFF · 7 VSYNTH`).
  It was built locally and SRAM-loaded over JTAG. That load path does **not** reprogram the
  SI5351, so `clk0` retains whatever the previously-booted slot left — and XBEAM ships with
  `--fs-192khz` enabled (`top/xbeam/top.py:140`), i.e. 49.152 MHz, while a 48 kHz build expects
  12.288 MHz. The original report's description of these runs as being on "stock v1.2.1
  bitstreams" was therefore **not accurate**.

  **Caveat that keeps this from being the answer:** a 4× clock mismatch predicts delivery near
  25% or near 400%. The observed 67–69% is neither. So clock inheritance may be part of it but
  does not explain the number on its own.

- **The XBEAM 97.4% / 2.56% figure has no explanation at all.** That run *was* booted from the
  menu, so the clock inheritance story does not apply. It was 3 seconds long, which raised the
  possibility that a fixed startup deficit was being amortised over too short a window — but two
  fresh 3-second runs came back at 100.27% with zero zeros. That hypothesis is dead too.

- **Only one unit and two host OS versions have been tested**, and this remains true in the
  clean direction as well: the unit measuring clean is not proof that no unit ever misbehaves.

## Archived original evidence (unreproducible)

Kept verbatim because it is the primary record of what was reported, and because a future
reproduction — if one ever occurs — will want to compare against it. **These numbers do not
reproduce and must not be cited as current.** Both runs are the `usb_audio` example at 48 kHz,
`blocksize=0`, 10 seconds, SRAM-loaded over JTAG with the audio clock unverified.

```
opened sr=48000.0 blocksize=0 latency=0.0884
callbacks=81  total_frames=331776  expected=480000  (69.12%)
frames/callback: unique=[4096] min=4096 max=4096
zero frames inside delivered data: 14510 (4.373%)
callbacks with status flags: 0

ADC timestamp delta: median=85.333 ms min=85.333 max=388.249
callbacks preceded by a timeline jump >0.5 ms: 28
  jump sizes (ms): [157.497  44.628 148.786  44.53  153.048 145.999 206.412 150.999  44.668
   6.304]
  at times (s):    [0.085 0.755 0.885 1.546 1.675 2.34  2.828 3.205 3.868 3.998]
  interval between jumps (s): median=0.3012 min=0.0916 max=0.8149

callbacks containing zeros: 42 of 81
  their zero counts: [ 94 589 102 597 370  90 579 450  97 586 365 107]
  their frame counts: [4096 4096 4096 4096 4096 4096 4096 4096 4096 4096 4096 4096]
  fully-zero callbacks: 0
```

The same capture with `dbg` physically unplugged from the host:

```
opened sr=48000.0 blocksize=0 latency=0.0884
callbacks=79  total_frames=323584  expected=480000  (67.41%)
frames/callback: unique=[4096] min=4096 max=4096
zero frames inside delivered data: 13486 (4.168%)
callbacks with status flags: 0

ADC timestamp delta: median=85.333 ms min=85.333 max=501.912
callbacks preceded by a timeline jump >0.5 ms: 30
  jump sizes (ms): [161.082 180.246  44.701   6.309   6.309  33.278  10.078 168.152  44.55
   6.304]
  at times (s):    [0.427 0.758 1.451 1.581 1.672 1.764 1.883 1.978 2.658 2.788]
  interval between jumps (s): median=0.1300 min=0.0916 max=0.7579

callbacks containing zeros: 39 of 79
  their zero counts: [108 602 328 112 606 108 602 328 119 572 406 134]
  their frame counts: [4096 4096 4096 4096 4096 4096 4096 4096 4096 4096 4096 4096]
  fully-zero callbacks: 0
```

Note that both print only the **first 10** jump sizes and the **first 12** zero counts. Every
claim the original report made about periodicity and quantisation rested on those truncated
windows. That truncation is fixed in `probe_compare.py`.

The wedged-state sweep excerpt, from `probe_gaps.py` after several open/close cycles:

```
=== 192 kHz ===
  sr=192000 lat=   low bs=    0 -> frames=  18760/384000 (  4.9%)  gaps=  3  overflows=0
  sr=192000 lat=  high bs=    0 -> frames=   8192/384000 (  2.1%)  gaps=  0  overflows=0
  sr=192000 lat=  0.05 bs=    0 -> frames=   4096/384000 (  1.1%)  gaps=  0  overflows=0
=== 48 kHz ===
  sr= 48000 lat=   low bs=    0 -> frames=   7192/96000 (  7.5%)  gaps= 10  overflows=0
  sr=48000 lat=high bs=0: no data
```

### Identity of the unit under test

Unchanged throughout, failing runs and clean runs alike.

| | |
|---|---|
| Module | Tiliqua R5 |
| SoM | SoldierCrab R3 (ECP5 `LFE5U-25F-6BG256C`) |
| Bootloader / debug firmware | `apfbug-beta4-1-g9b45` |
| Shipped bitstreams | release **v1.2.1** |
| Slot map as shipped | `0 XBEAM · 1 POLYSYN · 2 MACRO-OSC · 3 SID · 4 SELFTEST · 5 SAMPLER · 6 DSP-MDIFF · 7 VSYNTH` |
| USB identity on `dbg` | `0x1209:0xc0ca dirtyJtag apf.audio` |
| Host | Mac mini, Apple Silicon; macOS 26.4.1 build 25E253 (Darwin 25.4.0) |
| Audio stack | CoreAudio → PortAudio `V19.7.0-devel` → `sounddevice` 0.5.5 |
| Cable | USB-C to USB-C, 50 cm, to the `usb2` port |

Bootloader self-report on the same boot: `ak4619/codec: register config looks healthy`,
`audio/calibration: looks good! switch to it`, all 8 slot manifests parse and CRC-match, PSRAM
copies 163 KiB of firmware without error. The one unrelated anomaly,
`cy8cmbr3xxx/touch: n_working_sensors=Ok(0)` (with `CRC OK`), is a separate question and is
untouched by any of this.

## Reproduction

Probes live in `boards/tiliqua/probe/` and select the device by matching `tiliqua` in its name, so
nothing needs configuring. Run with `uv run boards/tiliqua/probe/<name>.py`.

| Script | What it does |
|---|---|
| `probe_compare.py` | **Use this one.** Per-callback frames, status flags, `inputBufferAdcTime`, zero count — plus zero-run **positions** with a startup-vs-mid-stream verdict, and the **full** jump-size array. `--sweep` walks blocksize 0…2048 with a pause between. Appends one JSON line per run to `probe_compare.log`. |
| `probe_wedge.py` | Rested baseline → 9 rapid open/close cycles → 3 post-churn measurements → rested measurement. Prints an explicit wedge / no-wedge verdict. |
| `probe_cb.py` | The original decisive probe. Superseded: hardcodes 48 kHz and truncates its output arrays. Kept so the archived runs above can be regenerated exactly. |
| `probe_status.py` | Single-open capture at a fixed blocksize; measures gap runs and whether their lengths are multiples of the block size. |
| `probe_gaps.py` | Sweeps rate × latency × blocksize with no pause between opens. Where the withdrawn 97.4% and ~14% figures came from. |

Minimal one-command reproduction at the supported configuration:

```python
# uv run --with sounddevice --with numpy python repro.py
import numpy as np, sounddevice as sd
SR, SEC = 192000, 10.0
dev = next(i for i, d in enumerate(sd.query_devices())
           if "tiliqua" in d["name"].lower() and d["max_input_channels"] > 0)
rows, chunks = [], []
def cb(indata, frames, t, status):
    rows.append((t.inputBufferAdcTime, frames, str(status)))
    chunks.append(indata.copy())
with sd.InputStream(device=dev, channels=4, samplerate=SR, dtype="int32",
                    blocksize=0, callback=cb):
    sd.sleep(int(SEC * 1000))
nf = np.array([r[1] for r in rows]); adc = np.array([r[0] for r in rows])
audio = np.concatenate(chunks); zeros = (audio == 0).all(1)
exp = int(SR * SEC)
print(f"frames {nf.sum()}/{exp} ({100*nf.sum()/exp:.2f}%)  "
      f"zeros {zeros.sum()} ({100*zeros.sum()/nf.sum():.3f}%)  "
      f"flagged {sum(1 for r in rows if r[2])}")
jump = np.diff(adc) - nf[:-1] / SR
big = np.where(jump > 5e-4)[0]
print(f"timeline jumps >0.5 ms: {len(big)}  sizes(ms) {(jump[big]*1000).round(2).tolist()}")
print(f"first zero at frame {np.argmax(zeros) if zeros.any() else 'n/a'} "
      f"(startup settling if < {SR//10})")
```

**Expected on this unit, as of the retraction:** ~100.3% delivered, 0 zeros, 0 jumps.

Two things the original probes got wrong and this one fixes, both learned from the vendor's own
healthy runs: report **where** zeros are (his 46 were all startup settling), and keep the **whole**
jump-size array (his 93 jumps were all exactly 1.0 ms — a healthy stream).

## What went wrong with the original report

Recorded so the same failure mode is not repeated.

1. **Measurements were carried forward across sessions without re-verification.** The 67–69% and
   2.56% figures were weeks old, taken under conditions that were not fully recorded, and were
   treated as settled facts to reason *from* rather than claims to re-check. Re-measuring took
   about fifteen minutes and would have prevented the entire report.

2. **A configuration that does not ship was described as "stock".** The `usb_audio` example is not
   in the slot map and was SRAM-loaded over a path that leaves the SI5351 unprogrammed. That
   should have been stated as a caveat in the first draft, not discovered afterwards.

3. **A technical claim was asserted without reading the source.** The ULPI clock statement was
   inference presented as fact, in a document that explicitly promised "where a claim is inference,
   it says so". The vendor source is public and took minutes to check.

4. **Metrics were reported without the qualifier that makes them meaningful.** Zero *count*
   without position, and jump *count* without size, are both uninformative — as the vendor
   demonstrated by clearing his own 46 zeros and 93 jumps in one line each.

5. **No control was run before reporting.** Our own bitstream, which wraps the vendor's unmodified
   UAC2 code, delivers 99.84% on the same host and cable. Running that first would have located
   the problem on our side of the line immediately.

The correct order was: re-measure on a supported, verified configuration → run the in-house
control → read the source for anything about to be asserted → *then* write to the vendor.
