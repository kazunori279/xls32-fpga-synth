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

## Rebuilding

This file is a copy of `build/top.bit` (gitignored build output). To regenerate it, see README §3
"Basys 3 — build, flash, verify", then `cp build/top.bit boards/basys3/firmware/top.bit`.
