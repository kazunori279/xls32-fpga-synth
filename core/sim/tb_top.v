`timescale 1ns/1ps
// End-to-end sim of the real shell (top.v) + engine (engine.v): bit-bang MIDI note-ons
// over RsRx at 2 Mbaud and watch the LED "comet" (cursor advance + per-LED brightness).
// iverilog has no BUFG primitive, so provide a pass-through stub.
module BUFG(input I, output O); assign O = I; endmodule

module tb_top;
    reg clk = 1'b0;
    reg rsrx = 1'b1;              // UART idle high
    wire [15:0] led;
    wire rstx;

    top dut(.clk(clk), .RsRx(rsrx), .led(led), .RsTx(rstx));

    always #5 clk = ~clk;        // 100 MHz

    localparam integer BAUD = 50;    // matches top.v (2 Mbaud @ 100 MHz)

    // send one UART byte LSB-first: start(0), 8 data bits, stop(1)
    task send_byte(input [7:0] b);
        integer i;
        begin
            rsrx = 1'b0; repeat (BAUD) @(posedge clk);            // start bit
            for (i = 0; i < 8; i = i + 1) begin
                rsrx = b[i]; repeat (BAUD) @(posedge clk);        // data bits, LSB first
            end
            rsrx = 1'b1; repeat (BAUD) @(posedge clk);            // stop bit
            repeat (BAUD) @(posedge clk);                         // idle gap
        end
    endtask

    // note-on: 0x90, note, velocity
    task note_on(input [7:0] n, input [7:0] v);
        begin send_byte(8'h90); send_byte(n); send_byte(v); end
    endtask
    task note_off(input [7:0] n);
        begin send_byte(8'h80); send_byte(n); send_byte(8'd0); end
    endtask

    // watch cursor / brightness changes via hierarchical refs
    integer k, j;
    reg [3:0] pcur = 4'hf;
    always @(posedge clk) begin
        if (dut.cursor !== pcur) begin
            pcur <= dut.cursor;
            $display("[%0t] cursor=%0d sidx=%0d  led=%b", $time, dut.cursor, dut.sidx, led);
        end
    end
    // periodic brightness dump
    task dump_bright;
        begin
            $write("[%0t] bright:", $time);
            for (k = 0; k < 16; k = k + 1) $write(" %3d", dut.ledbright[k]);
            $write("\n");
        end
    endtask

    initial begin
        // let power-on reset + BRAM clear finish (clearing sweeps 16384 slots)
        repeat (40000) @(posedge clk);
        $display("=== reset/clear done, sending single note A4(69) ===");
        note_on(8'd69, 8'd100);
        for (j = 0; j < 4; j = j + 1) begin repeat (300000) @(posedge clk); dump_bright; end
        $display("=== chord: +72, +76 (expect cursor to jump) ===");
        note_on(8'd72, 8'd100);
        note_on(8'd76, 8'd100);
        repeat (30000) @(posedge clk); dump_bright;
        $display("=== note-off 69 (its LED should fade) ===");
        note_off(8'd69);
        repeat (60000) @(posedge clk); dump_bright;
        $display("=== done ===");
        $finish;
    end
    initial begin #200000000; $display("TIMEOUT"); $finish; end
endmodule
