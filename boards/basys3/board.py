"""Digilent Basys 3 (Artix-7 xc7a35t) — the board XLS32 was built on.

The engine runs on a ÷3 clock enable off the 100 MHz oscillator, so a sample lands
every 3125 clocks: 32 kHz. Audio leaves over the FT2232's channel-B UART at 2 Mbaud,
which is just enough for 16-bit stereo at that rate.
"""
from boards import Board

BOARD = Board(
    name="basys3",
    fpga="xc7a35tcpg236-1",
    sr=32000,                     # 100 MHz / 3125, set by the ÷3 engine clock enable
    transport="uart",
    transport_opts={"baud": 2000000},   # 100 MHz / 50
    stereo=True,
    load_cmd="openFPGALoader -b basys3 build/top.bit",
    root="boards/basys3",
)
