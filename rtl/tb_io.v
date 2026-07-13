`timescale 1ns/1ps
// Functional check for the new DIN-MIDI input + I2S DAC output in top.v (stub engine).
module BUFG(input I, output O); assign O = I; endmodule
module xls_engine(input clk, input rst, input ce,
    input [7:0] _midi_in, input _midi_in_vld, output _midi_in_rdy,
    output [15:0] _audio_out, output _audio_out_vld, input _audio_out_rdy,
    output [31:0] _viz_out, output _viz_out_vld, input _viz_out_rdy);
    assign _midi_in_rdy = 1'b1;
    assign _audio_out = 16'd48000;           // constant loud-ish sample
    assign _audio_out_vld = 1'b1;
    assign _viz_out = 32'd0; assign _viz_out_vld = 1'b0;
endmodule

module tb_io;
    reg clk = 0, rsrx = 1, din = 1;
    wire [15:0] led; wire rstx, bclk, ws, sd;
    top dut(.clk(clk), .RsRx(rsrx), .led(led), .RsTx(rstx),
            .midi_din(din), .i2s_bclk(bclk), .i2s_ws(ws), .i2s_sd(sd));
    always #5 clk = ~clk;
    localparam integer MB = 3200;            // 31250-baud bit period in 100MHz cycles

    task midibyte(input [7:0] b); integer i; begin
        din = 0; repeat(MB) @(posedge clk);                       // start bit
        for (i = 0; i < 8; i = i + 1) begin din = b[i]; repeat(MB) @(posedge clk); end
        din = 1; repeat(MB) @(posedge clk);                       // stop bit
    end endtask

    integer got = 0; reg [7:0] lastbyte = 0;
    always @(posedge clk) if (dut.mvld && dut.mrdy && dut.ce) begin got = got + 1; lastbyte = dut.mdata; end

    integer wsedges = 0, bclkedges = 0; reg wsp = 0, bp = 0;
    always @(posedge clk) begin
        if (ws   !== wsp) wsedges   = wsedges + 1;   wsp <= ws;
        if (bclk !== bp)  bclkedges = bclkedges + 1; bp  <= bclk;
    end

    initial begin
        repeat(4000) @(posedge clk);        // let power-on reset clear
        midibyte(8'h91); midibyte(8'h3C); midibyte(8'h64);   // note-on ch2, C4, vel100
        repeat(3000) @(posedge clk);
        $display("DIN MIDI: bytes forwarded to engine = %0d, last = %02x (expect >=3, last 64)", got, lastbyte);
        $display("I2S: WS edges = %0d, BCLK edges = %0d (both should be > 0)", wsedges, bclkedges);
        $finish;
    end
    initial begin #6000000; $display("TIMEOUT"); $finish; end
endmodule
