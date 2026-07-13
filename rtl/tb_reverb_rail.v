`timescale 1ns/1ps
// Reproduce the reverb RAILING on the actual RTL. Plays a note through reverb and reports the
// peak |sampL/R - center| during the note and in the tail. Rail => peak approaches 32768.
// Compile with -DSIMFAST so the comb/allpass buffers wrap in a few hundred samples.
module BUFG(input I, output O); assign O = I; endmodule

module tb_reverb_rail;
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

    integer peakL=0, peakR=0; reg watching=0; integer eL, eR;
    integer pk_cbn=0, pk_nlp=0, pk_acc=0, pk_csr=0, pk_apy=0, pk_drd=0, pk_fbm=0, pk_raw=0, t;
    function integer iabs(input integer x); begin iabs = (x<0)?-x:x; end endfunction
    always @(posedge clk) if (watching) begin
        eL = dut.sampL; eL = iabs(eL-32768); if (eL>peakL) peakL=eL;
        eR = dut.sampR; eR = iabs(eR-32768); if (eR>peakR) peakR=eR;
        t=$signed(dut.raws); if (iabs(t)>pk_raw) pk_raw=iabs(t);
        // internal reverb signals (signed) — find which exceeds its legal range
        t=$signed(dut.cbn);  if (iabs(t)>pk_cbn) pk_cbn=iabs(t);
        t=$signed(dut.nlp);  if (iabs(t)>pk_nlp) pk_nlp=iabs(t);
        t=$signed(dut.fbm);  if (iabs(t)>pk_fbm) pk_fbm=iabs(t);
        t=$signed(dut.accL); if (iabs(t)>pk_acc) pk_acc=iabs(t);
        t=$signed(dut.csrL); if (iabs(t)>pk_csr) pk_csr=iabs(t);
        t=$signed(dut.apy0L);if (iabs(t)>pk_apy) pk_apy=iabs(t);
        t=$signed(dut.drdL); if (iabs(t)>pk_drd) pk_drd=iabs(t);
    end

    task test_room(input [7:0] roomval, input [127:0] label); begin
        cc(8'd83, 8'd64);            // fx mode = 64>>4 = 4 = reverb
        cc(8'd91, roomval);          // room size (val>>5)
        cc(8'd74, 8'd127); cc(8'd71, 8'd30); cc(8'd22, 8'd127); cc(8'd26, 8'd127);
        cc(8'd23, 8'd20); cc(8'd27, 8'd20);
        peakL=0; peakR=0; pk_cbn=0; pk_nlp=0; pk_acc=0; pk_csr=0; pk_apy=0; pk_drd=0; pk_fbm=0;
        watching=1;
        non(8'd60);
        repeat(2500000) @(posedge clk); // sustain
        $display("%0s during: out=%0d raw=%0d | cbn=%0d nlp=%0d fbm=%0d acc=%0d csr=%0d apy=%0d drd=%0d",
                 label, peakL, pk_raw, pk_cbn, pk_nlp, pk_fbm, pk_acc, pk_csr, pk_apy, pk_drd);
        peakL=0; peakR=0; pk_cbn=0; pk_nlp=0; pk_acc=0; pk_csr=0; pk_apy=0; pk_drd=0; pk_fbm=0;
        noff(8'd60);
        repeat(2500000) @(posedge clk); // tail
        $display("%0s tail:   out=%0d raw=%0d | cbn=%0d nlp=%0d fbm=%0d acc=%0d csr=%0d apy=%0d drd=%0d",
                 label, peakL, pk_raw, pk_cbn, pk_nlp, pk_fbm, pk_acc, pk_csr, pk_apy, pk_drd);
        watching=0;
        // let it settle before next room
        cc(8'd83, 8'd0); repeat(1500000) @(posedge clk);
    end endtask

    initial begin
        repeat(40000) @(posedge clk);
        test_room(8'd0,   "ROOM0");
        test_room(8'd127, "ROOM3");
        $display("=== done ===");
        $finish;
    end
    initial begin #900000000; $display("TIMEOUT"); $finish; end
endmodule
