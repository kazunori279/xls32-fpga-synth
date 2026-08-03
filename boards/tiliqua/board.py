"""apf.audio Tiliqua R5 (SoldierCrab R3, ECP5 LFE5U-25F) — the M21+ target.

Gateware lives in boards/tiliqua/gateware/, built by boards/tiliqua/build.sh; see
docs/TILIQUA_PORT.md. As of M23 the bitstream is audio-only -- a fixed boot patch on
outputs 0/1, no MIDI input and no host transport -- so the automated suite cannot drive
it yet. That arrives with M24 (MIDI in) and M25 (USB audio back to the host).

The numbers below are measured on the module, not guessed: the audio clock comes from
an external SI5351 that each bitstream reconfigures, and the vendor's own usb_audio top
sets it to 12.288 MHz for 48 kHz. Audio reaches the host over the FPGA's own USB HS PHY
as a UAC2 device (4 in, 4 out, 24-bit), not over the RP2040's 115200-baud CDC port.
"""
from boards import Board

BOARD = Board(
    name="tiliqua",
    fpga="LFE5U-25F-6BG256C",
    sr=48000,                     # SI5351 at 12.288 MHz; --fs-192khz opts into 192 kHz
    transport="usbaudio",
    transport_opts={"channels": 4, "dtype": "int32"},
    stereo=True,
    load_cmd="openFPGALoader -c dirtyJtag build/top.bit",   # SRAM; never touches the flash slots
    root="boards/tiliqua",
    unsupported="gateware builds as of M23, but there is no host loop yet: "
                "MIDI in lands at M24 and USB audio capture at M25 — see docs/TILIQUA_PORT.md",
)
