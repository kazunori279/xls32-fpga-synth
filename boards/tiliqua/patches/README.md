# Local patches to the Tiliqua SDK

We treat the `tiliqua` checkout as read-only — `boards/tiliqua/build.sh` writes every artefact into
this repo's `build/` tree rather than dirtying it. These patches are the exception: changes that
belong upstream, kept here so a measurement can be reproduced before upstream has taken them.

```bash
git -C ~/Documents/GitHub/tiliqua apply /path/to/xls32-fpga-synth/boards/tiliqua/patches/0001-*.patch
# ... build, measure ...
git -C ~/Documents/GitHub/tiliqua checkout -- gateware/src/tiliqua/usb_audio/__init__.py
```

Applied against `tiliqua` at `d760756`.

`0002` is against **luna**, not the Tiliqua checkout, and luna is a wheel in the SDK's venv rather
than a git tree — so `git apply` has nothing to apply into. Patch the installed file and put it
back by reinstalling:

```bash
V=~/Documents/GitHub/tiliqua/gateware/.venv/lib/python3.13/site-packages
cp $V/luna/gateware/usb/usb2/packet.py /tmp/packet.orig.py          # keep the original
patch -p1 -d $V < boards/tiliqua/patches/0002-*.patch
# ... build, measure ...
cp /tmp/packet.orig.py $V/luna/gateware/usb/usb2/packet.py
```

Against `luna-usb` 0.2.3, `greatscottgadgets/luna` at `6771b6d` — the revision `pdm.lock` pins.

## 0001-usb-in-skid-buffer.patch

A two-deep `SyncFIFOBuffered` between `ChannelsToUSBStream` and the isochronous IN endpoint, so
the ULPI transmit path's `ready` stops reaching the audio FIFO's level counter combinationally.
Background: issue #34, `ARCHITECTURE_tiliqua.md`, and the thread with Seb.

**Measured, 24 voices, `--router router2 --seed 4`, post-route `clk`:**

| build                      | Fmax      | worst path                                    | logic / routing  |
|----------------------------|-----------|-----------------------------------------------|------------------|
| baseline                   | 45.23 MHz | `USBControlEndpoint.request[4]` → `level[8]`  | 4.79 / 17.32 ns  |
| with this patch            | 46.54 MHz | `fx.rsize[0]` → `fx.csr[8]`                   | 9.96 / 11.53 ns  |

The patch does what it was predicted to do — the 22.11 ns luna cone is gone from the report
entirely, which puts it somewhere below 21.49 ns. What it does not do is raise Fmax, because a
path of our own was hiding 0.62 ns behind it. **The USB cone is no longer the blocker; the reverb
is.** `fx.rsize` → `Array(RVG)[rsize]` → `MULT18X18D` → the `fbm` shift-and-round → `rin_r + fbm`
→ the 20-bit `acc` chain → `csr` is 21.49 ns, and unlike the luna cone nearly half of it (9.96 ns)
is logic depth we put there. That one is ours to fix, in `boards/tiliqua/gateware/fx.py`.

### …and then M35 made it unnecessary

Once `fx.py` registered the comb feedback and `top.py` put a buffer in the MIDI stream chain, the
patch stopped paying for itself. Same source, same seed, 24 voices:

| build                            | Fmax          | worst path                                                       |
|----------------------------------|---------------|------------------------------------------------------------------|
| M35, **no** patch                | **50.92 MHz** | `USBControlEndpoint.index[11]` → `usb.transmitter.fsm_state[2]`  |
| M35 **with** the patch           | 48.90 MHz     | `USBControlEndpoint.request[4]` → `IsoStreamInEndpoint.bytes_left_in_frame[1]` |

48.90 sits inside the no-patch seed spread (47.30–50.92 over five seeds), so the honest reading is
**no measurable difference**, not a 2 MHz regression. What the patch cannot do is help any more:
it cuts the `ready` path into `ChannelsToUSBStream`, and the cones that cap the design now both
start at luna's control endpoint and end inside luna — `bytes_left_in_frame` is *behind* the skid
buffer, not in front of it.

So the patch is kept for the record and not applied. It also answers the question the vendor
raised, which was whether his libraries needed changing at all: **they did not.** The two paths
that were actually costing us the clock were both ours.

## 0002-luna-register-interpacket-strobes.patch

`USBInterpacketTimer`'s three strobes — `tx_allowed`, `tx_timeout`, `rx_timeout` — are
`counter == N` comparators driven straight into every endpoint interface. `tx_allowed` gates the
decision to transmit, so on a design that fills its device the comparator and its fanout are the
first two LUT levels of the longest path in it. On the M41 24-voice build the worst `clk` path is
17.66 ns and it starts at `usbif.usb.timer.counter[7]`, reaching `setup_decoder.received` 3.16 ns
later and ending in `data_crc.crc[1]`.

The patch compares against `N - 1` a cycle earlier and flops the result, which lands each strobe
on exactly the cycle it was on before with the comparator behind a register. Two things keep that
"exactly" honest: the strobes are cleared each cycle, because an undriven combinational signal
reads 0 where an undriven register holds and the `fs_only` build leaves two branches empty; and
`~any_reset` suppresses the strobe when an interface restarts the timer on the cycle the register
was loaded, which is the one case where a register and a comparator disagree.

**Equivalence, `boards/tiliqua/patches/test_0002_timer.py`.** Five cases — first strobe at HS and
FS, a restart that cancels a pending timeout, a start colliding with the load cycle, and a 900-cycle
idle — asserted against the spec constants rather than a golden trace, so the file passes **both**
with the patch and without it. It does: 5/5 either way. Drop the `& ~any_reset` and case 4 gains a
spurious `tx_allowed` at cycle 0, which is how you can tell the guard is load-bearing.

