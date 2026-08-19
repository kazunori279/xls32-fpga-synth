`timescale 1ns/1ps
// Does the pulse wave's DC offset cost headroom *inside* the engine?
//
// A pulse at anything but 50 % duty carries a DC term, and the demo patches run PULSE W at 100 of
// 128 -- `pwr = pw << 1` makes that 200 of 256, about 78 %. dc_block.py high-passes the USB tee so
// the offset is no longer observable over USB, but the tee is downstream of `scale_mix`, which is
// where the mix is clamped to +-32767. If the offset is eating headroom, a loud four-part passage
// clips asymmetrically: the positive rail is hit and the negative one is not, the waveform is
// squared off on one side only, and no test downstream of the clamp can see it. That is the open
// half of issue #2, and it is a question about the clamp, so this counts clamp hits.
//
//   SKIP_BUILD=1 bash boards/tiliqua/build.sh   # produces build/tiliqua/engine.v
//   iverilog -g2012 -o /tmp/tbhr core/sim/tb_headroom.v build/tiliqua/engine.v && vvp /tmp/tbhr
//
// The mix is offset binary: silence 32768, positive clamp 65535, negative clamp 1.
module tb_headroom;
    localparam integer MID  = 32768;
    localparam integer HI   = 65535;        // scale_mix clamped at +32767
    localparam integer LO   = 1;            // scale_mix clamped at -32767
    localparam integer WARM = 400;          // samples for the attack to reach sustain
    localparam integer RUN  = 1200;         // measurement window

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
        begin send_midi(8'h90 | {6'd0, ch}); send_midi(n); send_midi(8'd127); end endtask
    task cc(input [1:0] ch, input [7:0] c, input [7:0] v);
        begin send_midi(8'hB0 | {6'd0, ch}); send_midi(c); send_midi(v); end endtask

    integer nframe = 0;
    always @(posedge clk) if (!rst && viz_vld && ce && viz[17]) nframe = nframe + 1;
    task wait_samples(input integer n);
        integer target; begin target = nframe + n; while (nframe < target) @(posedge clk); end
    endtask

    // clamp hits and DC, over a window
    reg watching = 1'b0;
    integer nhi = 0, nlo = 0, nsamp = 0, smin = 0, smax = 0;
    real    sum = 0.0;
    always @(posedge clk) if (watching && audio_vld && ce) begin
        nsamp = nsamp + 1;
        sum   = sum + ($signed({16'd0, audio}) - MID);
        if ($signed({16'd0, audio}) - MID < smin) smin = $signed({16'd0, audio}) - MID;
        if ($signed({16'd0, audio}) - MID > smax) smax = $signed({16'd0, audio}) - MID;
        if (audio >= HI) nhi = nhi + 1;
        if (audio <= LO) nlo = nlo + 1;
    end
    task measure(input integer n);
        begin nhi = 0; nlo = 0; nsamp = 0; sum = 0.0; smin = 0; smax = 0;
              watching = 1'b1; wait_samples(n); watching = 1'b0; end
    endtask

    // `nv` sustaining voices spread over the four parts, as loud as the CCs allow
    task load_all(input [7:0] pw, input integer nv);
        integer p, n;
        begin
            for (p = 0; p < 4; p = p + 1) begin
                cc(p[1:0], 8'd70, 8'd32);       // wave = 2 (pulse); evv[4:7] == 2
                cc(p[1:0], 8'd75, pw);          // pulse width; pwr = pw << 1, of 256
                cc(p[1:0], 8'd7,  8'd127);      // part volume
                cc(p[1:0], 8'd22, 8'd127);      // amp sustain: hold, do not decay away
                cc(p[1:0], 8'd73, 8'd0);        // no sub-osc, so the pulse is the whole signal
            end
            for (n = 0; n < nv; n = n + 1)
                note_on(n[1:0], 8'd40 + n[7:0] * 8'd2);
        end
    endtask
    task release_all;
        integer p; begin for (p = 0; p < 4; p = p + 1) cc(p[1:0], 8'd120, 8'd0);
                         wait_samples(300); end
    endtask

    task report(input [1023:0] what);
        begin
            $display("  %0s", what);
            $display("    mean %8.1f counts   range %0d .. %0d", sum / nsamp, smin, smax);
            $display("    clamp hits: +%0d / -%0d  of %0d samples", nhi, nlo, nsamp);
        end
    endtask

    integer nv, first_clip;
    reg [7:0] pwv;
    initial begin
        @(negedge rst); repeat (5) @(posedge clk);

        // --- how much polyphony does the offset alone survive? -------------------------------
        // The DC is per voice and the voices add, so this is the number that decides whether the
        // shipped demo is affected or only a pathological worst case. Bach's Prelude in C runs
        // four parts, so anything that starts clipping in single digits matters.
        $display("\npulse at 78%% duty (CC75 = 100), full velocity, by polyphony");
        $display("  voices |     mean |     min |    max | clamp +/-");
        first_clip = 0;
        for (nv = 1; nv <= 32; nv = nv * 2) begin
            load_all(8'd100, nv);
            wait_samples(WARM);
            measure(RUN);
            $display("  %6d | %8.1f | %7d | %6d | +%0d / -%0d",
                     nv, sum / nsamp, smin, smax, nhi, nlo);
            if (first_clip == 0 && nhi > 0) first_clip = nv;
            release_all;
        end

        // --- the control: 50 % duty, no DC term ----------------------------------------------
        $display("\nthe same at 50%% duty (CC75 = 64), which has no DC term");
        $display("  voices |     mean |     min |    max | clamp +/-");
        for (nv = 1; nv <= 32; nv = nv * 2) begin
            load_all(8'd64, nv);
            wait_samples(WARM);
            measure(RUN);
            $display("  %6d | %8.1f | %7d | %6d | +%0d / -%0d",
                     nv, sum / nsamp, smin, smax, nhi, nlo);
            release_all;
        end

        // --- and across the pulse widths the shipped bank actually uses ----------------------
        // The demo patches' CC75 = 100 is nowhere near the worst case. Of the 76 pulse presets in
        // webui/presets_*.json the widths run from 5 to 124 -- 4% to 97% duty -- and the DC goes
        // as |2*duty - 1|, so pw = 5 is 1.6x further off centre than pw = 100 and of the opposite
        // sign. Four voices, because that is what the four-part demo plays.
        $display("\nfour voices, by pulse width (the range the shipped bank spans)");
        $display("     pw | duty%% |     mean |     min |    max | clamp +/-");
        for (nv = 0; nv < 8; nv = nv + 1) begin
            case (nv)
                0: pwv = 8'd5;   1: pwv = 8'd19;  2: pwv = 8'd48;  3: pwv = 8'd64;
                4: pwv = 8'd74;  5: pwv = 8'd88;  6: pwv = 8'd117; default: pwv = 8'd124;
            endcase
            load_all(pwv, 4);
            wait_samples(WARM);
            measure(RUN);
            $display("  %5d | %4d%% | %8.1f | %7d | %6d | +%0d / -%0d",
                     pwv, (pwv * 200) / 256, sum / nsamp, smin, smax, nhi, nlo);
            release_all;
        end

        // The question is not "does it clip" -- enough voices at full velocity are meant to. It
        // is whether the DC makes the clipping one-sided, which costs headroom that no test
        // downstream of the clamp can see.
        $display("\nverdict");
        if (first_clip == 0)
            $display("  78%% duty never reaches the clamp, even at 32 voices");
        else
            $display("  78%% duty first hits the positive clamp at %0d voices", first_clip);
        $finish;
    end
    initial begin #4000000000; $display("TIMEOUT at sample %0d", nframe); $finish; end
endmodule
