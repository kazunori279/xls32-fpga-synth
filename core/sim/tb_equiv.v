`timescale 1ns/1ps
// Bit-exactness harness for DSLX changes that are supposed to change nothing.
//
//   iverilog -g2005 -o /tmp/eq engine.v core/sim/tb_equiv.v
//   vvp /tmp/eq +out=golden.hex
//
// Drives the engine's midi_in channel directly (no UART), then dumps SAMPLES audio words.
// Two engines built from two revisions of synth.x must produce byte-identical files.
//
// MIDI is paced to sample boundaries — one byte presented after each audio pull — rather than
// streamed as fast as the engine accepts it. A datapath edit can shift XLS's pipeline schedule,
// and a free-running feed would then land a note-on on a different slot of the 32-voice ring,
// producing a diff that is a scheduling artefact rather than an arithmetic one.
module tb_equiv;
    parameter integer SAMPLES = 3000;
    parameter integer SETTLE  = 64;     // samples between the last MIDI byte and the first capture

    reg clk = 0, rst = 1;
    always #5 clk = ~clk;

    reg  [7:0]  mdata = 8'd0;
    reg         mvld  = 1'b0;
    wire        mrdy;
    wire [15:0] audio;
    wire        avld;
    wire [31:0] vdata;
    wire        vvld;

    xls_engine eng (
        .clk(clk), .rst(rst), .ce(1'b1),
        ._midi_in(mdata),   ._midi_in_vld(mvld),  ._midi_in_rdy(mrdy),
        ._audio_out(audio), ._audio_out_vld(avld), ._audio_out_rdy(1'b1),
        ._viz_out(vdata),   ._viz_out_vld(vvld),   ._viz_out_rdy(1'b1)
    );

    // --- stimulus -------------------------------------------------------------------------
    // Every path the M22 narrowing touches has to be live, or the comparison proves nothing:
    // unison (v.uni != 0), pitch bend (pmod != 0), per-part volume (compv != 254), and a
    // resonant filter driven hard (the SVF high/band state near its clamps). High notes matter
    // most — they are where inc0>>12 and inc>>9 exceed 18 bits.
    localparam integer NMSG = 64;
    reg [7:0] msg [0:NMSG-1];
    integer   mi = 0;
    initial begin
        // part 0 patch
        msg[ 0]=8'hB0; msg[ 1]=8'd80; msg[ 2]=8'd127;   // CC80 unison  = 3 (max stack)
        msg[ 3]=8'hB0; msg[ 4]=8'd1;  msg[ 5]=8'd127;   // CC1  vibrato = 3 (max)
        msg[ 6]=8'hB0; msg[ 7]=8'd76; msg[ 8]=8'd127;   // CC76 LFO rate = max (sweeps pmod fast)
        msg[ 9]=8'hB0; msg[10]=8'd7;  msg[11]=8'd100;   // CC7  volume  -> compv != 254
        msg[12]=8'hB0; msg[13]=8'd74; msg[14]=8'd90;    // CC74 cutoff
        msg[15]=8'hB0; msg[16]=8'd71; msg[17]=8'd110;   // CC71 resonance (drives the SVF state up)
        msg[18]=8'hB0; msg[19]=8'd79; msg[20]=8'd100;   // CC79 filter env depth
        msg[21]=8'hB0; msg[22]=8'd77; msg[23]=8'd90;    // CC77 LFO->cutoff depth
        msg[24]=8'hB0; msg[25]=8'd92; msg[26]=8'd80;    // CC92 tremolo depth -> tg != 64
        msg[27]=8'hB0; msg[28]=8'd85; msg[29]=8'd96;    // CC85 cross-mod mode
        msg[30]=8'hB0; msg[31]=8'd86; msg[32]=8'd100;   // CC86 cross-mod depth
        msg[33]=8'hE0; msg[34]=8'd0;  msg[35]=8'd100;   // bend up -> pmod pinned away from 0
        // part 1 patch: a second timbre so the 4:1 part mux is exercised
        msg[36]=8'hB1; msg[37]=8'd80; msg[38]=8'd64;    // CC80 unison = 2
        msg[39]=8'hB1; msg[40]=8'd74; msg[41]=8'd120;
        msg[42]=8'hE1; msg[43]=8'd0;  msg[44]=8'd40;    // bend down
        // notes. 120 and 108 are the point of the exercise: inc0 there is well past 2^30.
        msg[45]=8'h90; msg[46]=8'd24; msg[47]=8'd100;
        msg[48]=8'h90; msg[49]=8'd60; msg[50]=8'd110;
        msg[51]=8'h90; msg[52]=8'd108; msg[53]=8'd120;
        msg[54]=8'h90; msg[55]=8'd120; msg[56]=8'd127;
        msg[57]=8'h91; msg[58]=8'd36; msg[59]=8'd90;
        msg[60]=8'h91; msg[61]=8'd72; msg[62]=8'd100;
        msg[63]=8'h90; msg[63]=8'd0;                    // unused tail
    end
    localparam integer NBYTES = 63;

    // --- capture --------------------------------------------------------------------------
    integer fd;
    integer nsamp = 0, ncap = 0, done_at = -1;
    reg [1023:0] outfile;

    initial begin
        if (!$value$plusargs("out=%s", outfile)) outfile = "equiv.hex";
        fd = $fopen(outfile, "w");
        if (fd == 0) begin $display("cannot open output"); $finish; end
        repeat(40) @(posedge clk);
        rst = 0;
    end

    always @(posedge clk) if (!rst) begin
        // retire an accepted byte
        if (mvld && mrdy) begin
            mvld <= 1'b0;
            mi   <= mi + 1;
            if (mi + 1 == NBYTES) done_at <= nsamp;
        end

        if (avld) begin
            nsamp <= nsamp + 1;
            // one byte per sample, presented on the pull so it always lands at the same
            // ring position regardless of how XLS scheduled the pipeline
            if (!mvld && mi < NBYTES) begin
                mdata <= msg[mi];
                mvld  <= 1'b1;
            end
            if (done_at >= 0 && nsamp >= done_at + SETTLE) begin
                $fwrite(fd, "%04x\n", audio);
                ncap <= ncap + 1;
                if (ncap + 1 >= SAMPLES) begin
                    $fclose(fd);
                    $display("captured %0d samples to %0s (first pull at sample %0d)",
                             SAMPLES, outfile, done_at + SETTLE);
                    $finish;
                end
            end
        end
    end

    initial begin
        #400000000;
        $display("TIMEOUT: %0d bytes in, %0d samples seen, %0d captured", mi, nsamp, ncap);
        $fclose(fd);
        $finish;
    end
endmodule