```bash
~/Documents/GitHub/tiliqua/gateware/.venv/bin/python boards/tiliqua/patches/test_0002_timer.py
```

### Measured: the mean does not move, the ceiling does

One seed cannot answer this. The patch adds cells, the design is at 93.5 % of the die, and at that
occupancy the placer seed is worth ±1.8 MHz on its own — so a changed netlist is a fresh lottery
draw rather than a comparison. Seed 7 alone said 53.17 against a 56.63 baseline, which would have
read as a 3.5 MHz regression and was not one. Both columns below are 24 voices, M41 sources, the
same 24 seeds, `--timing-allow-fail --router router2 --router2-tmg-ripup`, post-route `clk`:

| over the 22 seeds that routed both ways | baseline  | with this patch |
|-----------------------------------------|-----------|-----------------|
| mean                                     | 53.90 MHz | 54.76 MHz       |
| sd                                       | 1.42      | 1.81            |
| floor                                    | 51.60 MHz | 51.05 MHz       |
| **ceiling**                              | **56.63 MHz** | **58.93 MHz** (seed 20) |
| draws at or above 56 MHz                 | 1 of 22   | 5 of 22         |
| TRELLIS_COMB                             | 22,722 (93.5 %) | 23,015 (94.8 %) |

**+0.85 MHz on the mean is not a result** — it is about 1.8σ, which on 22 draws is what noise looks
like. The ceiling is the result: 58.93 MHz is 2.30 MHz above anything this design has reached on
this die, and the count of draws that clear 56 goes from one to five. That distinction matters
because nobody ships the mean. `build.sh` pins a seed found by sweeping, so the number that reaches
a module is the maximum of the draw, and the patch moves the maximum while widening the spread it
comes from.

On seed 20 the cone the patch was aimed at is gone. The worst path becomes
`channels_to_usb_stream.out_fifo.r_port__addr[1]` → `usb.data_crc.crc[0]`, 16.97 ns, 4.60 ns logic
and 12.37 ns routing — still inside the USB stack, still three quarters routing, and no longer
touching `USBInterpacketTimer`.

It is not free. The 293 extra cells cost 1.3 points of occupancy, and two seeds that routed at
baseline (6 at 53.90, 16 at 55.94) now diverge instead: `overused` climbs across router iterations
and never comes back down. So the patch also buys a wider sweep, because two draws in 24 stop
producing a bitstream at all.

### It does not combine with `XLS32_SPLIT_CLOCKS=1`

Worth knowing before anyone tries it, because the two look like they should add: M39's split build
fell 0.6 MHz short of 60 MHz and its best-seed path started at `usb.token_detector.timer.counter`,
another `USBInterpacketTimer` and so exactly what this patch registers. Over the same 24 seeds,
23 routed and the result is worse than either lever on its own — mean 52.87, best **55.59**, and
zero draws at or above 56 against the patch's five. The patch still works: on the best draw the
timer cone is gone and the worst path is the same `out_fifo.r_port__addr[3]` → `data_crc.crc[0]`
pair the unsplit patched build ends on, at 17.99 ns against 16.97, all of the difference in routing
(13.61 vs 12.37 ns). The extra domain buys the `usb` cells freedom and takes it back in placement
dispersion. Measure this patch unsplit.

**Not applied.** The vendor asked that his distributed libraries not be modified, and luna is one of
them. The place this belongs is upstream in `greatscottgadgets/luna`, which is issue #34's third
item — if luna takes it, Tiliqua gets it through a version bump and nothing here is patched.

The block it changes is byte-identical on luna `main` (checked 2026-08-24) and on the `6771b6d`
that `pdm.lock` pins, so the patch applies upstream unchanged.

## 0003-luna-repair-the-interpacket-timer-test.patch

luna already ships `USBInterpacketTimerTest`, and it has never run. `test_resets_and_delays` is a
generator function without `@usb_domain_test_case`, so calling it returns a generator that is never
driven — pytest reports it as a pass and prints *"It is deprecated to return a value that is not
None from a test case"*, which is the only sign anything is wrong. Three separate defects were
sitting behind that, none of which could ever have been noticed:

- `initialize_signals` calls `USBSpeed.FULL`, and the file never imports `USBSpeed`. Adding the
  decorator gets you `NameError` before a single cycle is simulated.
- the same line is `yield self.dut.speed(USBSpeed.FULL)` — calling a `Signal` instead of `.eq()`.
- the final loop asserts on `self.rx_to_tx_min`, `self.rx_to_tx_max` and `self.tx_to_rx_timeout`,
  none of which exist on the test case. They are the DUT's internal names, not the interface's.

Once it runs, the cycle counts are wrong too. Measured against pristine luna in the test's own
idiom, the FS strobes land 11, 33 and 81 cycles after the `start` pulse, where the test advances 10,
32 and 54 and its comment claims 80. So this patch fixes the four defects and corrects the counts to
what the hardware does.

**It passes both with 0002 and without it**, which is the point: it is a real regression test for
the strobe cycles, so it is the thing that makes 0002 reviewable rather than a claim. The two go
upstream together — repair the test first, then make the change under it.

```bash
git -C /path/to/luna apply /path/to/xls32-fpga-synth/boards/tiliqua/patches/0003-*.patch
python -m pytest tests/test_usb2_packet.py -k InterpacketTimer -q
```
