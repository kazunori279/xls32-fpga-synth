`timescale 1ns/1ps
// M21: how many engine cycles does one audio sample actually cost?
//
// The resource sweep alone cannot pick an operating point, because the sample rate a given
// build can sustain is set by the engine's *achieved* initiation interval -- not by
// --pipeline_stages, and not by the --worst_case_throughput cap, which DEVELOPMENT.md records
// as sitting far above the real II. The only number that decides anything is measured here:
// clock cycles between successive audio_out handshakes, with ce tied high and the sink always
// ready, so nothing outside the engine paces it.
//
//   iverilog -o /tmp/rate build/spike/engine_sNN.v boards/tiliqua/spike/tb_rate.v && vvp /tmp/rate
//
// Required engine clock for a target rate is then simply cycles_per_sample * SR.
module tb_rate;
    reg clk = 0, rst = 1;
    always #5 clk = ~clk;

    reg  [7:0] mdata = 8'd0;
    reg        mvld  = 1'b0;
    wire       mrdy;
    wire [15:0] audio;
    wire        avld;
    wire [31:0] vdata;
    wire        vvld;

    xls_engine eng (
        .clk(clk), .rst(rst), .ce(1'b1),
        ._midi_in(mdata),  ._midi_in_vld(mvld),  ._midi_in_rdy(mrdy),
        ._audio_out(audio), ._audio_out_vld(avld), ._audio_out_rdy(1'b1),
        ._viz_out(vdata),   ._viz_out_vld(vvld),   ._viz_out_rdy(1'b1)
    );

    // Hand the engine one MIDI byte per accepted handshake. A silent engine could in principle
    // schedule differently from a sounding one, so the measurement is taken with notes held.
    reg [7:0] msg [0:8];
    integer   mi = 0;
    initial begin
        msg[0] = 8'h90; msg[1] = 8'h3C; msg[2] = 8'h64;   // note-on  C4
        msg[3] = 8'h90; msg[4] = 8'h40; msg[5] = 8'h64;   // note-on  E4
        msg[6] = 8'h90; msg[7] = 8'h43; msg[8] = 8'h64;   // note-on  G4
    end

    integer cyc = 0;
    always @(posedge clk) cyc = cyc + 1;

    always @(posedge clk) if (!rst) begin
        if (mvld && mrdy) begin
            mvld <= 1'b0;
            mi   <= mi + 1;
        end else if (!mvld && mi < 9) begin
            mdata <= msg[mi];
            mvld  <= 1'b1;
        end
    end

    // Ignore the first few samples: the pipeline fills and the note-ons are still arriving,
    // so early gaps describe start-up, not steady state.
    localparam integer WARMUP = 8;
    localparam integer WANT   = 24;

    integer n = 0, prev = 0, gap = 0;
    integer gmin = 1000000, gmax = 0, gsum = 0, ngap = 0;

    always @(posedge clk) if (!rst && avld) begin
        n = n + 1;
        if (n > 1) begin
            gap = cyc - prev;
            if (n > WARMUP) begin
                if (gap < gmin) gmin = gap;
                if (gap > gmax) gmax = gap;
                gsum = gsum + gap;
                ngap = ngap + 1;
            end
        end
        prev = cyc;
        if (ngap >= WANT) begin
            $display("CYCLES_PER_SAMPLE min=%0d max=%0d mean=%0d  (n=%0d gaps)",
                     gmin, gmax, gsum / ngap, ngap);
            $display("REQUIRED_MHZ 32k=%0d.%03d 48k=%0d.%03d",
                     (gmax * 32000) / 1000000, ((gmax * 32000) / 1000) % 1000,
                     (gmax * 48000) / 1000000, ((gmax * 48000) / 1000) % 1000);
            $finish;
        end
    end

    initial begin
        repeat(40) @(posedge clk);
        rst = 0;
    end
    initial begin
        #4000000;
        $display("TIMEOUT after %0d cycles, %0d samples seen, %0d gaps measured", cyc, n, ngap);
        $finish;
    end
endmodule
