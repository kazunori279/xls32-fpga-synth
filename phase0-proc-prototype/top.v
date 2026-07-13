`default_nettype none
module top (input wire clk, output wire RsTx, output wire [15:0] led);
    wire clk100; BUFG bg (.I(clk), .O(clk100));
    reg [3:0] rc = 4'd0;
    wire rst = (rc != 4'hf);
    always @(posedge clk100) if (rc != 4'hf) rc <= rc + 4'd1;
    wire txbit, txvld;
    xls_uartp u (.clk(clk100), .rst(rst), ._tx_rdy(1'b1), ._tx(txbit), ._tx_vld(txvld));
    reg tx = 1'b1;
    always @(posedge clk100) tx <= txbit;
    assign RsTx = tx;
    assign led = {14'd0, txvld, rst};
endmodule
`default_nettype wire
