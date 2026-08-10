#!/usr/bin/env python3
"""Reference side of the browser Aligner check. Usage: check_aligner.py [--record]

`webui/static/transport.js` carries a hand port of `host/transport/uart.py`'s `Aligner`, and the
two were only ever claimed to agree -- nothing in the repo compared them, and the periodic re-lock,
which exists because of the M28a rail bug, had never run outside Python at all. Worse, it had never
run inside Python either outside a test: it scored `self.buf`, which `feed` empties of whole frames
every call, so the guard asking for 4100 bytes there could only pass if one chunk carried that
many. Real chunks do not -- `os.read` returns 510-1020 bytes on this board and Web Serial's reader
is no different -- so the re-lock was dead code in both languages.

This runs the Python side over a fixture of real board bytes and writes the answer to
`testdata/expected.json`; `aligner_check.html` runs the JS side over the same bytes in a browser
and checks it got the same answer. Byte-for-byte on the aligned output, so every derived measure
follows -- there is nothing to keep in sync but a SHA-256.

The fixture is a 49152-byte capture off a Basys 3 with a **real mid-stream frame-phase shift** in
it at byte 16384, a third of the way in: +3 bytes, phase 2 -> 1. An **odd** shift is the one that
matters. An even one only swaps L and R; an odd one makes every following 16-bit sample an
(Lhi, Rlo) pair, which is uniform full-scale hash. Shifts used to be a matter of waiting for one;
they are reproducible now that the trigger is known -- flush the port, pause 50 ms, then read, and
the link discards a partial frame about four times in five. See `read_bytes` in
`host/transport/uart.py`.

Feeding it in fixed chunks matters: the re-lock interval is counted in bytes fed, so the chunk size
decides where the check lands relative to the shift and how much hash gets out before the step.
CHUNKS spans what the two readers actually deliver plus two sizes above it; both sides run all of
them, and both sides have to agree on all of them.
"""
import hashlib, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "host"))
from transport.uart import Aligner, _marker_score          # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
FIXTURE = HERE / "testdata" / "basys3_phase_shift.bin"
EXPECTED = HERE / "testdata" / "expected.json"
CHUNKS = [510, 1020, 2048, 4096, 8192]


def run(raw, chunk):
    """Feed `raw` through an Aligner in `chunk`-byte pieces and report what came out.

    `dropped` is the evidence the re-lock fired: the Aligner emits whole 4-byte frames and keeps
    the remainder, so anything unaccounted for was cut off the front to step the phase. A run that
    never re-locks drops 0-3 bytes at the initial lock and nothing after.

    `heal_at` is where the output stops being hash -- the last 512-byte window that scores badly
    against the marker pattern. Subtract the shift and that is the cost of the drop: the samples
    between the phase moving and the Aligner noticing.
    """
    a = Aligner()
    out = bytearray()
    for i in range(0, len(raw), chunk):
        out += a.feed(raw[i:i+chunk])
    heal, nbad = 0, 0
    for i in range(0, len(out) - 512, 512):
        if _marker_score(out[i:i+512], 0, limit=512) > 0.05:
            heal, nbad = i + 512, nbad + 1
    return {
        "chunk": chunk,
        "bytes_out": len(out),
        "dropped": len(raw) - len(out) - len(a.buf),
        "heal_at": heal,
        "bad_windows": nbad,
        "sha256": hashlib.sha256(out).hexdigest(),
    }


def describe(raw, win=128):
    """Where the fixture's phase shift is, so a failure says something more than a hash mismatch."""
    best = lambda b: min(range(4), key=lambda o: _marker_score(b, o))
    p0 = best(raw[:win])
    for i in range(win, len(raw)-win, win):
        p = best(raw[i:i+win])
        if p != p0 and _marker_score(raw[i:i+win], p) < 0.05:
            return {"shift_at": i, "phase_before": p0, "phase_after": p}
    return {"shift_at": None, "phase_before": p0, "phase_after": p0}


def main():
    raw = FIXTURE.read_bytes()
    fx = {"name": FIXTURE.name, "bytes": len(raw),
          "sha256": hashlib.sha256(raw).hexdigest(), **describe(raw)}
    if fx["shift_at"] is None:
        sys.exit("the fixture has no phase shift in it -- it is not testing what it claims to")
    if (fx["phase_after"] - fx["phase_before"]) % 2 == 0:
        sys.exit("the fixture's shift is even -- that is an L/R swap, not the rail bug")
    res = {"fixture": fx, "runs": [run(raw, c) for c in CHUNKS]}

    print(f"{fx['name']}: {len(raw)} B, phase {fx['phase_before']} -> {fx['phase_after']} "
          f"at byte {fx['shift_at']}")
    for r in res["runs"]:
        late = r["heal_at"] - fx["shift_at"] if r["heal_at"] else 0
        print(f"  chunk={r['chunk']:5d} dropped={r['dropped']} "
              f"healed {late} B ({late/128000*1000:.0f} ms) after the shift  {r['sha256'][:16]}")
    if not any(r["dropped"] >= 4 for r in res["runs"]):
        sys.exit("the re-lock never fired at any chunk size -- it is dead code again")

    if "--record" in sys.argv:
        EXPECTED.write_text(json.dumps(res, indent=2) + "\n")
        print(f"\nwrote {EXPECTED.relative_to(HERE.parent)}")
        return
    if not EXPECTED.exists():
        sys.exit("no expected.json -- run with --record")
    want = json.loads(EXPECTED.read_text())
    same = res == want
    print("\nPASS: matches the recorded answer" if same else "\nFAIL: differs from expected.json")
    sys.exit(0 if same else 1)


if __name__ == "__main__":
    main()
