# Vivado non-project batch flow for the Basys 3 synth (open-tool fallback backend).
# Vivado infers DSP48E1 from the XLS `*` operators and RAMB36E1 from the sync-read
# delay lines automatically (no -nodsp/-nobram games, no CARRYCASCIN routing bug).
# Run: vivado -mode batch -source build_vivado.tcl
set part xc7a35tcpg236-1
read_verilog [list engine.v top.v]
read_xdc basys3.xdc
synth_design -top top -part $part -directive AreaOptimized_high

# --- clock-enable multicycle -------------------------------------------------
# One 100 MHz clock, but the DSLX engine advances on a /3 clock-enable and the
# post-mix effects FSM on a /6 enable (see `ce`/`ce8` in top.v). Unconstrained,
# ~24k of these paths read as 10 ns "violations", so timing-driven place/route
# thrashes and — on the larger 8-comb reverb — mis-places the BRAM effect
# datapath, so the delay lines read garbage on hardware (effects silent). These
# paths genuinely have >=3 clocks; constrain them so P&R optimizes reality.
# Effect datapath regs (top-level, updated ONLY on the /6 effects enable) — matched
# by name so this survives hierarchy flattening. NOTE: waddr*/raddr* are excluded
# on purpose (they increment every clock during the power-up clearing sweep).
set fx_ffs [get_cells -hier -filter {IS_SEQUENTIAL && (NAME =~ *csrL_reg* || NAME =~ *csrR_reg* || \
    NAME =~ *accL_reg* || NAME =~ *accR_reg* || NAME =~ *cp?L_reg* || NAME =~ *cp?R_reg* || \
    NAME =~ *dlp?L_reg* || NAME =~ *dlp?R_reg* || NAME =~ *apyL_reg* || NAME =~ *apyR_reg* || \
    NAME =~ *revwetL_reg* || NAME =~ *revwetR_reg* || NAME =~ *ecwL_reg* || NAME =~ *ecwR_reg* || \
    NAME =~ *rin_r_reg* || NAME =~ *echodL_reg* || NAME =~ *echodR_reg* || \
    NAME =~ *sampL_reg* || NAME =~ *sampR_reg* || \
    NAME =~ *dwd2_reg* || NAME =~ *dwdL_reg* || NAME =~ *dwdR_reg*)}]
# sampL/sampR/dwd2/dwdL/dwdR were missing from the list above and are all written only inside
# `if (ce8)`, so they genuinely have 6 clocks. Unconstrained, sampL/sampR were the entire
# worst-five of build/timing.rpt and dwd2 was the worst path in the design (17.2 ns against a
# 10 ns requirement, WNS -7.321, from the reverb-tank BRAM output) -- which made the report
# useless for spotting a path that is actually late.
set eng_ffs [get_cells -hier -filter {IS_SEQUENTIAL && NAME =~ *eng*}]
puts "MCP: fx_ffs=[llength $fx_ffs]  eng_ffs=[llength $eng_ffs]"
set_multicycle_path 3 -setup -to $fx_ffs
set_multicycle_path 2 -hold  -to $fx_ffs
if {[llength $eng_ffs] > 0} {
  set_multicycle_path 3 -setup -from $eng_ffs -to $eng_ffs
  set_multicycle_path 2 -hold  -from $eng_ffs -to $eng_ffs
  # The engine's backpressure `_midi_in_rdy` = stage_outputs_ready_0 is a COMBINATIONAL chain
  # back through all 48 stages (~13 ns), and it leaves the engine to drive the shell's `mvld`.
  # `-from eng_ffs -to eng_ffs` above does not cover that crossing, so it read as -3.304 ns --
  # on the one register that carries MIDI bytes into the engine, which is alarming and wrong.
  # top.v clears mvld only on `mvld && mrdy && ce`: `ce` is an AND term, so mrdy is sampled
  # only on a /3 enable and physically has 30 ns. Constrain the crossing, not the register --
  # mvld itself has no clock enable and its rxhave/dinhave cone is genuinely single-cycle.
  set mvld_ff [get_cells -hier -filter {IS_SEQUENTIAL && NAME =~ *mvld_reg*}]
  if {[llength $mvld_ff] > 0} {
    set_multicycle_path 3 -setup -from $eng_ffs -to $mvld_ff
    set_multicycle_path 2 -hold  -from $eng_ffs -to $mvld_ff
  }
}
# Power-on reset only: `rc` is a 5-bit counter that saturates at 5'h1f and never moves again
# (top.v), so `rst` is static within 310 ns of power-up and its deassertion timing is irrelevant.
# Left unconstrained it sourced 1463 of the 1738 endpoint groups in the M28a census -- pure noise
# that buries anything real. False-path it so the census means something.
set rst_src [get_cells -hier -filter {IS_SEQUENTIAL && NAME =~ *rc_reg*}]
if {[llength $rst_src] > 0} { set_false_path -from $rst_src }

opt_design
place_design
phys_opt_design
route_design
# Reports (utilisation shows DSP48/BRAM/slice; timing shows the real critical path).
report_utilization           -file util.rpt
report_timing_summary -delay_type max -max_paths 5 -file timing.rpt

# Census of EVERY failing setup endpoint, not just the worst five.
#
# The claim this build rests on is "the residual TNS is all /3 and /6 paths that physically have
# 3 or 6 clocks". With -max_paths 5 that claim cannot be checked: M28a went looking and found the
# report named 5 endpoints out of 5643, so 5638 were taken on faith. A single genuine 10 ns
# violation hiding in that set would be a marginal, temperature-dependent, intermittent hardware
# fault -- which is exactly the shape of the bug M28a was chasing -- and it would be invisible in
# simulation. Group the endpoints by name so the answer is one page instead of 5643.
set fh [open timing_endpoints.rpt w]
set viol [get_timing_paths -setup -max_paths 20000 -nworst 1 -slack_lesser_than 0]
puts $fh "failing setup endpoints: [llength $viol]"
array set grp {}
foreach p $viol {
  set ep [get_property NAME [get_property ENDPOINT_PIN $p]]
  regsub {\[[0-9]+\]} $ep {[*]} ep                        ;# collapse bus bits
  # Endpoint alone cannot tell a real violation from a missing exception: -3.304 ns on mvld_reg/D
  # is a crisis if it starts at rxhave_reg and a non-event if it starts inside the engine. Key the
  # census on startpoint -> endpoint so the next reader does not have to guess the way M28a did.
  set spo [get_property STARTPOINT_PIN $p]
  set sp [expr {[llength $spo] > 0 ? [get_property NAME $spo] : "?"}]
  regsub {\[[0-9]+\]} $sp {[*]} sp
  set k "$sp -> $ep"
  set s [get_property SLACK $p]
  if {[info exists grp($k)]} {
    set grp($k) [list [expr {[lindex $grp($k) 0] + 1}] [expr {min([lindex $grp($k) 1], $s)}]]
  } else {
    set grp($k) [list 1 $s]
  }
}
foreach k [lsort [array names grp]] {
  puts $fh [format "%6d  worst %8.3f ns  %s" [lindex $grp($k) 0] [lindex $grp($k) 1] $k]
}
close $fh

# Bitstream: with the multicycle above, real paths meet; any residual 100 MHz TNS is
# on unconstrained /3-/6 paths that physically have >=3 clocks — write it regardless.
set_property SEVERITY {Warning} [get_drc_checks NSTD-1]
set_property SEVERITY {Warning} [get_drc_checks UCIO-1]
write_bitstream -force top.bit
puts "VIVADO_BUILD_DONE"
