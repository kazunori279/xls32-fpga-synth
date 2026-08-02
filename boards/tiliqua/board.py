"""apf.audio Tiliqua R5 (SoldierCrab R3, ECP5 LFE5U-25F) — the M21+ target.

Declared here so the registry is real and the host seam has a second implementation to
prove itself against. There is no gateware yet; see docs/TILIQUA_PORT.md.

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
    unsupported="no gateware yet — M21 onward, see docs/TILIQUA_PORT.md",
)
