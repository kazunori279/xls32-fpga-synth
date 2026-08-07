`timescale 1ns/1ps
// Does the Basys 3 shell deliver every MIDI byte the host sends?
//
// M27 left 6/274 factory presets railing on Basys 3 and 0/274 on Tiliqua, booked as "the
// fixed-point SVF diverges". Two of the six (`Brightness` reso 0, `Synth Strings 1` reso 5) sit at
// the LEAST resonant setting CC71 has, which is not how a filter diverges -- so the suspicion moved
// to the wire. top.v's RX has no FIFO: `rxbyte <= rxsh; rxhave <= 1;` runs unconditionally at the
// end of every byte, so a byte the engine has not collected yet is overwritten and gone. Total
// buffering is two bytes (`rxbyte` + `mdata`/`mvld`), the engine only accepts on `mrdy && ce`, and
// `_midi_in_rdy` goes low for the ~800 of every 3125 clocks the 48-stage pipeline spends
// backpressured on the 32 kHz `audio_out` tick. At 2 Mbaud a byte lands every 500 clocks.
//
// This bit-bangs the exact burst `validate_hw.capture()` sends -- 128 note-offs then the preset's
// CCs, back to back with no host-side pacing -- into the REAL top.v + engine.v, and compares what
// the engine accepted against what was sent.
//
// The comparison is deliberately made at the `mvld && mrdy && ce` handshake rather than by peeking
// at `rxhave`: that keeps the testbench valid across the FIFO rewrite that is supposed to fix this,
// and it measures the thing that actually matters -- bytes the ENGINE saw -- rather than an
// implementation detail of how they were lost.
//
//   iverilog -o /tmp/tbdrop core/sim/tb_midi_drop.v boards/basys3/rtl/top.v build/engine.v
//   vvp /tmp/tbdrop
//
// iverilog has no BUFG primitive, so provide a pass-through stub.
module BUFG(input I, output O); assign O = I; endmodule

module tb_midi_drop;
    reg clk = 1'b0;
    reg rsrx = 1'b1;              // FT2232 UART, idle high
    wire [15:0] led;
    wire rstx, i2s_bclk, i2s_ws, i2s_sd;

    // midi_din must be driven: an undriven DIN input floats to x and the 31250-baud FSM would
    // latch garbage bytes into the same stream this testbench is counting.
    top dut(.clk(clk), .RsRx(rsrx), .led(led), .RsTx(rstx), .midi_din(1'b1),
            .i2s_bclk(i2s_bclk), .i2s_ws(i2s_ws), .i2s_sd(i2s_sd));

    always #5 clk = ~clk;            // 100 MHz
    localparam integer BAUD = 50;    // matches top.v (2 Mbaud @ 100 MHz)

    // ---- what we sent ------------------------------------------------------------------------
    reg [7:0] sent [0:1023];
    integer   nsent = 0;
    task push(input [7:0] b); begin sent[nsent] = b; nsent = nsent + 1; end endtask
    task push_cc(input [7:0] c, input [7:0] v); begin push(8'hB0); push(c); push(v); end endtask

    // One byte, LSB-first, start + 8 data + stop and NOTHING else. A real FTDI stream has no idle
    // gap between bytes at line rate, and the gap is exactly what would hide this bug.
    task send_byte(input [7:0] b);
        integer i;
        begin
            rsrx = 1'b0; repeat (BAUD) @(posedge clk);            // start bit
            for (i = 0; i < 8; i = i + 1) begin
                rsrx = b[i]; repeat (BAUD) @(posedge clk);        // data bits, LSB first
            end
            rsrx = 1'b1; repeat (BAUD) @(posedge clk);            // stop bit
        end
    endtask

    // ---- what the engine accepted ------------------------------------------------------------
    reg [7:0] got [0:1023];
    integer   ngot = 0;
    always @(posedge clk) if (dut.mvld && dut.mrdy && dut.ce && ngot < 1024) begin
        got[ngot] = dut.mdata; ngot = ngot + 1;
    end

    // ---- is the shell actually under the pressure this is supposed to measure? ----------------
    // A zero drop count is only meaningful if the engine really does stall on `audio_out` while
    // bytes are arriving. These three numbers say whether the test had teeth: how long `mrdy` goes
    // away for, how long a byte sits in `mvld` waiting, and how many samples the engine produced
    // during the burst.
    //
    // They are gated on `bursting`: the 16384-clock BRAM `clearing` sweep after reset also parks
    // `mrdy` low for a very long time, and counting that would report pressure the burst never saw.
    reg bursting = 1'b0;
    integer rdylow = 0, rdylow_max = 0, vldwait = 0, vldwait_max = 0, nsamp = 0, nfull = 0;
    always @(posedge clk) if (bursting) begin
        rdylow  = dut.mrdy ? 0 : rdylow + 1;
        if (rdylow  > rdylow_max)  rdylow_max  = rdylow;
        vldwait = dut.mvld ? vldwait + 1 : 0;
        if (vldwait > vldwait_max) vldwait_max = vldwait;
        if (dut.ardy && dut.avld && dut.ce) nsamp = nsamp + 1;
        // The moment that actually decides this: a byte completes (`top.v:91-93`, the cycle
        // `rxbyte <= rxsh; rxhave <= 1` runs) while the previous one is still sitting in `rxbyte`.
        // Counting the overwrite directly, not just its consequence, says whether the shell ever
        // even came close -- a zero here and a zero at the engine mean different things.
        if (dut.rxa && dut.rxd == 16'd0 && dut.rxb == 4'd8 && dut.rxhave) nfull = nfull + 1;
    end

    // ---- the burst: validate_hw.capture(), byte for byte -------------------------------------
    // `Brightness` (webui/presets_soundfont.json) -- one of the six that rail -- in CC_MAP order
    // (presetgen/calibrate.py:40). It carries no `dtime`/`room` key, so neither is sent.
    integer n, i, drops, first_bad;
    initial begin
        for (n = 0; n < 128; n = n + 1) begin push(8'h80); push(n[7:0]); push(8'd0); end
        push_cc(8'd70, 8'd0);    push_cc(8'd75, 8'd50);  push_cc(8'd78, 8'd64);
        push_cc(8'd73, 8'd0);    push_cc(8'd74, 8'd74);  push_cc(8'd71, 8'd0);
        push_cc(8'd72, 8'd0);    push_cc(8'd24, 8'd72);  push_cc(8'd25, 8'd5);
        push_cc(8'd26, 8'd68);   push_cc(8'd27, 8'd9);   push_cc(8'd79, 8'd67);
        push_cc(8'd20, 8'd41);   push_cc(8'd21, 8'd127); push_cc(8'd22, 8'd59);
        push_cc(8'd23, 8'd72);   push_cc(8'd76, 8'd26);  push_cc(8'd77, 8'd21);
        push_cc(8'd92, 8'd64);   push_cc(8'd80, 8'd0);   push_cc(8'd5,  8'd0);
        push_cc(8'd93, 8'd0);    push_cc(8'd94, 8'd0);   push_cc(8'd95, 8'd64);

        // power-on reset + the 16384-slot BRAM clearing sweep, then a few samples so the engine is
        // in its steady stall-on-audio_out rhythm before the burst starts.
        repeat (40000) @(posedge clk);
        bursting = 1'b1;
        for (i = 0; i < nsent; i = i + 1) send_byte(sent[i]);
        repeat (20000) @(posedge clk);        // let the tail drain out of the shell
        bursting = 1'b0;

        drops = nsent - ngot;
        first_bad = -1;
        for (i = 0; i < ngot; i = i + 1)
            if (first_bad < 0 && got[i] !== sent[i]) first_bad = i;
        $display("");
        $display("sent %0d bytes at 2 Mbaud, engine accepted %0d  -> %0d LOST", nsent, ngot, drops);
        $display("during the burst: %0d samples, mrdy low up to %0d clocks, byte waited in mvld up to %0d clocks (one arrives every %0d)",
                 nsamp, rdylow_max, vldwait_max, 10 * BAUD);
        $display("rxbyte overwritten while still full: %0d times", nfull);
        if (first_bad >= 0) begin
            $display("first divergence at byte %0d: sent %02h, engine got %02h", first_bad,
                     sent[first_bad], got[first_bad]);
            // From the first loss onward the stream is shifted, so every later CC lands on the
            // wrong controller. Show the window around it -- that is the mechanism, not just the count.
            for (i = (first_bad > 3 ? first_bad - 3 : 0); i < first_bad + 9 && i < ngot; i = i + 1)
                $display("   [%0d] sent %02h  got %02h%s", i, sent[i], got[i],
                         (sent[i] === got[i]) ? "" : "   <-- shifted");
        end
        $display("%s", drops == 0 ? "PASS: no MIDI bytes dropped"
                                  : "FAIL: the shell dropped MIDI bytes");
        $finish;
    end
endmodule
