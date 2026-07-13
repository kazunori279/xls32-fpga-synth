module xls_uartp(
  input wire clk,
  input wire rst,
  input wire _tx_rdy,
  output wire _tx,
  output wire _tx_vld
);
  reg [9:0] ____state_1;
  reg [3:0] ____state_2;
  reg ____state_3;
  reg [7:0] ____state_4;
  reg [9:0] ____state_0;
  reg p1_inputs_valid;
  wire eq_497;
  wire eq_502;
  wire nor_505;
  wire and_506;
  wire nor_507;
  wire nand_508;
  wire [1:0] ____state_1__next_value_predicates;
  wire [2:0] ____state_2__next_value_predicates;
  wire [1:0] ____state_0__next_value_predicates;
  wire [2:0] one_hot_519;
  wire [3:0] one_hot_520;
  wire [2:0] one_hot_521;
  wire ____state_1__at_most_one_next_value;
  wire ____state_2__at_most_one_next_value;
  wire ____state_0__at_most_one_next_value;
  wire [1:0] concat_561;
  wire and_635;
  wire and_636;
  wire [2:0] concat_566;
  wire [3:0] bp;
  wire [1:0] concat_573;
  wire [9:0] add_575;
  wire or_581;
  wire or_582;
  wire or_583;
  wire [9:0] one_hot_sel_584;
  wire or_585;
  wire [3:0] one_hot_sel_586;
  wire or_587;
  wire nand_588;
  wire [7:0] add_590;
  wire [9:0] one_hot_sel_592;
  wire or_593;
  wire txbit;
  wire or_596;
  assign eq_497 = ____state_0 == 10'h363;
  assign eq_502 = ____state_2 == 4'h9;
  assign nor_505 = ~(~____state_3 | ~eq_497 | eq_502);
  assign and_506 = ____state_3 & eq_497 & eq_502;
  assign nor_507 = ~(~____state_3 | eq_497);
  assign nand_508 = ~(____state_3 & ~eq_497);
  assign ____state_1__next_value_predicates = {~____state_3, nor_505};
  assign ____state_2__next_value_predicates = {~____state_3, nor_505, and_506};
  assign ____state_0__next_value_predicates = {nor_507, nand_508};
  assign one_hot_519 = {____state_1__next_value_predicates[1:0] == 2'h0, ____state_1__next_value_predicates[1] && !____state_1__next_value_predicates[0], ____state_1__next_value_predicates[0]};
  assign one_hot_520 = {____state_2__next_value_predicates[2:0] == 3'h0, ____state_2__next_value_predicates[2] && ____state_2__next_value_predicates[1:0] == 2'h0, ____state_2__next_value_predicates[1] && !____state_2__next_value_predicates[0], ____state_2__next_value_predicates[0]};
  assign one_hot_521 = {____state_0__next_value_predicates[1:0] == 2'h0, ____state_0__next_value_predicates[1] && !____state_0__next_value_predicates[0], ____state_0__next_value_predicates[0]};
  assign ____state_1__at_most_one_next_value = ~____state_3 == one_hot_519[1] & nor_505 == one_hot_519[0];
  assign ____state_2__at_most_one_next_value = ~____state_3 == one_hot_520[2] & nor_505 == one_hot_520[1] & and_506 == one_hot_520[0];
  assign ____state_0__at_most_one_next_value = nor_507 == one_hot_521[1] & nand_508 == one_hot_521[0];
  assign concat_561 = {~____state_3, nor_505};
  assign and_635 = _tx_rdy & ~____state_3;
  assign and_636 = _tx_rdy & nor_505;
  assign concat_566 = {~____state_3, nor_505, and_506};
  assign bp = ____state_2 + 4'h1;
  assign concat_573 = {nor_507, nand_508};
  assign add_575 = ____state_0 + 10'h001;
  assign or_581 = ~_tx_rdy | ____state_1__at_most_one_next_value | rst;
  assign or_582 = ~_tx_rdy | ____state_2__at_most_one_next_value | rst;
  assign or_583 = ~_tx_rdy | ____state_0__at_most_one_next_value | rst;
  assign one_hot_sel_584 = {1'h0, ____state_1[9:1]} & {10{concat_561[0]}} | {1'h1, ____state_4, 1'h0} & {10{concat_561[1]}};
  assign or_585 = and_635 | and_636;
  assign one_hot_sel_586 = 4'h0 & {4{concat_566[0]}} | bp & {4{concat_566[1]}} | 4'h0 & {4{concat_566[2]}};
  assign or_587 = and_635 | and_636 | _tx_rdy & and_506;
  assign nand_588 = ~(____state_3 & eq_497 & eq_502);
  assign add_590 = ____state_4 + 8'h01;
  assign one_hot_sel_592 = 10'h000 & {10{concat_573[0]}} | add_575 & {10{concat_573[1]}};
  assign or_593 = _tx_rdy & nor_507 | _tx_rdy & nand_508;
  assign txbit = ~(____state_3 & ~____state_1[0]);
  assign or_596 = _tx_rdy | p1_inputs_valid;
  always @ (posedge clk) begin
    if (rst) begin
      ____state_1 <= 10'h000;
      ____state_2 <= 4'h0;
      ____state_3 <= 1'h0;
      ____state_4 <= 8'h00;
      ____state_0 <= 10'h000;
      p1_inputs_valid <= 1'h0;
    end else begin
      ____state_1 <= or_585 ? one_hot_sel_584 : ____state_1;
      ____state_2 <= or_587 ? one_hot_sel_586 : ____state_2;
      ____state_3 <= _tx_rdy ? nand_588 : ____state_3;
      ____state_4 <= and_635 ? add_590 : ____state_4;
      ____state_0 <= or_593 ? one_hot_sel_592 : ____state_0;
      p1_inputs_valid <= or_596 ? _tx_rdy : p1_inputs_valid;
    end
  end
  assign _tx = txbit;
  assign _tx_vld = 1'h1;
endmodule
