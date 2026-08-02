`timescale 1ns/1ps
// Stereo verification: drive a note through reverb, decode the RsTx UART stream, de-interleave
// 4-byte frames (Llo Lhi Rlo Rhi), and check the wet tail is decorrelated (L != R). Then dry
// mode should give L == R (centered). iverilog has no BUFG -> stub.
module BUFG(input I, output O); assign O = I; endmodule

module tb_stereo;
    reg clk = 1'b0, rsrx = 1'b1;
    wire [15:0] led; wire rstx;
    top dut(.clk(clk), .RsRx(rsrx), .led(led), .RsTx(rstx));
    always #5 clk = ~clk;                 // 100 MHz
    localparam integer BAUD = 50;

    task send_byte(input [7:0] b); integer i; begin
        rsrx=0; repeat(BAUD) @(posedge clk);
        for (i=0;i<8;i=i+1) begin rsrx=b[i]; repeat(BAUD) @(posedge clk); end
        rsrx=1; repeat(2*BAUD) @(posedge clk);
    end endtask
    task note_on(input [7:0] n, input [7:0] v); begin send_byte(8'h90); send_byte(n); send_byte(v); end endtask
    task cc(input [7:0] c, input [7:0] v); begin send_byte(8'hB0); send_byte(c); send_byte(v); end endtask

    // --- RsTx UART receiver -> de-interleaved L/R ---
    integer bcnt=0; reg [7:0] fr[0:3];
    integer diff=0, same=0, frames=0; reg checking=0;
    task rx_byte(output [7:0] b); integer i; begin
        @(negedge rstx);                                  // start bit
        repeat(BAUD + BAUD/2) @(posedge clk);             // to center of bit0
        for (i=0;i<8;i=i+1) begin b[i]=rstx; repeat(BAUD) @(posedge clk); end
    end endtask
    reg [7:0] rb; reg signed [15:0] L, R;
    initial begin : rxproc
        forever begin
            rx_byte(rb); fr[bcnt%4]=rb; bcnt=bcnt+1;
            if (bcnt%4==0) begin
                L = {fr[1],fr[0]}; R = {fr[3],fr[2]};
                if (checking) begin
                    frames=frames+1;
                    if (L!==R) diff=diff+1; else same=same+1;
                    if (frames<=6) $display("  frame %0d: L=%0d R=%0d %s", frames,
                                            $signed(L)-16'sd32768, $signed(R)-16'sd32768,
                                            (L!==R)?"(differ)":"(same)");
                end
            end
        end
    end

    initial begin
        repeat(40000) @(posedge clk);                     // reset + BRAM clear
        $display("=== REVERB (CC83=4, cathedral): expect wet tail L != R ===");
        cc(8'd83, 8'd127); cc(8'd91, 8'd127);             // reverb, cathedral
        cc(8'd20, 8'd60); cc(8'd22, 8'd110); cc(8'd23, 8'd30);
        note_on(8'd60, 8'd110);
        repeat(3200000) @(posedge clk);                   // run past the ~810-sample comb wrap
        checking=1; frames=0; diff=0; same=0;
        repeat(600000) @(posedge clk);
        checking=0;
        $display("  reverb frames=%0d differ=%0d same=%0d", frames, diff, same);

        $display("=== DRY (CC83=0): expect L == R (centered) ===");
        cc(8'd83, 8'd0);
        note_on(8'd64, 8'd110);
        repeat(80000) @(posedge clk);
        checking=1; frames=0; diff=0; same=0;
        repeat(120000) @(posedge clk);
        checking=0;
        $display("  dry frames=%0d differ=%0d same=%0d", frames, diff, same);
        $display("=== done ===");
        $finish;
    end
    initial begin #400000000; $display("TIMEOUT"); $finish; end
endmodule
