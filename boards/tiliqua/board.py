"""apf.audio Tiliqua R5 (SoldierCrab R3, ECP5 LFE5U-25F) — the M21+ target.

Gateware lives in boards/tiliqua/gateware/, built by boards/tiliqua/build.sh; see
ARCHITECTURE_tiliqua.md. M24 made the bitstream play MIDI arriving at the TRS jack out
channels 0/1; M25 added the host loop the automated suite needs — audio up over USB Audio
Class 2, MIDI down over USB-MIDI, both on the `usb2` port, so `run_tests.py --board tiliqua`
has something to drive. `unsupported` is therefore clear.

The formal bitstream is the **24-voice** build (M36): 93.9% of the die, `sync` at 55.48 MHz
against a 60 MHz requirement (ARCHITECTURE_tiliqua.md E4), graded 99.8/100 on the module. It is
loaded despite the shortfall, deliberately — it enumerates and streams at that speed — but the
shortfall is smaller than it looks written down: the 32-voice build it replaces closed at 46.35,
which is a bet on the silicon being 29% faster than modelled, and this one bets on 8%. That build
is still available as `VOICES=32 bash boards/tiliqua/build.sh` and is experimental: it runs here
and it did not run on one of the vendor's two modules (issue #3). Point
`boards/tiliqua/check_loop.py` at whichever is loaded before the 175-case suite: it isolates a
broken transport from a broken synth, which the suite cannot.

At 24 voices the `stress_32voice` and unison-chord cases become voice-stealing tests rather than
exact-capacity ones. They still pass, and stealing is the behaviour that matters, but the numbers
in their titles are the 32-voice engine's.

Audio reaches the host over the FPGA's own USB HS PHY as a UAC2 device (4 in, 4 out, 24-bit),
not over the RP2040's 115200-baud CDC port. Channels 2 and 3 carry a counter rather than
audio, so `check_loop.py` can measure the audio clock and refuse to grade a misclocked board;
see the note on the tee in gateware/top.py for why that turned out to matter.
"""
from boards import Board

BOARD = Board(
    name="tiliqua",
    fpga="LFE5U-25F-6BG256C",
    sr=48000,                     # SI5351 at 12.288 MHz; --fs-192khz opts into 192 kHz
    transport="usbaudio",
    transport_opts={"channels": 4, "dtype": "int32"},
    stereo=True,
    # SRAM only; never touches the nine flash slots. But the module has to be sitting in the
    # bootloader when this runs, and getting it there takes a hand on the encoder.
    #
    # The audio domain is clocked by the SI5351's clk0 straight through -- no FPGA PLL in
    # between (TiliquaDomainGeneratorPLLExternal). No bitstream programs that chip; only the
    # bootloader does, over I2C, and it sets 12.288 MHz, which is exactly what this design
    # wants. So far so good. The catch is what the bootloader does five seconds later: it
    # autoboots whichever slot the mobo EEPROM remembers as `last_boot_slot`, and reprograms
    # clk0 from that slot's manifest on the way. If the last slot booted by hand was a 192 kHz
    # one, every cold boot lands back at 49.152 MHz -- so a power cycle on its own cannot fix
    # this, and neither can a JTAG refresh. Touching the encoder during the countdown cancels
    # the autoboot and clears the flag; a long press from a running slot warm-boots back to
    # the bootloader, which also clears it.
    #
    # Worth the paragraph because the symptom is silent: the engine simply runs at 4x and the
    # whole instrument is 2400 cents sharp. M25 spent most of a day on it, twice -- once on
    # the rate, once on believing a power cycle was the cure.
    load_cmd="openFPGALoader -c dirtyJtag build/tiliqua/build/xls24-r5/top.bit",
    root="boards/tiliqua",
)
