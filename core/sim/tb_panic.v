`timescale 1ns/1ps
// Do the channel mode messages actually do what they claim?
//
// The engine used to parse 0x9n/0x8n/0xBn/0xEn and nothing else, so CC120/121/123 fell through
// `apply_cc`'s `_ => p` and vanished. That was fine until the TRS jack: a keyboard's panic button
// sends CC123, those bytes never reach the browser, and nothing else on the board could silence a
// stuck voice. M34 put the channel mode messages in the core; this is the cheapest rung that can
// tell whether they work -- no place-and-route, no board, just the generated engine.
//
//   bash boards/tiliqua/build.sh   (or SKIP_BUILD=1 ...)   # produces build/tiliqua/engine.v
//   iverilog -o /tmp/tbpanic core/sim/tb_panic.v build/tiliqua/engine.v && vvp /tmp/tbpanic
//
// The measurement is the VISUALISER tap, not the audio. viz carries {env, is_new, last, note, part}
// once per ring slot, so summing `env` over one full 32-slot pass gives a per-part energy that says
// exactly which part is sounding -- the audio mix cannot, because all four parts land in it.
// Audio is still watched for one thing: after All Sound Off the mix has to stop moving, which is
// the difference between CC120 and CC123 and is not visible in an envelope alone.
module tb_panic;
    localparam integer W = 200;             // samples to wait for an envelope to settle either way

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
    always @(posedge clk) cec <= (cec == 2'd2) ? 2'd0 : cec + 2'd1;   // engine ticks every 3rd clk
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
    task note_off(input [1:0] ch, input [7:0] n);
        begin send_midi(8'h80 | {6'd0, ch}); send_midi(n); send_midi(8'd0); end endtask
    task cc(input [1:0] ch, input [7:0] c, input [7:0] v);
        begin send_midi(8'hB0 | {6'd0, ch}); send_midi(c); send_midi(v); end endtask

    // ---- per-part energy, one number per sample ------------------------------------------------
    // Blocking assignments on purpose: `frame` has to include the slot that carried `last`, and the
    // accumulator has to be clear before the next sample's first slot arrives. `kf` is this block's
    // alone -- a loop variable shared with the stimulus below gets clobbered between its iterations.
    integer kf;
    reg [31:0] acc [0:3];
    reg [31:0] frame [0:3];
    integer nframe = 0;
    initial for (kf = 0; kf < 4; kf = kf + 1) begin acc[kf] = 0; frame[kf] = 0; end
    always @(posedge clk) if (!rst && viz_vld && ce) begin
        acc[viz[26:25]] = acc[viz[26:25]] + {16'd0, viz[15:0]};
        if (viz[17]) begin
            for (kf = 0; kf < 4; kf = kf + 1) begin frame[kf] = acc[kf]; acc[kf] = 0; end
            nframe = nframe + 1;
        end
    end
    task wait_samples(input integer n);
        integer target; begin target = nframe + n; while (nframe < target) @(posedge clk); end
    endtask

    // Peak-to-peak of the mix over a window. Not "is it exactly 32768": each voice's SVF keeps a
    // little integer state that the >>6/>>7 leak cannot shift below 1 LSB, so a part that has ever
    // sounded leaves a few hundred counts of DC behind for good. That is pre-existing and inaudible.
    // What All Sound Off has to deliver is that the mix stops *moving*.
    reg watching = 1'b0;
    reg [15:0] amin = 16'hFFFF, amax = 16'd0;
    always @(posedge clk) if (watching && audio_vld && ce) begin
        if (audio < amin) amin = audio;
        if (audio > amax) amax = audio;
    end
    task measure(input integer n);
        begin amin = 16'hFFFF; amax = 16'd0; watching = 1'b1; wait_samples(n); watching = 1'b0; end
    endtask

    integer fails = 0;
    task expect_loud(input [1023:0] what, input integer p);
        begin
            if (frame[p] > 32'd0) $display("  ok   %0s (part %0d energy %0d)", what, p, frame[p]);
            else begin fails = fails + 1; $display("  FAIL %0s (part %0d is silent)", what, p); end
        end
    endtask
    task expect_quiet(input [1023:0] what, input integer p);
        begin
            if (frame[p] == 32'd0) $display("  ok   %0s (part %0d silent)", what, p);
            else begin fails = fails + 1; $display("  FAIL %0s (part %0d still at %0d)", what, p, frame[p]); end
        end
    endtask

    // Short envelopes everywhere: the default release is ~3300 samples, which would make this
    // testbench take minutes to say something it can say in a hundred samples. CC20/21/23 index
    // TIME_INC, and 0 picks the fastest rate (~100 samples end to end).
    task fast_env(input [1:0] ch);
        begin cc(ch, 8'd20, 8'd0); cc(ch, 8'd21, 8'd0); cc(ch, 8'd22, 8'd100); cc(ch, 8'd23, 8'd0); end
    endtask

    integer ki, pp_play, pp_dead;
    initial begin
        @(negedge rst); repeat (5) @(posedge clk);
        for (ki = 0; ki < 4; ki = ki + 1) fast_env(ki[1:0]);

        // --- CC123 All Notes Off: the part goes quiet, but through the release ------------------
        $display("\nCC123 All Notes Off (part 0)");
        note_on(2'd0, 8'd60); note_on(2'd0, 8'd64); note_on(2'd0, 8'd67);
        wait_samples(W);
        expect_loud("three notes are sounding", 0);
        cc(2'd0, 8'd123, 8'd0);
        wait_samples(3);
        expect_loud("still audible a few samples later -- it releases, it does not cut", 0);
        wait_samples(W);
        expect_quiet("gone after the release", 0);

        // --- CC120 All Sound Off: immediate, and it clicks ---------------------------------------
        $display("\nCC120 All Sound Off (part 1)");
        note_on(2'd1, 8'd60); note_on(2'd1, 8'd64); note_on(2'd1, 8'd67);
        wait_samples(W);
        expect_loud("three notes are sounding", 1);
        measure(50); pp_play = amax - amin;
        cc(2'd1, 8'd120, 8'd0);
        wait_samples(2);
        expect_quiet("silent within two samples", 1);
        // The filter states are still ringing down for a couple of milliseconds after the
        // envelopes are gone; 100 samples is ~3 ms at 32 kHz, several time constants of the
        // SVF's >>6 leak. After that the mix has to be flat.
        wait_samples(100); measure(50); pp_dead = amax - amin;
        if (pp_dead * 64 < pp_play)
            $display("  ok   the mix stops moving: %0d counts peak-to-peak, was %0d while playing",
                     pp_dead, pp_play);
        else begin
            fails = fails + 1;
            $display("  FAIL mix still moving after All Sound Off: %0d counts peak-to-peak, was %0d",
                     pp_dead, pp_play);
        end

        // --- CC64 is ignored, and that is deliberate ---------------------------------------------
        // M34 built a sustain pedal and then removed it again -- the ECP5 would not route with
        // the per-voice `held` bit it needs. This asserts the absence: a note-off under a
        // depressed pedal releases like any other, it is not deferred. If someone puts the pedal
        // back, this test fails and points at the doc that says why it was taken out.
        $display("\nCC64 is ignored (part 2)");
        cc(2'd2, 8'd64, 8'd127);
        note_on(2'd2, 8'd60);
        wait_samples(W);
        expect_loud("the note sounds", 2);
        note_off(2'd2, 8'd60);
        wait_samples(W);
        expect_quiet("note-off releases it anyway -- no pedal to defer it", 2);

        // --- CC120 reaps a voice that is already releasing, CC123 does not -----------------------
        // The `rel_ok` term. A plain note-off must not re-trigger a release on a voice already in
        // one; All Sound Off must cut it regardless. A slow release (CC23=127, ~2 s) makes the
        // window wide enough to see.
        $display("\nCC120 cuts a voice mid-release (part 3)");
        cc(2'd3, 8'd23, 8'd127);
        note_on(2'd3, 8'd62);
        wait_samples(W);
        note_off(2'd3, 8'd62);
        wait_samples(W);
        expect_loud("still falling through a two-second release", 3);
        cc(2'd3, 8'd120, 8'd0);
        wait_samples(2);
        expect_quiet("All Sound Off takes it anyway", 3);

        $display("\n%0s", fails == 0 ? "PASS: the channel mode messages behave"
                                     : "FAIL: see above");
        $finish;
    end
    initial begin #2000000000; $display("TIMEOUT at sample %0d", nframe); $finish; end
endmodule
