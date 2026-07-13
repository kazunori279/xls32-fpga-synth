#!/usr/bin/env python3
"""Post-process XLS pipeline Verilog for the F4PGA flow. Two transforms:

1. Unroll XLS's `for (genvar ...) ... assign a[i] = ...; end` generate loops
   (emitted for dynamic-index array updates) into explicit assigns, so F4PGA's
   plain-Verilog yosys accepts them.

2. Add a global clock-enable `ce` to the engine's single pipeline always-block.
   F4PGA/VPR floors this design at ~15ns (wide MUXF6 mux trees; no DSP48, no BRAM
   inference, no MMCM), so we can't close 100MHz. Instead the shell drives `ce` at
   half rate (divide-by-2), making every register path multicycle (20ns budget) so
   the 15ns paths are safe at an effective 50MHz. VPR still reports the 15ns paths
   as failing (it can't see the multicycle), so timing must be reasoned, not read
   from the report: safe iff single-cycle critical path < (ce-period x 10ns).

Usage: fix_verilog.py file.v
"""
import sys, re

f = sys.argv[1]
src = open(f).read()
pat = re.compile(
    r'for \(genvar (\w+) = 0; \1 < (\d+); \1 = \1 \+ 1\) begin : \w+\s*\n'
    r'\s*(assign [^\n]+?);\s*\n\s*end')

def unroll(m):
    var, n, stmt = m.group(1), int(m.group(2)), m.group(3)
    return '\n'.join('  ' + re.sub(r'\b' + re.escape(var) + r'\b', str(k), stmt) + ';'
                     for k in range(n))

src, count = pat.subn(unroll, src)

# --- global clock-enable ---
# add `ce` port right after the reset port
src, np = re.subn(r'(input wire rst,\n)', r'\1  input wire ce,\n', src, count=1)
# gate the pipeline registers: `end else begin` -> `end else if (ce) begin`
src, ng = re.subn(r'\bend else begin\b', 'end else if (ce) begin', src, count=1)

open(f, 'w').write(src)
print(f'unrolled {count} generate-for block(s); added ce port ({np}) and gate ({ng}) in {f}')
