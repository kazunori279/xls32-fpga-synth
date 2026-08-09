# Prebuilt bitstream archive

`xls32-r5.tar.gz` is a ready-to-flash **bitstream archive** for the **Tiliqua R5** (Lattice
`LFE5U-25F`), so you can run the synth **without building** — no Amaranth, no yosys, no Docker.

An archive is the format the Tiliqua bootloader wants: the bitstream plus a `manifest.json` that
tells the bootloader what to call the slot, what to print beside it, and — the part that matters —
what to program the SI5351 clock generator to. Ours pins `clk0_hz: 12288000` and
`clk1_hz: 39070000`, which is why a module booted from this slot is always clocked correctly.

It is the full XLS32 engine at 48 kHz: 32-voice polyphony, 4 multitimbral parts, resonant multimode
filter, LFO, unison, cross-osc FM/ring-mod, the block-RAM effects (chorus / ping-pong echo /
8-comb Freeverb), UAC2 audio + USB-MIDI over `usb2`, TRS MIDI in, and the 720×720p60 beam-raced
visualiser on the DVI output.

## Flash it

Either way round writes it to a slot. **Slot 6 is what this repo's docs and tooling assume**, but
any of 0–7 works.

```bash
# A) No toolchain — open https://apfaudio.github.io/tiliqua-webflash/ in Chrome, pick the module
#    over WebUSB, upload this file, choose slot 6.

# B) With the vendor SDK checked out:
cd ~/Documents/GitHub/tiliqua/gateware
pdm flash archive <this-repo>/boards/tiliqua/firmware/xls32-r5.tar.gz --slot 6
```

Catch the bootloader's five-second countdown and pick the slot from the menu once; every cold boot
from then on loads it directly. See the repo README §2 "Tiliqua — flash and go" for the full
walkthrough, including what you should see and hear when it comes up.

## What it was built from

The manifest's `tag` field names the commit. A trailing `-` means the tree was dirty at build time
and the archive should not be trusted as a release.

## Rebuilding

This file is a copy of the archive the build drops beside `top.bit` (both gitignored). See README §3
"Tiliqua — build, flash, verify", then:

```bash
cp build/tiliqua/build/xls32-r5/xls32-*-r5.tar.gz boards/tiliqua/firmware/xls32-r5.tar.gz
```

Nothing checks that this file still matches the sources — see [docs/TODO.md](../../../docs/TODO.md).
