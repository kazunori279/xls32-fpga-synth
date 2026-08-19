`timescale 1ns/1ps
// Does the mix return to exactly zero after every voice has been silenced?
//
// It did not. Each voice's Chamberlin SVF leaks its integrator state with `low1 - (low1 >> 7)`,
// and `>>` on a signed value in DSLX is an *arithmetic* shift -- it rounds toward minus infinity.
// So the two halves of the number line behave differently:
//
//   low1 = +100   ->  100 >> 7 = 0   ->  low2 = 100      latched, forever
//   low1 = -100   -> -100 >> 7 = -1  ->  low2 =  -99     decays to zero, as intended
//
// Every positive residue below 128 (below 64 for `band`, which leaks by >>6) is a fixed point of
// the leak. A voice that has ever sounded parks on one and holds it for the rest of the power
// cycle, and thirty-two of them add. tb_panic found this and worked around it, asserting only
// that the mix stops *moving* after All Sound Off. This testbench asserts the stronger thing the
// message actually promises, and prints the residual either way so the number is on the record.
//
//   SKIP_BUILD=1 bash boards/tiliqua/build.sh   # produces build/tiliqua/engine.v
//   iverilog -g2012 -o /tmp/tbdc core/sim/tb_dc.v build/tiliqua/engine.v && vvp /tmp/tbdc
//
// See issue #1. The measurement is the audio mix, in offset binary: silence is 32768.
module tb_dc;
    localparam integer MID = 16'd32768;
    localparam integer W   = 200;           // samples for an envelope to settle either way
    localparam integer SETTLE = 600;        // samples to let the SVF leak run to its fixed point

    reg clk = 1'b0, rst = 1'b1;
    reg [1:0] cec = 2'd0; wire ce = (cec == 2'd0);
    reg [7:0] midi_data = 8'd0; reg midi_vld = 1'b0; wire midi_rdy;
    wire [15:0] audio; wire audio_vld;
    wire [31:0] viz; wire viz_vld;
    xls_engine dut(.clk(clk), .rst(rst), .ce(ce), ._midi_in(midi_data), ._midi_in_vld(midi_vld),
                   ._audio_out_rdy(1'b1), ._midi_in_rdy(midi_rdy),
                   ._audio_out(audio), ._audio_out_vld(audio_vld),
                   ._viz_out(viz), ._viz_out_vld(viz_vld), ._viz_out_rdy(1'b1));
    always #5 clk = ~clk;
    always @(posedge clk) cec <= (cec == 2'd2) ? 2'd0 : cec + 2'd1;
    initial begin repeat (20) @(posedge clk); rst <= 1'b0; end

    task send_midi(input [7:0] b);
        begin
            @(negedge clk); midi_data <= b; midi_vld <= 1'b1;
            @(posedge clk); while (!(midi_rdy && ce)) @(posedge clk);
            @(negedge clk); midi_vld <= 1'b0;
            repeat (3) @(posedge clk);
        end
    endtask
    task note_on (input [1:0] ch, input [7:0] n);
        begin send_midi(8'h90 | {6'd0, ch}); send_midi(n); send_midi(8'd100); end endtask
    task cc(input [1:0] ch, input [7:0] c, input [7:0] v);
        begin send_midi(8'hB0 | {6'd0, ch}); send_midi(c); send_midi(v); end endtask

    // one tick per sample, off the visualiser's end-of-ring marker
    integer nframe = 0;
    always @(posedge clk) if (!rst && viz_vld && ce && viz[17]) nframe = nframe + 1;
    task wait_samples(input integer n);
        integer target; begin target = nframe + n; while (nframe < target) @(posedge clk); end
    endtask

    // min/max of the mix over a window, as signed counts away from silence
    reg watching = 1'b0;
    integer amin = 32'h7FFFFFFF, amax = -32'h7FFFFFFF;
    always @(posedge clk) if (watching && audio_vld && ce) begin
        if ($signed({16'd0, audio}) - MID < amin) amin = $signed({16'd0, audio}) - MID;
        if ($signed({16'd0, audio}) - MID > amax) amax = $signed({16'd0, audio}) - MID;
    end
    task measure(input integer n);
        begin amin = 32'h7FFFFFFF; amax = -32'h7FFFFFFF; watching = 1'b1;
              wait_samples(n); watching = 1'b0; end
    endtask

    task fast_env(input [1:0] ch);
        begin cc(ch, 8'd20, 8'd0); cc(ch, 8'd21, 8'd0); cc(ch, 8'd22, 8'd100); cc(ch, 8'd23, 8'd0); end
    endtask

    // Silence the part, let the leak run out, and report where the mix parked.
    integer fails = 0;
    task check_silent(input [1023:0] what);
        begin
            wait_samples(SETTLE);
            measure(100);
            if (amin == 0 && amax == 0)
                $display("  ok   %0s: mix is exactly 0", what);
            else begin
                fails = fails + 1;
                $display("  FAIL %0s: mix parked at %0d..%0d counts off silence", what, amin, amax);
            end
        end
    endtask

    integer ki, kn;
    initial begin
        @(negedge rst); repeat (5) @(posedge clk);
        for (ki = 0; ki < 4; ki = ki + 1) fast_env(ki[1:0]);

        // Nothing has sounded yet, so nothing can have latched. If this fails the fault is
        // elsewhere and every number below is meaningless.
        $display("\nbefore anything sounds");
        measure(100);
        if (amin == 0 && amax == 0) $display("  ok   reset state is exactly 0");
        else begin fails = fails + 1; $display("  FAIL reset mix is %0d..%0d", amin, amax); end

        // --- one part, one note --------------------------------------------------------------
        $display("\none note on part 0, then All Sound Off");
        note_on(2'd0, 8'd60);
        wait_samples(W);
        cc(2'd0, 8'd120, 8'd0);
        check_silent("after one voice");

        // --- all four parts, eight notes each: every one of the 32 slots gets used -------------
        $display("\neight notes on each of four parts, then All Sound Off everywhere");
        for (ki = 0; ki < 4; ki = ki + 1)
            for (kn = 0; kn < 8; kn = kn + 1)
                note_on(ki[1:0], 8'd48 + kn[7:0] * 8'd3 + ki[7:0]);
        wait_samples(W);
        for (ki = 0; ki < 4; ki = ki + 1) cc(ki[1:0], 8'd120, 8'd0);
        check_silent("after all 32 slots");

        // --- and again with the filter driven as hard as the CCs allow -------------------------
        // The residual is a fixed point of the SVF's leak, so it scales with how much state the
        // filter was holding. CC74 cutoff and CC71 resonance both max: the state is largest and
        // the coupling between `low` and `band` strongest, which is the worst case for the latch.
        $display("\nsame, with cutoff (CC74) and resonance (CC71) at maximum");
        for (ki = 0; ki < 4; ki = ki + 1) begin
            cc(ki[1:0], 8'd74, 8'd127); cc(ki[1:0], 8'd71, 8'd127);
        end
        for (ki = 0; ki < 4; ki = ki + 1)
            for (kn = 0; kn < 8; kn = kn + 1)
                note_on(ki[1:0], 8'd60 + kn[7:0] * 8'd2 + ki[7:0]);
        wait_samples(W);
        for (ki = 0; ki < 4; ki = ki + 1) cc(ki[1:0], 8'd120, 8'd0);
        check_silent("after 32 slots at max cutoff and resonance");

        $display("\n%0s", fails == 0 ? "PASS: silence is silent"
                                     : "FAIL: the SVF leak has a positive fixed point (issue #1)");
        $finish;
    end
    initial begin #2000000000; $display("TIMEOUT at sample %0d", nframe); $finish; end
endmodule
