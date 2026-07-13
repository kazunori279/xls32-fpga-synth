`timescale 1ns/1ps
// Fast decorrelation probe (compile with -DSIMFAST for shrunk comb delays). Plays a SHORT note
// through cathedral reverb, then checks the tail (after note-off, dry gone) directly on the
// internal sampL/sampR, masking bit0 (the L/R channel marker). Expect L != R in the wet tail.
module BUFG(input I, output O); assign O = I; endmodule

module tb_stereo2;
    reg clk=1'b0, rsrx=1'b1; wire [15:0] led; wire rstx;
    top dut(.clk(clk), .RsRx(rsrx), .led(led), .RsTx(rstx));
    always #5 clk = ~clk;
    localparam integer BAUD=50;
    task send_byte(input [7:0] b); integer i; begin
        rsrx=0; repeat(BAUD) @(posedge clk);
        for (i=0;i<8;i=i+1) begin rsrx=b[i]; repeat(BAUD) @(posedge clk); end
        rsrx=1; repeat(2*BAUD) @(posedge clk);
    end endtask
    task cc(input [7:0] c,input [7:0] v); begin send_byte(8'hB0);send_byte(c);send_byte(v); end endtask
    task non(input [7:0] n); begin send_byte(8'h90);send_byte(n);send_byte(8'd110); end endtask
    task noff(input [7:0] n); begin send_byte(8'h80);send_byte(n);send_byte(8'd0); end endtask

    integer diff=0, same=0, cnt=0; reg checking=0; reg [15:0] tick=0;
    // sample the internal L/R every ~4000 clocks while checking; mask bit0 (channel marker)
    always @(posedge clk) begin
        tick <= tick + 1;
        if (checking && tick==0) begin
            cnt=cnt+1;
            if ((dut.sampL & 16'hFFFE) !== (dut.sampR & 16'hFFFE)) diff=diff+1; else same=same+1;
            if (cnt<=6) $display("  L=%0d R=%0d", $signed(dut.sampL)-16'sd32768, $signed(dut.sampR)-16'sd32768);
        end
    end

    initial begin
        repeat(40000) @(posedge clk);
        cc(8'd83,8'd64); cc(8'd91,8'd127); cc(8'd23,8'd110);   // reverb (mode=val>>4=4), cathedral, fast release
        $display("=== short note through reverb; check TAIL after note-off ===");
        non(8'd60);
        repeat(200000) @(posedge clk);        // dry builds + combs wrap (SIMFAST ~64 samples)
        noff(8'd60);
        repeat(150000) @(posedge clk);        // dry releases; wet tail continues
        checking=1;
        repeat(300000) @(posedge clk);
        checking=0;
        $display("  reverb tail: samples=%0d differ=%0d same=%0d", cnt, diff, same);
        $display("=== done ===");
        $finish;
    end
    initial begin #200000000; $display("TIMEOUT"); $finish; end
endmodule
