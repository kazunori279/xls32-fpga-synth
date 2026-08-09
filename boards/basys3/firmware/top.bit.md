# Prebuilt bitstream

`top.bit` is a ready-to-flash bitstream for the **Basys 3** board (Xilinx `xc7a35t`), so you can
run the synth **without building** (no Vivado / F4PGA toolchain needed — just `openFPGALoader`).

It is the full XLS32 engine: 32-voice polyphony, 4 multitimbral parts, resonant multimode filter,
LFO, unison, cross-osc FM/ring-mod, and the block-RAM effects (chorus / delay / reverb).

## Flash it

```bash
# volatile (SRAM) — fast, lost on power-off:
openFPGALoader -b basys3 firmware/top.bit

# persistent (SPI flash) — survives power cycles, boots standalone:
openFPGALoader -b basys3 -f firmware/top.bit
```

For standalone boot from flash, set the Basys 3 **mode jumper JP1 to QSPI**.

See the repo README §2 "Basys 3 — flash and go" for the full walkthrough. The Tiliqua equivalent is
`boards/tiliqua/firmware/`, which ships a flash archive rather than a bare bitstream.

## What it was built from

**This bitstream is behind the sources.** It is byte-for-byte the blob from the 2026-07-13 initial
public release (`a38cffe`); the `refactor(M20)` commit only moved it into this directory. Since then
`core/synth.x` has changed twice (M22's 18×18 narrowing, M29) and so have `rtl/top.v` and
`rtl/build_vivado.tcl`. It plays — it is a complete engine, just an older one — but it is not the
engine the rest of the repo documents.

It has stayed behind because refreshing it needs Vivado on an x86 machine (see
`boards/basys3/scripts/remote_build.sh`), which no hosted runner can provide.

To see the drift, and to re-record provenance after a rebuild:

```bash
uv run --no-project python scripts/check_artefacts.py           # what changed since the build
uv run --no-project python scripts/check_artefacts.py --update  # after copying in a fresh build
```

The record it compares against is `scripts/artefact_hashes.json`.

## Rebuilding

This file is a copy of `build/top.bit` (gitignored build output). To regenerate it, see README §3
"Basys 3 — build, flash, verify", then:

```bash
cp build/top.bit boards/basys3/firmware/top.bit
uv run --no-project python scripts/check_artefacts.py --update basys3
```
