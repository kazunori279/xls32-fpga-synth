"""apf.audio Tiliqua R5 (SoldierCrab R3, ECP5 LFE5U-25F) — the M21+ target.

Gateware lives in boards/tiliqua/gateware/, built by boards/tiliqua/build.sh; see
docs/TILIQUA_PORT.md. As of M24 the bitstream plays MIDI arriving at the TRS jack out
channels 0/1, but there is still no host transport, so the automated suite cannot drive
it: the host has no way to send notes or to record what came back. That arrives with M25
(USB audio in one direction, USB-MIDI in the other).

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
    # SRAM only; never touches the nine flash slots. Power-cycle the module first: the audio
    # domain is clocked by the SI5351's clk0, which no bitstream configures -- the bootloader
    # does, at power-on, to the same 12.288 MHz this design wants. Load over a stale clk0 from
    # some other slot and the engine clocks at the wrong rate or not at all.
    load_cmd="openFPGALoader -c dirtyJtag build/tiliqua/build/xls32-r5/top.bit",
    root="boards/tiliqua",
    unsupported="gateware plays TRS MIDI as of M24, but there is no host loop yet: "
                "USB audio and USB-MIDI land at M25 — see docs/TILIQUA_PORT.md",
)
