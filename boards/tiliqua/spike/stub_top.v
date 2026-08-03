// M21 feasibility spike: the XLS engine alone on an ECP5, with no Tiliqua in the way.
//
// The only job here is to make `xls_engine` measurable — how many TRELLIS_COMB / TRELLIS_FF /
// DP16KD / MULT18X18D it costs on an LFE5U-25F, and how fast it closes. So this top is
// deliberately not a synth: no audio interface, no MIDI decode, no effects, no PSRAM.
//
// The one thing it must get right is *not lying*. Tie an input to a constant and yosys will
// happily constant-fold half the datapath away, and the spike would report a number for a
// design that does not exist. So every engine input is driven from a real pin (through a shift
// register, so the 8-bit MIDI byte cannot be folded to a constant either), and every engine
// output is XOR-reduced into a real pin so nothing downstream is dead. The reduction tree costs
// a few dozen LUTs against an engine in the thousands.
`default_nettype none

module stub_top (
    input  wire clk,      // free-running; the sweep constrains this net
    input  wire rst_n,
    input  wire din,      // serialised stimulus -> MIDI byte + valid
    output wire dout      // XOR of everything the engine produces
);
    // Active-high reset for the XLS proc, plus a few cycles of power-on.
    reg [4:0] rc = 5'd0;
    wire rst = (rc != 5'h1f) | ~rst_n;
    always @(posedge clk) if (rc != 5'h1f) rc <= rc + 5'd1;

    // The engine advances one voice per enabled cycle, 32 voices per sample. On Basys 3 that is
    // a /3 enable off 100 MHz; the exact ratio does not change the critical path, only how often
    // it is exercised, so the spike keeps a divider here purely so `ce` is a real high-fanout
    // net rather than a constant 1.
    reg [5:0] cediv = 6'd0;
    wire ce = (cediv == 6'd0);
    always @(posedge clk) cediv <= (cediv == 6'd38) ? 6'd0 : cediv + 6'd1;

    // Stimulus: shift the pin into the MIDI byte. Unpredictable to yosys, so the byte stays live.
    reg [7:0] mdata = 8'd0;
    reg       mvld  = 1'b0;
    always @(posedge clk) begin
        mdata <= {mdata[6:0], din};
        mvld  <= din;
    end

    wire        mrdy;
    wire [15:0] audio;
    wire        avld;
    wire [31:0] vdata;
    wire        vvld;

    // Ready strobes come off the divider, not constants -- an always-ready sink lets the tools
    // simplify the channel handshakes that a real board would have to pay for.
    wire ardy = (cediv == 6'd1);
    wire vrdy = (cediv == 6'd2);

    xls_engine eng (
        .clk(clk), .rst(rst), .ce(ce),
        ._midi_in(mdata),  ._midi_in_vld(mvld),  ._midi_in_rdy(mrdy),
        ._audio_out(audio), ._audio_out_vld(avld), ._audio_out_rdy(ardy),
        ._viz_out(vdata),   ._viz_out_vld(vvld),   ._viz_out_rdy(vrdy)
    );

    // Every output bit has to reach the pin or it is dead logic and the count is wrong.
    reg q = 1'b0;
    always @(posedge clk) q <= ^{audio, vdata, mrdy, avld, vvld};
    assign dout = q;
endmodule

`default_nettype wire
