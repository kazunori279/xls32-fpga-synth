#!/usr/bin/env bash
# synth.x (DSLX proc `engine`) -> pipelined Verilog. The one path every board takes.
#
# The engine knows nothing about a board: it has three channels (midi_in, audio_out,
# viz_out) and a clock. Everything board-specific — pins, clock rate, transport —
# lives in boards/<name>/. Keep it that way; see docs/TILIQUA_PORT.md section 3.
#
# Env:
#   XLS_DIR  (required) unpacked XLS release containing ir_converter_main etc.
#   SRC      DSLX source                        (default: synth.x next to this script,
#                                                or ./synth.x when run flat on a build VM)
#   OUT      Verilog output                     (default: engine.v beside SRC)
#   STAGES   --pipeline_stages                  (default: 48)
#   WCT      --worst_case_throughput            (default: 48)
#   FIXUP    run fix_verilog.py afterwards      (default: 1; set 0 where python is absent,
#                                                e.g. inside the bare ubuntu-base container)
#   PY       python used for fix_verilog.py     (default: python3)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${XLS_DIR:?set XLS_DIR to the unpacked XLS release directory}"

# Prefer a sibling synth.x (repo layout); fall back to the cwd (flat layout on the build VM).
if [ -z "${SRC:-}" ]; then
  if [ -f "$HERE/synth.x" ]; then SRC="$HERE/synth.x"; else SRC="synth.x"; fi
fi
OUT="${OUT:-$(dirname "$SRC")/engine.v}"
STAGES="${STAGES:-48}"; WCT="${WCT:-48}"
FIXUP="${FIXUP:-1}"; PY="${PY:-python3}"

IR="${OUT%.v}.ir"; OPT="${OUT%.v}.opt.ir"

echo "==> XLS codegen: $(basename "$SRC") -> $(basename "$OUT") (stages=$STAGES wct=$WCT)"
"$XLS_DIR/ir_converter_main" --top=engine "$SRC" > "$IR"
"$XLS_DIR/opt_main" "$IR" > "$OPT"
"$XLS_DIR/codegen_main" --generator=pipeline --pipeline_stages="$STAGES" \
  --worst_case_throughput="$WCT" --delay_model=unit --use_system_verilog=false \
  --reset=rst --reset_active_low=false --reset_asynchronous=false \
  --top=engine --module_name=xls_engine --output_verilog_path="$OUT" "$OPT"

# yosys can't elaborate XLS's dynamic-index genvar loops; unroll them.
if [ "$FIXUP" = "1" ]; then
  FIX="$HERE/fix_verilog.py"; [ -f "$FIX" ] || FIX="fix_verilog.py"
  "$PY" "$FIX" "$OUT"
fi
