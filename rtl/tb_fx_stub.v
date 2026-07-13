`timescale 1ns/1ps
// Isolate the EFFECTS: stub xls_engine with a loud sustained saw so the reverb combs get a
// real, harmonically-rich excitation, then cut to silence and watch the tail. Verifies the
// full 8-comb Freeverb SERIAL SEND (echo/chorus -> reverb): output + internal reverb peaks
// stay BOUNDED (no 32767 rail) and the tail DECAYS. Compile with -DSIMFAST for short combs.
module BUFG(input I, output O); assign O = I; endmodule

// --- stub engine: ignores MIDI, always-valid loud saw; `gate` (poked by TB) forces silence ---
module xls_engine(input clk, input rst, input ce,
    input [7:0] _midi_in, input _midi_in_vld, output _midi_in_rdy,
    output [15:0] _audio_out, output _audio_out_vld, input _audio_out_rdy,
    output [31:0] _viz_out, output _viz_out_vld, input _viz_out_rdy);
    assign _midi_in_rdy = 1'b1;
    assign _audio_out_vld = 1'b1;
    assign _viz_out = 32'd0; assign _viz_out_vld = 1'b0;
    reg [15:0] ph = 16'd0;
    reg gate = 1'b1;
    always @(posedge clk) if (ce && _audio_out_rdy) ph <= ph + 16'd2200;   // saw ramp per sample
    wire signed [15:0] s = $signed(ph) - ($signed(ph) >>> 2);              // ~0.75 full-scale
    assign _audio_out = gate ? (16'sd32768 + s) : 16'd32768;              // gated off -> exact center
endmodule

module tb_fx_stub;
    reg clk=1'b0, rsrx=1'b1; wire [15:0] led; wire rstx;
    top dut(.clk(clk), .RsRx(rsrx), .led(led), .RsTx(rstx),
            .midi_din(1'b1), .i2s_bclk(), .i2s_ws(), .i2s_sd());   // midi_din idle-high
    always #5 clk = ~clk;
    localparam integer BAUD=50;
    task send_byte(input [7:0] b); integer i; begin
        rsrx=0; repeat(BAUD) @(posedge clk);
        for (i=0;i<8;i=i+1) begin rsrx=b[i]; repeat(BAUD) @(posedge clk); end
        rsrx=1; repeat(2*BAUD) @(posedge clk);
    end endtask
    task cc(input [7:0] c,input [7:0] v); begin send_byte(8'hB0);send_byte(c);send_byte(v); end endtask

    integer peakO=0, tailO=0, pk_cbn=0, pk_nlp=0, pk_acc=0, pk_csr=0, pk_apy=0, pk_rw=0, pk_fbm=0, t;
    reg watching=0, tailph=0;
    function integer iabs(input integer x); begin iabs=(x<0)?-x:x; end endfunction
    always @(posedge clk) if (watching) begin
        t=dut.sampL; t=iabs(t-32768); if (t>peakO) peakO=t; if (tailph && t>tailO) tailO=t;
        t=$signed(dut.cbn);  if (iabs(t)>pk_cbn) pk_cbn=iabs(t);
        t=$signed(dut.nlp);  if (iabs(t)>pk_nlp) pk_nlp=iabs(t);
        t=$signed(dut.fbm);  if (iabs(t)>pk_fbm) pk_fbm=iabs(t);
        t=$signed(dut.accL); if (iabs(t)>pk_acc) pk_acc=iabs(t);
        t=$signed(dut.csrL); if (iabs(t)>pk_csr) pk_csr=iabs(t);
        t=$signed(dut.apyL); if (iabs(t)>pk_apy) pk_apy=iabs(t);
        t=$signed(dut.revwetL); if (iabs(t)>pk_rw) pk_rw=iabs(t);
    end

    // roomval -> CC91 (rsize=[6:5]); drive echo (CC83=mode2) + full reverb wet (CC93=127)
    task run(input [7:0] roomval, input [127:0] label); begin
        cc(8'd83, 8'd48);            // fx mode = both (chorus+echo)
        cc(8'd94, 8'd127);           // chorus depth MAX
        cc(8'd95, 8'd127);           // delay depth MAX
        cc(8'd82, 8'd40);            // delay time
        cc(8'd91, roomval);          // reverb size
        cc(8'd93, 8'd127);           // reverb wet full (worst-case: all effects max)
        peakO=0;pk_cbn=0;pk_nlp=0;pk_acc=0;pk_csr=0;pk_apy=0;pk_rw=0;pk_fbm=0; tailO=0; tailph=0; watching=1;
        dut.eng.gate = 1'b1;
        repeat(1500000) @(posedge clk);   // ~420 samples of sustained saw (build the tail)
        dut.eng.gate = 1'b0; tailph=1;    // cut to silence -> watch the tail decay
        repeat(1500000) @(posedge clk);
        $display("%0s: out=%0d tail=%0d | cbn=%0d nlp=%0d fbm=%0d acc=%0d csr=%0d apy=%0d revwet=%0d (rail=32767)",
                 label, peakO, tailO, pk_cbn, pk_nlp, pk_fbm, pk_acc, pk_csr, pk_apy, pk_rw);
        watching=0; cc(8'd93,8'd0); repeat(300000) @(posedge clk);
    end endtask

    initial begin
        repeat(40000) @(posedge clk);
        run(8'd0,   "ROOM0");
        run(8'd127, "ROOM3");
        $display("=== done (bounded if all peaks < 32767; tail < out shows decay) ===");
        $finish;
    end
    initial begin #400000000; $display("TIMEOUT"); $finish; end
endmodule
