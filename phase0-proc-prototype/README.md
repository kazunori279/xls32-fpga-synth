# Phase 0 — proc + pipeline codegen de-risk (GO)

Throwaway prototype that validated the **new architecture** for M6 (per-voice filter):
an XLS **proc** codegen'd with the **pipeline generator** (`--generator=pipeline`),
synthesized by F4PGA, running at full 100 MHz. Everything before this used only
combinational codegen + a hand-written register shell; this proved the proc/pipeline
flow works end-to-end on the Basys 3 before committing to the full engine rewrite.

## What it is
`uart_proc.x` — a proc that streams an incrementing byte as a 115200-baud UART bit
stream on a `chan<u1>` output. `top.v` — a trivial shell: 100 MHz via BUFG, power-on
reset, `_tx_rdy` tied high, the proc's `_tx` bit registered to `RsTx`.
`uart_proc.v` — the pipeline-codegen output (reference).

## Result (GO)
- Proc + `--generator=pipeline` synthesized **cleanly** in F4PGA (no genvar/SV issues).
- **Fmax 198 MHz** (5.0 ns critical path) — closes 100 MHz with huge margin, no clock
  enable needed.
- Flashed → UART stream **perfectly incrementing (14262/14262)**.

## Validated flow (for M6a/M6b)
- Proc syntax: `member: chan<T> out;` — do **not** name a member `out`/`in` (keywords);
  `send(join(), ch, x)`.
- Codegen: `--generator=pipeline --pipeline_stages=N` (or `--clock_period_ps=10000`)
  `--reset=rst --reset_active_low=false --reset_asynchronous=false
  --use_system_verilog=false`.
- Ports: `clk, rst, _<ch>_rdy` (input for output channels), `_<ch>` (data),
  `_<ch>_vld` (output). Standard ready/valid handshake.
- Architecture for the engine: pace at 32 kHz via **backpressure** on the audio-out
  channel (shell asserts `rdy` at the sample rate; the proc blocks on `send`); keep
  UART RX/TX in the Verilog shell.

See `../../.claude/plans/federated-sniffing-truffle.md` (M6a engine, M6b per-voice filter).
