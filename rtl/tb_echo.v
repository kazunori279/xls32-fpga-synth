`timescale 1ns/1ps
// Focused ECHO test on the current top.v: burst then silence, fx=echo, short delay time.
// Watches the delayed tap read (drdL/echodL) + output tail -> if the tap goes non-zero after
// the burst, the dmem read/write works (logic OK); if it stays 0, the echo logic is broken.
module BUFG(input I, output O); assign O = I; endmodule
module xls_engine(input clk, input rst, input ce,
    input [7:0] _midi_in, input _midi_in_vld, output _midi_in_rdy,
    output [15:0] _audio_out, output _audio_out_vld, input _audio_out_rdy,
    output [31:0] _viz_out, output _viz_out_vld, input _viz_out_rdy);
    assign _midi_in_rdy = 1'b1; assign _audio_out_vld = 1'b1;
    assign _viz_out = 32'd0; assign _viz_out_vld = 1'b0;
    reg gate = 1'b0;
    assign _audio_out = gate ? 16'd52000 : 16'd32768;   // gate on = loud DC; off = silence(center)
endmodule
module tb_echo;
    reg clk=1'b0, rsrx=1'b1; wire [15:0] led; wire rstx;
    top dut(.clk(clk), .RsRx(rsrx), .led(led), .RsTx(rstx),
            .midi_din(1'b1), .i2s_bclk(), .i2s_ws(), .i2s_sd());
    always #5 clk = ~clk;
    localparam integer BAUD=50;
    task send_byte(input [7:0] b); integer i; begin
        rsrx=0; repeat(BAUD) @(posedge clk);
        for (i=0;i<8;i=i+1) begin rsrx=b[i]; repeat(BAUD) @(posedge clk); end
        rsrx=1; repeat(2*BAUD) @(posedge clk);
    end endtask
    task cc(input [7:0] c,input [7:0] v); begin send_byte(8'hB0);send_byte(c);send_byte(v); end endtask
    integer mx_drd=0, mx_echo=0, mx_out=0, t; reg watch=0;
    function integer iabs(input integer x); begin iabs=(x<0)?-x:x; end endfunction
    always @(posedge clk) if (watch) begin
        t=$signed(dut.drdL);  if(iabs(t)>mx_drd)  mx_drd=iabs(t);
        t=$signed(dut.echodL);if(iabs(t)>mx_echo) mx_echo=iabs(t);
        t=dut.sampL; t=iabs(t-32768); if(t>mx_out) mx_out=t;
    end
    initial begin
        repeat(40000) @(posedge clk);      // let power-up clearing finish
        cc(8'd83, 8'd32);                  // fx = echo (mode 2)
        cc(8'd95, 8'd127);                 // echo depth max
        cc(8'd82, 8'd4);                   // delay time -> edly = (4<<7)|128 = 640 samples
        dut.eng.gate = 1'b1; repeat(300000) @(posedge clk);   // burst (~96 samples @32k)
        dut.eng.gate = 1'b0;                                   // silence
        watch=1; repeat(2000000) @(posedge clk);              // watch ~640 samples for the echo repeat
        $display("ECHO sim: max drdL=%0d  echodL=%0d  out=%0d  (nonzero drdL/echodL => dmem read works)",
                 mx_drd, mx_echo, mx_out);
        $finish;
    end
    initial begin #300000000; $display("TIMEOUT"); $finish; end
endmodule
