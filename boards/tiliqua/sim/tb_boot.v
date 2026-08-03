`timescale 1ns/1ps
// Reference run for the M23 pitch check: the bare engine, driven by the same MIDI the Tiliqua
// gateware feeds it, with nothing between it and the capture.
//
//   iverilog -g2005 -o ref build/tiliqua/engine.v boards/tiliqua/sim/tb_boot.v
//   vvp ref +out=build/tiliqua/ref32.txt +n=12000
//
// The point is to have the engine's own waveform, at the engine's own sample rate, to compare
// the Tiliqua capture against. That makes the check about the audio path -- CDC, resampler,
// codec -- rather than about whether the synth is in tune, which is a separate open question
// (see the pitch_a4 test failure).
//
// The MIDI feed is free-running here, matching the gateware's boot ROM, which hands the engine
// a byte whenever `_midi_in_rdy` is high. That differs from core/sim/tb_equiv.v, which paces
// MIDI to sample boundaries -- that harness compares two engines byte-for-byte and needs the
// note-on to land on the same slot of the voice ring; this one only measures a frequency.
module tb_boot;
    reg clk = 0, rst = 1;
    always #5 clk = ~clk;

    // The first 36 bytes must match BOOT_MIDI in boards/tiliqua/gateware/xls_core.py; the note-on
    // after them is what the Verilator harness's "pitch" script sends over the MIDI wire, since
    // M24's boot patch is CCs only. Together they are what the engine hears in the gateware run.
    localparam integer NBYTES = 39;
    reg [7:0] msg [0:NBYTES-1];
    integer ch, k;
    initial begin
        k = 0;
        for (ch = 0; ch < 4; ch = ch + 1) begin      // broadcast: part = channel nibble[1:0]
            msg[k+0]=8'hB0+ch; msg[k+1]=8'd74; msg[k+2]=8'd100;  // CC74 cutoff
            msg[k+3]=8'hB0+ch; msg[k+4]=8'd71; msg[k+5]=8'd40;   // CC71 resonance
            msg[k+6]=8'hB0+ch; msg[k+7]=8'd7;  msg[k+8]=8'd110;  // CC7  volume
            k = k + 9;
        end
        msg[36]=8'h90; msg[37]=8'd69; msg[38]=8'd100;            // note on, A4, channel 1
    end

    reg  [7:0]  mdata = 8'd0;
    reg         mvld  = 1'b0;
    wire        mrdy;
    wire [15:0] audio;
    wire        avld;
    wire [31:0] vdata;
    wire        vvld;
    integer     mi = 0;

    xls_engine eng (
        .clk(clk), .rst(rst), .ce(1'b1),
        ._midi_in(mdata),   ._midi_in_vld(mvld),   ._midi_in_rdy(mrdy),
        ._audio_out(audio), ._audio_out_vld(avld), ._audio_out_rdy(1'b1),
        ._viz_out(vdata),   ._viz_out_vld(vvld),   ._viz_out_rdy(1'b1)
    );

    integer fd, nsamp = 0, nwant = 12000;
    reg [1023:0] outfile;

    initial begin
        if (!$value$plusargs("out=%s", outfile)) outfile = "ref32.txt";
        if (!$value$plusargs("n=%d", nwant)) nwant = 12000;
        fd = $fopen(outfile, "w");
        if (fd == 0) begin $display("cannot open output"); $finish; end
        repeat(40) @(posedge clk);
        rst = 0;
    end

    always @(posedge clk) if (!rst) begin
        // free-running MIDI feed
        if (mvld && mrdy) begin
            mvld <= 1'b0;
            mi   <= mi + 1;
        end else if (!mvld && mi < NBYTES) begin
            mdata <= msg[mi];
            mvld  <= 1'b1;
        end

        if (avld) begin
            // Offset binary out of scale_mix; the gateware inverts the MSB to get signed. Do
            // the same here so both files hold the same quantity.
            $fwrite(fd, "%0d\n", $signed({~audio[15], audio[14:0]}));
            nsamp <= nsamp + 1;
            if (nsamp + 1 >= nwant) begin
                $fclose(fd);
                $display("captured %0d engine samples to %0s", nwant, outfile);
                $finish;
            end
        end
    end

    initial begin
        #2000000000;
        $display("TIMEOUT: %0d bytes in, %0d samples", mi, nsamp);
        $fclose(fd);
        $finish;
    end
endmodule
