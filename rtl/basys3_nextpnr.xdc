## Basys 3 constraints for the openXC7 (yosys synth_xilinx + nextpnr-xilinx) backend.
## nextpnr-xilinx wants `set_property LOC`/`IOSTANDARD` (not the Vivado `-dict { PACKAGE_PIN }`
## form used by rtl/basys3.xdc for the F4PGA/VPR flow). Keep the two in sync.

## 100 MHz clock (W5)
set_property LOC W5 [get_ports clk]
set_property IOSTANDARD LVCMOS33 [get_ports clk]
create_clock -period 10.000 -name sys_clk [get_ports clk]

## USB-UART bridge (FT2232 channel B)
set_property LOC A18 [get_ports RsTx]
set_property IOSTANDARD LVCMOS33 [get_ports RsTx]
set_property LOC B18 [get_ports RsRx]
set_property IOSTANDARD LVCMOS33 [get_ports RsRx]

## LEDs LD0..LD15
set_property LOC U16 [get_ports {led[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[0]}]
set_property LOC E19 [get_ports {led[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[1]}]
set_property LOC U19 [get_ports {led[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[2]}]
set_property LOC V19 [get_ports {led[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[3]}]
set_property LOC W18 [get_ports {led[4]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[4]}]
set_property LOC U15 [get_ports {led[5]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[5]}]
set_property LOC U14 [get_ports {led[6]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[6]}]
set_property LOC V14 [get_ports {led[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[7]}]
set_property LOC V13 [get_ports {led[8]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[8]}]
set_property LOC V3 [get_ports {led[9]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[9]}]
set_property LOC W3 [get_ports {led[10]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[10]}]
set_property LOC U3 [get_ports {led[11]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[11]}]
set_property LOC P3 [get_ports {led[12]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[12]}]
set_property LOC N3 [get_ports {led[13]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[13]}]
set_property LOC P1 [get_ports {led[14]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[14]}]
set_property LOC L1 [get_ports {led[15]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[15]}]

## Pmod JA1: DIN MIDI input (31250 baud)
set_property LOC J1 [get_ports midi_din]
set_property IOSTANDARD LVCMOS33 [get_ports midi_din]

## Pmod JB1-3: I2S out to the UDA1334A DAC
set_property LOC A14 [get_ports i2s_bclk]
set_property IOSTANDARD LVCMOS33 [get_ports i2s_bclk]
set_property LOC A16 [get_ports i2s_ws]
set_property IOSTANDARD LVCMOS33 [get_ports i2s_ws]
set_property LOC B15 [get_ports i2s_sd]
set_property IOSTANDARD LVCMOS33 [get_ports i2s_sd]
