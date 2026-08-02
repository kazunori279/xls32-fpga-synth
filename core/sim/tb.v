`timescale 1ns/1ps
module tb;
    localparam integer NSAMP = 3000;
    reg clk=1'b0, rst=1'b1; reg [1:0] cec=2'd0; wire ce = (cec==2'd0);
    reg [7:0] midi_data=8'd0; reg midi_vld=1'b0; wire midi_rdy;
    wire [15:0] audio; wire audio_vld; reg audio_rdy=1'b0;
    wire [31:0] viz; wire viz_vld;
    xls_engine dut(.clk(clk),.rst(rst),.ce(ce),._midi_in(midi_data),._midi_in_vld(midi_vld),
                   ._audio_out_rdy(audio_rdy),._midi_in_rdy(midi_rdy),
                   ._audio_out(audio),._audio_out_vld(audio_vld),
                   ._viz_out(viz),._viz_out_vld(viz_vld),._viz_out_rdy(1'b1));
    always #5 clk=~clk;
    always @(posedge clk) cec <= (cec==2'd2)?2'd0:cec+2'd1;   // divide-by-3: engine every 3rd clk
    initial begin repeat(20) @(posedge clk); rst<=1'b0; end

    task send_midi(input [7:0] b);
        begin
            @(negedge clk); midi_data<=b; midi_vld<=1'b1;
            @(posedge clk); while(!(midi_rdy && ce)) @(posedge clk);  // xfer only on ce cycle
            @(negedge clk); midi_vld<=1'b0;
            repeat(3) @(posedge clk);
        end
    endtask

    initial begin
        @(negedge rst); repeat(5) @(posedge clk);
        send_midi(8'h90);
        send_midi(8'd69); send_midi(8'd100);
        send_midi(8'd73); send_midi(8'd100);
        send_midi(8'd76); send_midi(8'd100);
        send_midi(8'd80); send_midi(8'd100);
    end

    // one capture per transfer: re-arm rdy only after vld drops
    integer got=0; reg armed=1'b1;
    always @(posedge clk) begin
        if(rst) begin audio_rdy<=1'b1; armed<=1'b1; end
        else begin
            if(armed && audio_vld && ce) begin   // xfer only on ce cycle
                audio_rdy<=1'b1;
                $display("S %0d", audio);
                got<=got+1; armed<=1'b0;
                if(got==NSAMP) $finish;
            end else begin
                audio_rdy<=armed;                // stay ready until captured
                if(!audio_vld) armed<=1'b1;      // re-arm between rounds
            end
        end
    end
    initial begin #800000000; $display("TIMEOUT got=%0d", got); $finish; end
endmodule
