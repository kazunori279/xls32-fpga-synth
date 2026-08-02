## Basys 3 constraints for the XLS UART-blinky.

## 100 MHz clock
set_property -dict { PACKAGE_PIN W5  IOSTANDARD LVCMOS33 } [get_ports clk]
create_clock -add -name sys_clk_pin -period 10.00 -waveform {0 5} [get_ports clk]

## USB-UART bridge: RsTx = FPGA transmits to host (FT2232 channel B)
set_property -dict { PACKAGE_PIN A18 IOSTANDARD LVCMOS33 } [get_ports RsTx]
set_property -dict { PACKAGE_PIN B18 IOSTANDARD LVCMOS33 } [get_ports RsRx]

## LEDs LD0..LD15
set_property -dict { PACKAGE_PIN U16 IOSTANDARD LVCMOS33 } [get_ports {led[0]}]
set_property -dict { PACKAGE_PIN E19 IOSTANDARD LVCMOS33 } [get_ports {led[1]}]
set_property -dict { PACKAGE_PIN U19 IOSTANDARD LVCMOS33 } [get_ports {led[2]}]
set_property -dict { PACKAGE_PIN V19 IOSTANDARD LVCMOS33 } [get_ports {led[3]}]
set_property -dict { PACKAGE_PIN W18 IOSTANDARD LVCMOS33 } [get_ports {led[4]}]
set_property -dict { PACKAGE_PIN U15 IOSTANDARD LVCMOS33 } [get_ports {led[5]}]
set_property -dict { PACKAGE_PIN U14 IOSTANDARD LVCMOS33 } [get_ports {led[6]}]
set_property -dict { PACKAGE_PIN V14 IOSTANDARD LVCMOS33 } [get_ports {led[7]}]
set_property -dict { PACKAGE_PIN V13 IOSTANDARD LVCMOS33 } [get_ports {led[8]}]
set_property -dict { PACKAGE_PIN V3  IOSTANDARD LVCMOS33 } [get_ports {led[9]}]
set_property -dict { PACKAGE_PIN W3  IOSTANDARD LVCMOS33 } [get_ports {led[10]}]
set_property -dict { PACKAGE_PIN U3  IOSTANDARD LVCMOS33 } [get_ports {led[11]}]
set_property -dict { PACKAGE_PIN P3  IOSTANDARD LVCMOS33 } [get_ports {led[12]}]
set_property -dict { PACKAGE_PIN N3  IOSTANDARD LVCMOS33 } [get_ports {led[13]}]
set_property -dict { PACKAGE_PIN P1  IOSTANDARD LVCMOS33 } [get_ports {led[14]}]
set_property -dict { PACKAGE_PIN L1  IOSTANDARD LVCMOS33 } [get_ports {led[15]}]

## Pmod JA: DIN MIDI input (31250 baud) from the MIDI-UART opto breakout (set breakout to 3.3V)
##   JA1 = data (breakout serial out) ; power the breakout from JA VCC (pin 6) + GND (pin 5)
set_property -dict { PACKAGE_PIN J1  IOSTANDARD LVCMOS33 } [get_ports midi_din]

## Pmod JB: I2S out to the UDA1334A DAC (power it from JB VCC pin 6 + GND pin 5)
##   JB1 = BCLK, JB2 = LRCLK/WSEL, JB3 = DIN(serial data)
set_property -dict { PACKAGE_PIN A14 IOSTANDARD LVCMOS33 } [get_ports i2s_bclk]
set_property -dict { PACKAGE_PIN A16 IOSTANDARD LVCMOS33 } [get_ports i2s_ws]
set_property -dict { PACKAGE_PIN B15 IOSTANDARD LVCMOS33 } [get_ports i2s_sd]
