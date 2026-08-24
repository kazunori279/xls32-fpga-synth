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
patch -p3 -d $V < boards/tiliqua/patches/0002-*.patch
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
