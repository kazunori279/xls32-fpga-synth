"""Equivalence check for 0002-luna-register-interpacket-strobes.patch.

The patch turns luna's `USBInterpacketTimer` strobes from combinational into registered, which is
only worth doing if the strobes land on exactly the same cycles. So the expectations here are
written from the spec constants -- `counter == N` observed on cycle N, counting from the `start`
pulse -- and the file is meant to pass **both** with the patch and without it. If it only passes
one way, the patch is not the no-op it claims to be.

The cases are the ones where "exactly" is at risk:

  * the first strobe after a start, high and full speed
  * a restart that cancels a pending timeout
  * a start landing on the cycle the register was loaded, which is what `~any_reset` is for --
    drop that term and `min` gains a spurious strobe at cycle 0
  * a long idle, to prove nothing fires while the counter saturates

Run it against the SDK's venv, which is where luna lives:

    ~/Documents/GitHub/tiliqua/gateware/.venv/bin/python \
        boards/tiliqua/patches/test_0002_timer.py
"""

import sys

from amaranth import Elaboratable, Module, Signal
from amaranth.sim import Simulator

from luna.gateware.usb.usb2 import USBSpeed
from luna.gateware.usb.usb2.packet import USBInterpacketTimer, InterpacketTimerInterface

HS = USBInterpacketTimer._HS_RX_TO_TX_DELAY[60e6]              # (1, 24)
FS = USBInterpacketTimer._FS_RX_TO_TX_DELAY[60e6]              # (10, 32)
HS_TIMEOUT = USBInterpacketTimer._HS_TX_TO_RX_TIMEOUT[60e6]    # 92
FS_TIMEOUT = USBInterpacketTimer._FS_TX_TO_RX_TIMEOUT[60e6]    # 80


class Harness(Elaboratable):
    """The timer with one interface attached, so the strobes come out where an endpoint sees them."""

    def __init__(self):
        self.speed = Signal(2)
        self.iface = InterpacketTimerInterface()

    def elaborate(self, platform):
        m = Module()
        m.submodules.timer = timer = USBInterpacketTimer()
        timer.add_interface(self.iface)
        m.d.comb += timer.speed.eq(self.speed)
        return m


async def collect(ctx, dut, cycles, start_at):
    """Run `cycles` clocks, pulsing `start` on each cycle in `start_at`, and return the cycle
    numbers on which each strobe was seen."""
    fired = {"min": [], "max": [], "timeout": []}
    for n in range(cycles):
        ctx.set(dut.iface.start, n in start_at)
        await ctx.tick("usb")
        if ctx.get(dut.iface.tx_allowed):
            fired["min"].append(n)
        if ctx.get(dut.iface.tx_timeout):
            fired["max"].append(n)
        if ctx.get(dut.iface.rx_timeout):
            fired["timeout"].append(n)
    return fired


def run(name, speed, cycles, start_at, expect):
    dut = Harness()
    got = {}

    async def bench(ctx):
        ctx.set(dut.speed, speed)
        got.update(await collect(ctx, dut, cycles, start_at))

    sim = Simulator(dut)
    sim.add_clock(1 / 60e6, domain="usb")
    sim.add_testbench(bench)
    sim.run()

    ok = got == expect
    print(f"{'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        print(f"       expected {expect}")
        print(f"       got      {got}")
    return ok


def main():
    results = []

    results.append(run(
        "HS, one start, strobes at counter == N",
        USBSpeed.HIGH, 120, (0,),
        {"min": [HS[0]], "max": [HS[1]], "timeout": [HS_TIMEOUT]},
    ))

    results.append(run(
        "FS, one start, strobes at counter == N",
        USBSpeed.FULL, 120, (0,),
        {"min": [FS[0]], "max": [FS[1]], "timeout": [FS_TIMEOUT]},
    ))

    # A restart resets the counter, so the pending rx timeout at 92 never arrives and everything
    # is re-measured from cycle 40.
    results.append(run(
        "HS, restarted at cycle 40, cancels the pending rx timeout",
        USBSpeed.HIGH, 160, (0, 40),
        {"min":     [HS[0], 40 + HS[0]],
         "max":     [HS[1], 40 + HS[1]],
         "timeout": [40 + HS_TIMEOUT]},
    ))

    # The case `~any_reset` exists for: a start on the cycle the registered strobe was loaded on.
    # The combinational version reads a counter that is already back at 0 and does not fire, and
    # the registered one must agree. Without the guard, `min` gains a strobe at cycle 0.
    restart = HS[1] - 1
    results.append(run(
        "HS, start collides with the cycle the register was loaded",
        USBSpeed.HIGH, 200, (0, restart),
        {"min":     [HS[0], restart + HS[0]],
         "max":     [restart + HS[1]],
         "timeout": [restart + HS_TIMEOUT]},
    ))

    # Nothing fires while the counter saturates, however long we wait.
    results.append(run(
        "FS, 900 idle cycles, no strobe past the timeout",
        USBSpeed.FULL, 900, (0,),
        {"min": [FS[0]], "max": [FS[1]], "timeout": [FS_TIMEOUT]},
    ))

    print()
    print(f"{sum(results)}/{len(results)} pass")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
