// Basys 3 top for the M6 pipelined voice engine (xls_engine proc).
// The shell does UART at 1 Mbaud and bridges to the proc's ready/valid channels:
//   RsRx -> UART RX -> midi_in channel ;  audio_out channel -> UART TX -> RsTx
// Audio is paced at 32 kHz by asserting _audio_out_rdy on the sample tick (the
// proc blocks on its send between rounds). Full 100 MHz.
`default_nettype none

module top (
    input  wire        clk,       // 100 MHz, W5
    input  wire        RsRx,      // USB-UART RX (MIDI in from web bridge), B18
    output wire [15:0] led,
    output wire        RsTx,      // USB-UART TX (audio out), A18
    input  wire        midi_din,  // Pmod JA1 (J1): 31250-baud DIN MIDI in (opto breakout) -> low latency
    output wire        i2s_bclk,  // Pmod JB1 (A14): I2S bit clock       -> UDA1334A DAC
    output wire        i2s_ws,    // Pmod JB2 (A16): I2S word/LR clock    -> UDA1334A DAC
    output wire        i2s_sd     // Pmod JB3 (B15): I2S serial data      -> UDA1334A DAC
);
    wire clk100; BUFG bg (.I(clk), .O(clk100));

    // power-on reset, active-high (XLS proc reset)
    reg [4:0] rc = 5'd0;
    wire rst = (rc != 5'h1f);
    always @(posedge clk100) if (rc != 5'h1f) rc <= rc + 5'd1;

    reg [1:0] rxs = 2'b11;
    always @(posedge clk100) rxs <= {rxs[0], RsRx};
    wire rx = rxs[1];

    // DIN MIDI input (Pmod JA1) is asynchronous -> 2-FF synchronizer; idles high
    reg [1:0] mrxs = 2'b11;
    always @(posedge clk100) mrxs <= {mrxs[0], midi_din};
    wire mrx = mrxs[1];

    localparam integer BAUD    = 50;     // 100 MHz / 2 Mbaud (2 bytes/sample TX must fit the
                                         // 3125-clock sample budget in real time -> 32 kHz)
    localparam integer MBAUD   = 3200;   // 100 MHz / 31250 baud = standard DIN MIDI bit period
    localparam integer SAMPDIV = 3125;   // 100 MHz / 32 kHz (the ÷3 DSP pipeline sustains this
                                         // in real time; BASE_INC rescaled to 32 kHz in synth.x)

    // Engine/effect clock-enable: F4PGA floors this design at ~20ns and can't make a
    // sub-100MHz clock (no MMCM), so it advances every 4th cycle (ce) -> every register
    // path gets 40ns. (Was /3; the reverb feedback multiply needs ~34ns, so /4.) The
    // UART and sample tick stay at full 100MHz; the ready/valid handshakes are level-
    // based (completed only on ce cycles) so the shell interacts correctly.
    //
    // The multitimbral (4-part) engine congests the fabric enough that the shared reverb's
    // feedback-multiply path stretches past 40ns -> echo/reverb corrupt at /4. The effects
    // FSM uses only ~17 states of the ~3500 clocks per sample, so it advances on a SLOWER
    // /8 enable (ce8, 80ns/step): the reverb multiply gets 80ns and closes with margin
    // while the engine stays at /4. ce8 is a strict subset of ce (same phase) so the
    // /4 sample-consume handshake and the /8 FSM never collide on `dst`.
    // With DSP48 (Vivado backend) the engine critical path is ~19.5ns. Running /2 (20ns) was
    // too tight (stress patches latched the SVF), so it advances every 3rd cycle (ce, /3 ->
    // 30ns budget, ~10ns margin) -- still fast enough to sustain a true 32kHz real-time stream
    // (vs /4 which capped at 28kHz). Effects FSM runs /6 (ce8, 60ns; its reverb mult is a DSP).
    // ce8 is a strict subset of ce (same phase) so the sample-consume and FSM never collide.
    reg [2:0] cec = 3'd0;
    always @(posedge clk100) cec <= rst ? 3'd0 : (cec == 3'd5 ? 3'd0 : cec + 3'd1);   // mod-6
    wire ce  = (cec == 3'd0) || (cec == 3'd3);   // 1 of every 3 cycles (engine + sample, 30ns)
    wire ce8 = (cec == 3'd0);                     // 1 of every 6 cycles (effects FSM, 60ns/step)

    // ---- engine proc ----
    reg  [7:0]  mdata = 8'd0; reg mvld = 1'b0; wire mrdy;
    wire [15:0] audio; wire avld; reg ardy = 1'b0;
    wire [31:0] vdata; wire vvld;                 // LED-comet tap: {env[15:0], is_new@16, last@17}
    xls_engine eng (.clk(clk100), .rst(rst), .ce(ce),
        ._midi_in(mdata), ._midi_in_vld(mvld), ._midi_in_rdy(mrdy),
        ._audio_out(audio), ._audio_out_vld(avld), ._audio_out_rdy(ardy),
        ._viz_out(vdata), ._viz_out_vld(vvld), ._viz_out_rdy(1'b1));   // always ready -> never stalls

    // ---- UART RX -> midi_in ----  two sources merge into the engine's MIDI stream:
    //   (a) FT2232 @2 Mbaud (web bridge / host)   (b) DIN MIDI @31250 baud (Pmod JA1, opto breakout)
    reg rxa = 1'b0; reg [15:0] rxd = 0; reg [3:0] rxb = 0; reg [7:0] rxsh = 0;
    reg [7:0] rxbyte = 0; reg rxhave = 1'b0;
    reg mra = 1'b0; reg [12:0] mrd = 0; reg [3:0] mrb = 0; reg [7:0] mrsh = 0;   // DIN MIDI RX FSM
    reg [7:0] dinbyte = 0; reg dinhave = 1'b0;
    // effect-control CC sniffer (parallel to forwarding): CC83 -> fxmode (0..3)
    reg [7:0] ectrl = 0; reg [1:0] ecnt = 0; reg [2:0] fxmode = 3'd0;   // 0dry 1chorus 2echo 3both
    reg [1:0] rsize = 2'd3;                                             // reverb room size (CC91), default cathedral
    reg [6:0] revwet = 7'd0;                                            // reverb send/wet level (CC93), default off
    reg [6:0] chdep  = 7'd64;                                           // chorus depth/wet (CC94), default 0.5
    reg [6:0] echodep= 7'd64;                                           // echo/delay depth/wet (CC95), default 0.5
    reg [6:0] dtime  = 7'd63;                                           // delay TIME (CC82) -> ~252 ms default
    reg [6:0] dbg    = 7'd0;                                            // DEBUG probe (CC90): 0 normal, else stream internals
    always @(posedge clk100) begin
        if (rst) begin rxa <= 0; rxhave <= 0; mra <= 0; dinhave <= 0; mvld <= 0; end
        else begin
            // --- (a) FT2232 UART RX @2 Mbaud (also sniffs CC83/91 for the shell effects) ---
            if (!rxa) begin
                if (rx == 1'b0) begin rxa <= 1; rxd <= BAUD + BAUD/2 - 1; rxb <= 0; end
            end else if (rxd == 0) begin
                if (rxb == 4'd8) begin
                    rxa <= 0; rxbyte <= rxsh; rxhave <= 1;
                    // sniff CC messages (mirror the engine's parser) for effect control
                    if (rxsh >= 8'h80) ecnt <= ((rxsh & 8'hF0) == 8'hB0) ? 2'd1 : 2'd0;  // status
                    else if (ecnt == 2'd1) begin ectrl <= rxsh; ecnt <= 2'd2; end          // controller
                    else if (ecnt == 2'd2) begin
                        if (ectrl == 8'd83) fxmode <= rxsh[6:4];                            // CC83 -> mode 0..3
                        if (ectrl == 8'd91) rsize  <= rxsh[6:5];                            // CC91 -> room size 0..3
                        if (ectrl == 8'd93) revwet <= rxsh[6:0];                            // CC93 -> reverb wet 0..127
                        if (ectrl == 8'd94) chdep  <= rxsh[6:0];                            // CC94 -> chorus depth 0..127
                        if (ectrl == 8'd95) echodep<= rxsh[6:0];                            // CC95 -> echo/delay depth 0..127
                        if (ectrl == 8'd82) dtime  <= rxsh[6:0];                            // CC82 -> delay time 0..127
                        if (ectrl == 8'd90) dbg    <= rxsh[6:0];                            // CC90 -> DEBUG probe select
                        ecnt <= 2'd1;                                                       // running status
                    end
                end
                else begin rxsh <= {rx, rxsh[7:1]}; rxd <= BAUD - 1; rxb <= rxb + 1; end
            end else rxd <= rxd - 1;
            // --- (b) DIN MIDI UART RX @31250 baud (Pmod JA1) ---
            if (!mra) begin
                if (mrx == 1'b0) begin mra <= 1; mrd <= MBAUD + MBAUD/2 - 1; mrb <= 0; end
            end else if (mrd == 0) begin
                if (mrb == 4'd8) begin mra <= 0; dinbyte <= mrsh; dinhave <= 1; end
                else begin mrsh <= {mrx, mrsh[7:1]}; mrd <= MBAUD - 1; mrb <= mrb + 1; end
            end else mrd <= mrd - 1;
            // --- merge both sources into midi_in (hold vld until accepted on a ce cycle) ---
            if (!mvld) begin
                if (rxhave)       begin mdata <= rxbyte;  mvld <= 1; rxhave  <= 0; end   // web bridge first
                else if (dinhave) begin mdata <= dinbyte; mvld <= 1; dinhave <= 0; end   // then DIN keyboard
            end else if (mvld && mrdy && ce) mvld <= 0;   // xfer only when engine advances
        end
    end

    // ---- LED comet: cursor advances per new voice; brightness = live ADSR envelope ----
    // The engine streams one voice's envelope per engine cycle (viz_out). is_new (bit 16)
    // is a one-shot when a freshly allocated voice first reaches slot 0 -> advance the head
    // LED and bind this voice's scan-slot to it. Every cycle we refresh the bound LED's
    // brightness from the live envelope, so the comet trails and fades as notes release.
    // sidx (the voice-in-scan index) self-resyncs on the last bit (bit 17) each 32-voice scan.
    reg [4:0] sidx = 5'd0;         // which voice-in-scan this viz tuple is (0..31)
    reg [3:0] cursor = 4'd0;       // current comet head LED (0..15)
    reg [3:0] bindled  [0:31];     // scan-slot -> LED it lit
    reg [7:0] ledbright [0:15];    // per-LED PWM duty = envelope[15:8]
    integer ii;
    initial begin
        for (ii = 0; ii < 32; ii = ii + 1) bindled[ii]  = 4'd0;
        for (ii = 0; ii < 16; ii = ii + 1) ledbright[ii] = 8'd0;
    end
    always @(posedge clk100) if (vvld && ce) begin
        if (vdata[16]) begin                             // is_new: freshly allocated voice
            cursor                   <= cursor + 4'd1;   // advance the head
            bindled[sidx]            <= cursor + 4'd1;   // bind this scan-slot to the new head LED
            ledbright[cursor + 4'd1] <= vdata[15:8];     // light it at the current envelope
        end else begin
            ledbright[bindled[sidx]] <= vdata[15:8];     // track the bound LED's brightness
        end
        sidx <= vdata[17] ? 5'd0 : sidx + 5'd1;          // resync scan index on the last voice
    end

    // 8-bit free-running PWM (~390 kHz refresh, flicker-free); per-LED compare = brightness.
    reg [7:0] pwm = 8'd0;
    always @(posedge clk100) pwm <= pwm + 8'd1;

    // ---- 32 kHz sample tick ----
    reg [15:0] sdiv = 0; wire stick = (sdiv == SAMPDIV - 1);
    always @(posedge clk100) sdiv <= rst ? 16'd0 : (stick ? 16'd0 : sdiv + 1);

    // ---- STEREO effects: chorus + echo + reverb via per-channel block-RAM delay lines ----
    // The voice engine is mono; the effects create the stereo image. Two 16K x 16-bit
    // circular buffers (dmemL/dmemR), each sync read+write -> 8x RAMB36E1. The mono dry sits
    // centered (identical L/R); only the WET is decorrelated: reverb uses the Freeverb stereo
    // spread (R delay lengths = L + SPREAD), echo ping-pongs L<->R, and the chorus L/R LFO
    // taps run in anti-phase. One arithmetic datapath (a single multiply) is time-shared
    // L then R by the FSM. Modes (CC83): 0 dry, 1 chorus, 2 echo, 3 both, 4 reverb.
    // echo/delay TIME is now a knob (CC82 -> dtime): edly = dtime*128 samples, ~4..508 ms @ 32 kHz
    // (dtime=63 default ~252 ms). The dmem history buffer (16K) holds the longest tap.
    wire [13:0] edly = {dtime, 7'd0} | 14'd128;     // dtime<<7, floored ~4 ms so the tap never == waddr
    // --- REVERB (serial send): full Freeverb, 8 combs + 4 all-pass per channel, in its own tank
    //     dmem2 (echo/chorus stay in dmem). Per-channel layout: 8 comb regions then 4 all-pass. ---
    localparam [13:0] RB0=14'd0,     RB1=14'd1300,  RB2=14'd2600,  RB3=14'd3900,
                      RB4=14'd5200,  RB5=14'd6500,  RB6=14'd7800,  RB7=14'd9100,
                      RA0=14'd10400, RA1=14'd11000, RA2=14'd11600, RA3=14'd12200;
`ifdef SIMFAST
    localparam [10:0] CL0=11'd41,CL1=11'd47,CL2=11'd53,CL3=11'd59,CL4=11'd61,CL5=11'd67,CL6=11'd71,CL7=11'd79;
`else
    localparam [10:0] CL0=11'd810, CL1=11'd878, CL2=11'd940, CL3=11'd1012,     // comb delays @32kHz
                      CL4=11'd1066,CL5=11'd1122,CL6=11'd1176,CL7=11'd1230;      // (coprime-ish)
`endif
    localparam [8:0]  AL0=9'd403, AL1=9'd320, AL2=9'd247, AL3=9'd163;           // 4 all-pass delays
    localparam [10:0] SPREAD = 11'd23;                                          // Freeverb stereo spread
    localparam [10:0] CL0R=CL0+SPREAD,CL1R=CL1+SPREAD,CL2R=CL2+SPREAD,CL3R=CL3+SPREAD,
                      CL4R=CL4+SPREAD,CL5R=CL5+SPREAD,CL6R=CL6+SPREAD,CL7R=CL7+SPREAD;
    localparam [8:0]  AL0R=AL0+9'd23,AL1R=AL1+9'd23,AL2R=AL2+9'd23,AL3R=AL3+9'd23;
    reg  [15:0] dmemL [0:16383];  reg  [15:0] dmemR [0:16383];    // echo + chorus
    reg  [15:0] dmem2L[0:16383];  reg  [15:0] dmem2R[0:16383];    // reverb tank
    reg  [13:0] waddrL=0, raddrL=0, waddrR=0, raddrR=0;
    reg  [15:0] drdL=0, drdR=0;                       // registered read data (BRAM outputs)
    reg         dweL=0, dweR=0; reg [15:0] dwdL=0, dwdR=0;
    // reverb tank ports: one addr pair, L then R selected by `chan`; drd2 muxed like drd
    reg  [13:0] waddr2=0, raddr2=0; reg [15:0] drd2L=0, drd2R=0;
    reg         dwe2L=0, dwe2R=0; reg [15:0] dwd2=0;
    always @(posedge clk100) begin
        drdL <= dmemL[raddrL]; if (dweL) dmemL[waddrL] <= dwdL;   // L: sync read + write
        drdR <= dmemR[raddrR]; if (dweR) dmemR[waddrR] <= dwdR;   // R: sync read + write
        drd2L <= dmem2L[raddr2]; if (dwe2L) dmem2L[waddr2] <= dwd2;   // reverb tank L
        drd2R <= dmem2R[raddr2]; if (dwe2R) dmem2R[waddr2] <= dwd2;   // reverb tank R
    end

    // chorus LFO: triangle sweeping the tap in Q3 (1/8 sample) so the read can be LINEARLY
    // INTERPOLATED -- an integer-only tap jumps a whole sample as it sweeps and each jump is a
    // click => zipper NOISE. L/R anti-phase for width.
    reg  [14:0] clfo = 15'd0;
    wire [10:0] ctriL = clfo[14] ? (11'd2047 - clfo[13:3]) : clfo[13:3];   // 0..2047 (Q3 = 0..255.9 samples)
    wire [10:0] ctriR = 11'd2047 - ctriL;            // anti-phase
    wire [13:0] ctapQL = 14'd2400 + {3'd0, ctriL};   // Q3 tap: (300<<3)+sweep -> 300.0..555.9 samples
    wire [13:0] ctapQR = 14'd2400 + {3'd0, ctriR};
    wire [13:0] ctiL = ctapQL[13:3];  wire [2:0] cfrL = ctapQL[2:0];       // integer tap / fraction 0..7
    wire [13:0] ctiR = ctapQR[13:3];  wire [2:0] cfrR = ctapQR[2:0];
    wire echo_on   = (echodep != 7'd0);   // depth-gated: each effect is on iff its depth knob > 0
    wire chorus_on = (chdep   != 7'd0);   // (no separate mode selector; CC83/fxmode now unused)

    function signed [15:0] sat18(input signed [17:0] x);
        sat18 = (x >  18'sd32767) ?  16'sd32767 :
                (x < -18'sd32768) ? -16'sd32768 : x[15:0];
    endfunction
    // Reverb comb feedback = a real MULTIPLY by a room-size gain g (Q15), rounded.
    // g<1 strictly so DC decays (no drift/railing that killed the shift version) and
    // small samples decay smoothly (no `y - y>>k` limit cycle). One multiply, shared
    // across the 4 combs by the FSM. Room size sets g -> decay time (CC91).
    wire [14:0] rvg = (rsize==2'd0) ? 15'd22000 :   // 0.671  room      (~0.4 s)
                      (rsize==2'd1) ? 15'd26000 :   // 0.793  hall      (~0.8 s)
                      (rsize==2'd2) ? 15'd29000 :   // 0.885  large     (~1.5 s)
                                      15'd31200;    // 0.952  cathedral (~3.5 s)

    // per-channel reverb state (L / R): 8 comb pointers + damping, 4 all-pass pointers each
    reg [10:0] cp0L=0,cp1L=0,cp2L=0,cp3L=0,cp4L=0,cp5L=0,cp6L=0,cp7L=0;
    reg [10:0] cp0R=0,cp1R=0,cp2R=0,cp3R=0,cp4R=0,cp5R=0,cp6R=0,cp7R=0;
    reg [8:0]  ap0pL=0,ap1pL=0,ap2pL=0,ap3pL=0, ap0pR=0,ap1pR=0,ap2pR=0,ap3pR=0;
    reg signed [15:0] dlp0L=0,dlp1L=0,dlp2L=0,dlp3L=0,dlp4L=0,dlp5L=0,dlp6L=0,dlp7L=0;
    reg signed [15:0] dlp0R=0,dlp1R=0,dlp2R=0,dlp3R=0,dlp4R=0,dlp5R=0,dlp6R=0,dlp7R=0;
    reg signed [18:0] accL=0, accR=0;                 // 8-comb running sum -> 19 bits
    reg signed [15:0] csrL=0, csrR=0;                 // comb sum /4 = all-pass chain input
    reg signed [15:0] apyL=0, apyR=0;                 // running all-pass carry (prev stage output)
    reg signed [15:0] revwetL=0, revwetR=0;           // reverb wet per channel (after 4 all-pass)
    reg signed [15:0] ecwL=0, ecwR=0;                 // captured echo/chorus wet = reverb send base
    reg               chan = 1'b0;                     // effect-FSM channel: 0=L, 1=R

    // ---- pull audio, run the effect FSM, UART TX @2 Mbaud (4 bytes/frame: Llo Lhi Rlo Rhi) ----
    reg signed [15:0] raws=0, echodL=0, echodR=0, rin_r=0;
    reg signed [15:0] chs0L=0, chs0R=0;              // chorus interp: nearer tap sample (s0)
    reg [15:0] sampL=0, sampR=0; reg [2:0] pend=0; reg want=1'b0; reg [5:0] dst=6'd0;
    // rin_r = reverb comb input (echo/chorus wet mono-summed, /8 Freeverb gain). The reverb reads
    // its own TANK (dmem2); drd2 = active channel. curdlp selects the current comb's damping reg.
    wire signed [15:0] drd2 = chan ? $signed(drd2R) : $signed(drd2L);
    wire signed [15:0] curdlp =
        (dst==6'd5) ?dlp0L :(dst==6'd6) ?dlp1L :(dst==6'd7) ?dlp2L :(dst==6'd8) ?dlp3L :
        (dst==6'd9) ?dlp4L :(dst==6'd10)?dlp5L :(dst==6'd11)?dlp6L :(dst==6'd12)?dlp7L :
        (dst==6'd17)?dlp0R :(dst==6'd18)?dlp1R :(dst==6'd19)?dlp2R :(dst==6'd20)?dlp3R :
        (dst==6'd21)?dlp4R :(dst==6'd22)?dlp5R :(dst==6'd23)?dlp6R :dlp7R;
    // Damping = 0.5*old + 0.5*new (overflow-safe). fbm = g*y (Q15, DSP mult). cbn = in + fbm.
    wire signed [15:0] nlp = curdlp + ((drd2 - curdlp + 16'sd1) >>> 1);
    wire signed [15:0] fbm = ($signed({1'b0,rvg}) * nlp + 32'sd16384) >>> 15;   // g*y, rounded
    wire signed [15:0] cbn = sat18(rin_r + fbm);
    wire signed [15:0] wetgn = $signed({1'b0, revwet, 8'd0});                   // CC93 wet gain (Q15, 0..~0.99)
    // chorus interp: chint = s0 + (s1-s0)*frac at QUARTER-sample resolution via a shift-mux (no
    // multiply -> off the timing budget; reverb path untouched). s0=nearer tap, s1=drd at dst4.
    wire signed [16:0] cdifL = $signed(drdL) - chs0L;
    wire signed [16:0] cdifR = $signed(drdR) - chs0R;
    wire signed [16:0] cbleL = (cfrL[2:1]==2'd0)?17'sd0 : (cfrL[2:1]==2'd1)?(cdifL>>>2)
                             : (cfrL[2:1]==2'd2)?(cdifL>>>1) : ((cdifL>>>1)+(cdifL>>>2));
    wire signed [16:0] cbleR = (cfrR[2:1]==2'd0)?17'sd0 : (cfrR[2:1]==2'd1)?(cdifR>>>2)
                             : (cfrR[2:1]==2'd2)?(cdifR>>>1) : ((cdifR>>>1)+(cdifR>>>2));
    wire signed [15:0] chintL = chs0L + cbleL;
    wire signed [15:0] chintR = chs0R + cbleR;
    // per-effect depth gains (Q15): CCxx(0..127) << 8 -> 0..~0.99 (default 64 = 0.5 = old fixed level)
    wire signed [15:0] chdep_q15   = $signed({1'b0, chdep,   8'd0});
    wire signed [15:0] echodep_q15 = $signed({1'b0, echodep, 8'd0});
    // echo/chorus wet (signed, pre-reverb) = the reverb SEND base; captured at dst4 into ecwL/R.
    // wet = tap * depth (Q15, DSP mult) -- each effect has its own amount (CC94 chorus, CC95 echo).
    // NB: the product MUST be taken at full 32-bit width before >>>15. Writing (a*b)>>>15 straight
    // into a 16-bit wire makes Verilog evaluate a*b in 16 bits (LRM context width) -> the high half
    // is discarded and >>>15 yields ~0. iverilog happened to extend it (sim OK) but Vivado truncated
    // -> depth/wet silent on hardware. Explicit 32-bit intermediates force the correct width.
    wire signed [31:0] echoWL_p = echodep_q15 * echodL;
    wire signed [31:0] echoWR_p = echodep_q15 * echodR;
    wire signed [31:0] chWL_p   = chdep_q15   * chintL;
    wire signed [31:0] chWR_p   = chdep_q15   * chintR;
    wire signed [15:0] echoWL = echoWL_p >>> 15;
    wire signed [15:0] echoWR = echoWR_p >>> 15;
    wire signed [15:0] chWL   = chWL_p   >>> 15;
    wire signed [15:0] chWR   = chWR_p   >>> 15;
    wire signed [15:0] ecwL_c = sat18(raws + (echo_on?echoWL:16'sd0) + (chorus_on?chWL:16'sd0));
    wire signed [15:0] ecwR_c = sat18(raws + (echo_on?echoWR:16'sd0) + (chorus_on?chWR:16'sd0));
    wire signed [15:0] revwetR_c = sat18(drd2 - (apyR >>> 1));   // R all-pass-3 output (sampled at dst28)
    // reverb wet mix (Q15) at full 32-bit width -- same truncation trap as echoWL (see note above).
    wire signed [31:0] rwetL_p = wetgn * revwetL;
    wire signed [31:0] rwetR_p = wetgn * revwetR_c;
    wire signed [15:0] rwetL = rwetL_p >>> 15;
    wire signed [15:0] rwetR = rwetR_p >>> 15;
    reg txa = 1'b0; reg [15:0] txd = 0; reg [3:0] txb = 0; reg [9:0] frame = 10'h3ff; reg txo = 1'b1;
    reg clearing = 1'b1;                              // zero the BRAM after reset (power-up is garbage)
    always @(posedge clk100) begin
        if (rst) begin ardy<=0; pend<=0; txa<=0; txo<=1; want<=0; dst<=0; dweL<=0; dweR<=0; clfo<=0;
                       dwe2L<=0; dwe2R<=0; chan<=0; clearing<=1; waddrL<=0; waddrR<=0; waddr2<=0;
                       cp0L<=0;cp1L<=0;cp2L<=0;cp3L<=0;cp4L<=0;cp5L<=0;cp6L<=0;cp7L<=0;
                       cp0R<=0;cp1R<=0;cp2R<=0;cp3R<=0;cp4R<=0;cp5R<=0;cp6R<=0;cp7R<=0;
                       ap0pL<=0;ap1pL<=0;ap2pL<=0;ap3pL<=0; ap0pR<=0;ap1pR<=0;ap2pR<=0;ap3pR<=0;
                       dlp0L<=0;dlp1L<=0;dlp2L<=0;dlp3L<=0;dlp4L<=0;dlp5L<=0;dlp6L<=0;dlp7L<=0;
                       dlp0R<=0;dlp1R<=0;dlp2R<=0;dlp3R<=0;dlp4R<=0;dlp5R<=0;dlp6R<=0;dlp7R<=0;
                       accL<=0;accR<=0;csrL<=0;csrR<=0;apyL<=0;apyR<=0;revwetL<=0;revwetR<=0;ecwL<=0;ecwR<=0; end
        else if (clearing) begin
            dweL<=1'b1; dweR<=1'b1; dwdL<=16'd0; dwdR<=16'd0;              // zero echo/chorus buffers
            dwe2L<=1'b1; dwe2R<=1'b1; dwd2<=16'd0; waddr2<=waddr2+14'd1;   // zero the reverb tank too
            waddrL<=waddrL+14'd1; waddrR<=waddrR+14'd1;
            if (waddrL == 14'h3fff) clearing <= 1'b0;                      // done -> waddrs wrap back to 0
        end
        else begin
            dweL<=1'b0; dweR<=1'b0; dwe2L<=1'b0; dwe2R<=1'b0;
            if (stick && pend == 0 && dst == 0) want <= 1;
            if (want && avld && !ardy) ardy <= 1;
            if (ardy && avld && ce) begin
                raws  <= $signed(audio) - 16'sd32768;                  // mono dry (signed)
                clfo  <= clfo + 15'd1; chan <= 1'b0;
                raddrL <= waddrL - edly; raddrR <= waddrR - edly;      // echo/delay taps L+R (always first)
                dst <= 6'd1; ardy <= 0; want <= 0;
            end
            // effect FSM at ce8 rate: echo/chorus (dst1-4) THEN the reverb SEND (dst5-28) run
            // sequentially each sample -> reverb is layered on the echo/chorus wet. Thousands of
            // spare clocks/sample, so serializing L then R is far inside one sample period.
            if (ce8) begin
            // --- echo/chorus (modes 0-3), STEREO ping-pong; produces ecwL/R = reverb send base ---
            if (dst == 6'd1) dst <= 6'd2;                 // wait echo read (both buffers)
            else if (dst == 6'd2) begin
                echodL <= $signed(drdL); echodR <= $signed(drdR);
                raddrL <= waddrL - ctiL; raddrR <= waddrR - ctiR;     // chorus s0 (integer tap)
                dst    <= 6'd3;
            end
            else if (dst == 6'd3) begin
                chs0L <= $signed(drdL); chs0R <= $signed(drdR);       // capture s0, read s1 (adjacent)
                raddrL <= waddrL - ctiL - 14'd1; raddrR <= waddrR - ctiR - 14'd1;
                dst    <= 6'd4;
            end
            else if (dst == 6'd4) begin
                // ping-pong echo write; chorus wet uses the interpolated tap (drdL here = s1)
                dwdL <= sat18(raws + (echo_on ? (echodR >>> 1) : 16'sd0)); dweL <= 1'b1;
                dwdR <= sat18(raws + (echo_on ? (echodL >>> 1) : 16'sd0)); dweR <= 1'b1;
                waddrL <= waddrL + 14'd1; waddrR <= waddrR + 14'd1;
                ecwL  <= ecwL_c; ecwR <= ecwR_c;                          // capture echo/chorus wet
                if (dbg != 7'd0) begin                                    // DEBUG probe (CC90): bypass mix
                    // dbg1: L=echo BRAM tap read, R=dry input (~write) -> is the echo tank round-tripping?
                    // dbg2: L=write ptr, R=read ptr (as ramps) -> are the addresses advancing?
                    sampL <= (dbg == 7'd2) ? $signed({2'b0, waddrL}) : (echodL + 16'sd32768);
                    sampR <= (dbg == 7'd2) ? $signed({2'b0, raddrL}) : (raws   + 16'sd32768);
                    pend  <= 3'd4; dst <= 6'd0;
                end else begin
                sampL <= ecwL_c + 16'sd32768; sampR <= ecwR_c + 16'sd32768;   // output when reverb is OFF
                if (revwet != 7'd0) begin                                 // -> reverb send: L comb0
                    rin_r <= (ecwL_c + ecwR_c) >>> 6;                     // reverb send: low, so combs don't saturate
                    chan  <= 1'b0; raddr2 <= RB0 + {3'd0, cp0L}; dst <= 6'd5;
                end else begin pend <= 3'd4; dst <= 6'd0; end             // dry/echo/chorus only
                end
            end
            // --- reverb SEND: L combs (5-12), L all-pass (13-16), R combs (17-24), R all-pass (25-28)
            //     drd2 = tank read set the prior state; write commits one clock later. ---
            else if (dst==6'd5)  begin dwd2<=cbn; waddr2<=RB0+{3'd0,cp0L}; dwe2L<=1; dlp0L<=nlp; accL<=cbn;        raddr2<=RB1+{3'd0,cp1L}; dst<=6'd6;  end
            else if (dst==6'd6)  begin dwd2<=cbn; waddr2<=RB1+{3'd0,cp1L}; dwe2L<=1; dlp1L<=nlp; accL<=accL+cbn;   raddr2<=RB2+{3'd0,cp2L}; dst<=6'd7;  end
            else if (dst==6'd7)  begin dwd2<=cbn; waddr2<=RB2+{3'd0,cp2L}; dwe2L<=1; dlp2L<=nlp; accL<=accL+cbn;   raddr2<=RB3+{3'd0,cp3L}; dst<=6'd8;  end
            else if (dst==6'd8)  begin dwd2<=cbn; waddr2<=RB3+{3'd0,cp3L}; dwe2L<=1; dlp3L<=nlp; accL<=accL+cbn;   raddr2<=RB4+{3'd0,cp4L}; dst<=6'd9;  end
            else if (dst==6'd9)  begin dwd2<=cbn; waddr2<=RB4+{3'd0,cp4L}; dwe2L<=1; dlp4L<=nlp; accL<=accL+cbn;   raddr2<=RB5+{3'd0,cp5L}; dst<=6'd10; end
            else if (dst==6'd10) begin dwd2<=cbn; waddr2<=RB5+{3'd0,cp5L}; dwe2L<=1; dlp5L<=nlp; accL<=accL+cbn;   raddr2<=RB6+{3'd0,cp6L}; dst<=6'd11; end
            else if (dst==6'd11) begin dwd2<=cbn; waddr2<=RB6+{3'd0,cp6L}; dwe2L<=1; dlp6L<=nlp; accL<=accL+cbn;   raddr2<=RB7+{3'd0,cp7L}; dst<=6'd12; end
            else if (dst==6'd12) begin dwd2<=cbn; waddr2<=RB7+{3'd0,cp7L}; dwe2L<=1; dlp7L<=nlp; csrL<=sat18((accL+cbn)>>>2); raddr2<=RA0+{5'd0,ap0pL}; dst<=6'd13; end
            else if (dst==6'd13) begin apyL<=sat18(drd2-(csrL>>>1)); dwd2<=sat18(csrL+(drd2>>>1)); waddr2<=RA0+{5'd0,ap0pL}; dwe2L<=1; raddr2<=RA1+{5'd0,ap1pL}; dst<=6'd14; end
            else if (dst==6'd14) begin apyL<=sat18(drd2-(apyL>>>1)); dwd2<=sat18(apyL+(drd2>>>1)); waddr2<=RA1+{5'd0,ap1pL}; dwe2L<=1; raddr2<=RA2+{5'd0,ap2pL}; dst<=6'd15; end
            else if (dst==6'd15) begin apyL<=sat18(drd2-(apyL>>>1)); dwd2<=sat18(apyL+(drd2>>>1)); waddr2<=RA2+{5'd0,ap2pL}; dwe2L<=1; raddr2<=RA3+{5'd0,ap3pL}; dst<=6'd16; end
            else if (dst==6'd16) begin revwetL<=sat18(drd2-(apyL>>>1)); dwd2<=sat18(apyL+(drd2>>>1)); waddr2<=RA3+{5'd0,ap3pL}; dwe2L<=1;
                                       chan<=1'b1; raddr2<=RB0+{3'd0,cp0R}; dst<=6'd17; end      // switch to R
            else if (dst==6'd17) begin dwd2<=cbn; waddr2<=RB0+{3'd0,cp0R}; dwe2R<=1; dlp0R<=nlp; accR<=cbn;        raddr2<=RB1+{3'd0,cp1R}; dst<=6'd18; end
            else if (dst==6'd18) begin dwd2<=cbn; waddr2<=RB1+{3'd0,cp1R}; dwe2R<=1; dlp1R<=nlp; accR<=accR+cbn;   raddr2<=RB2+{3'd0,cp2R}; dst<=6'd19; end
            else if (dst==6'd19) begin dwd2<=cbn; waddr2<=RB2+{3'd0,cp2R}; dwe2R<=1; dlp2R<=nlp; accR<=accR+cbn;   raddr2<=RB3+{3'd0,cp3R}; dst<=6'd20; end
            else if (dst==6'd20) begin dwd2<=cbn; waddr2<=RB3+{3'd0,cp3R}; dwe2R<=1; dlp3R<=nlp; accR<=accR+cbn;   raddr2<=RB4+{3'd0,cp4R}; dst<=6'd21; end
            else if (dst==6'd21) begin dwd2<=cbn; waddr2<=RB4+{3'd0,cp4R}; dwe2R<=1; dlp4R<=nlp; accR<=accR+cbn;   raddr2<=RB5+{3'd0,cp5R}; dst<=6'd22; end
            else if (dst==6'd22) begin dwd2<=cbn; waddr2<=RB5+{3'd0,cp5R}; dwe2R<=1; dlp5R<=nlp; accR<=accR+cbn;   raddr2<=RB6+{3'd0,cp6R}; dst<=6'd23; end
            else if (dst==6'd23) begin dwd2<=cbn; waddr2<=RB6+{3'd0,cp6R}; dwe2R<=1; dlp6R<=nlp; accR<=accR+cbn;   raddr2<=RB7+{3'd0,cp7R}; dst<=6'd24; end
            else if (dst==6'd24) begin dwd2<=cbn; waddr2<=RB7+{3'd0,cp7R}; dwe2R<=1; dlp7R<=nlp; csrR<=sat18((accR+cbn)>>>2); raddr2<=RA0+{5'd0,ap0pR}; dst<=6'd25; end
            else if (dst==6'd25) begin apyR<=sat18(drd2-(csrR>>>1)); dwd2<=sat18(csrR+(drd2>>>1)); waddr2<=RA0+{5'd0,ap0pR}; dwe2R<=1; raddr2<=RA1+{5'd0,ap1pR}; dst<=6'd26; end
            else if (dst==6'd26) begin apyR<=sat18(drd2-(apyR>>>1)); dwd2<=sat18(apyR+(drd2>>>1)); waddr2<=RA1+{5'd0,ap1pR}; dwe2R<=1; raddr2<=RA2+{5'd0,ap2pR}; dst<=6'd27; end
            else if (dst==6'd27) begin apyR<=sat18(drd2-(apyR>>>1)); dwd2<=sat18(apyR+(drd2>>>1)); waddr2<=RA2+{5'd0,ap2pR}; dwe2R<=1; raddr2<=RA3+{5'd0,ap3pR}; dst<=6'd28; end
            else if (dst==6'd28) begin dwd2<=sat18(apyR+(drd2>>>1)); waddr2<=RA3+{5'd0,ap3pR}; dwe2R<=1;
                                       // final: echo/chorus wet + reverb wet (CC93, Q15); +offset for output
                                       sampL <= sat18(ecwL + rwetL) + 16'sd32768;
                                       sampR <= sat18(ecwR + rwetR) + 16'sd32768;
                                       // advance all comb + all-pass pointers once per sample
                                       cp0L<=(cp0L==CL0-1)?11'd0:cp0L+1; cp1L<=(cp1L==CL1-1)?11'd0:cp1L+1;
                                       cp2L<=(cp2L==CL2-1)?11'd0:cp2L+1; cp3L<=(cp3L==CL3-1)?11'd0:cp3L+1;
                                       cp4L<=(cp4L==CL4-1)?11'd0:cp4L+1; cp5L<=(cp5L==CL5-1)?11'd0:cp5L+1;
                                       cp6L<=(cp6L==CL6-1)?11'd0:cp6L+1; cp7L<=(cp7L==CL7-1)?11'd0:cp7L+1;
                                       cp0R<=(cp0R==CL0R-1)?11'd0:cp0R+1; cp1R<=(cp1R==CL1R-1)?11'd0:cp1R+1;
                                       cp2R<=(cp2R==CL2R-1)?11'd0:cp2R+1; cp3R<=(cp3R==CL3R-1)?11'd0:cp3R+1;
                                       cp4R<=(cp4R==CL4R-1)?11'd0:cp4R+1; cp5R<=(cp5R==CL5R-1)?11'd0:cp5R+1;
                                       cp6R<=(cp6R==CL6R-1)?11'd0:cp6R+1; cp7R<=(cp7R==CL7R-1)?11'd0:cp7R+1;
                                       ap0pL<=(ap0pL==AL0-1)?9'd0:ap0pL+1; ap1pL<=(ap1pL==AL1-1)?9'd0:ap1pL+1;
                                       ap2pL<=(ap2pL==AL2-1)?9'd0:ap2pL+1; ap3pL<=(ap3pL==AL3-1)?9'd0:ap3pL+1;
                                       ap0pR<=(ap0pR==AL0R-1)?9'd0:ap0pR+1; ap1pR<=(ap1pR==AL1R-1)?9'd0:ap1pR+1;
                                       ap2pR<=(ap2pR==AL2R-1)?9'd0:ap2pR+1; ap3pR<=(ap3pR==AL3R-1)?9'd0:ap3pR+1;
                                       pend<=3'd4; dst<=6'd0; end
            end  // if (ce8) -- effect FSM
            if (txa) begin
                if (txd == 0) begin
                    if (txb == 4'd9) txa <= 0;
                    else begin frame <= frame >> 1; txb <= txb + 1; txd <= BAUD - 1; end
                end else txd <= txd - 1;
            end else if (pend != 0) begin
                // 1-bit channel marker in the low byte's LSB (L=0, R=1) lets the host lock
                // both byte-alignment AND L/R order unambiguously; inaudible at 16-bit.
                frame <= {1'b1, (pend==3'd4 ? {sampL[7:1],1'b0} : pend==3'd3 ? sampL[15:8]
                               : pend==3'd2 ? {sampR[7:1],1'b1} : sampR[15:8]), 1'b0};
                txd <= BAUD - 1; txb <= 0; txa <= 1; pend <= pend - 3'd1;
            end
            txo <= txa ? frame[0] : 1'b1;
        end
    end

    assign RsTx = txo;
    // per-LED PWM: on while the free-running counter is below that LED's brightness
    assign led[0]  = (pwm < ledbright[0]);   assign led[1]  = (pwm < ledbright[1]);
    assign led[2]  = (pwm < ledbright[2]);   assign led[3]  = (pwm < ledbright[3]);
    assign led[4]  = (pwm < ledbright[4]);   assign led[5]  = (pwm < ledbright[5]);
    assign led[6]  = (pwm < ledbright[6]);   assign led[7]  = (pwm < ledbright[7]);
    assign led[8]  = (pwm < ledbright[8]);   assign led[9]  = (pwm < ledbright[9]);
    assign led[10] = (pwm < ledbright[10]);  assign led[11] = (pwm < ledbright[11]);
    assign led[12] = (pwm < ledbright[12]);  assign led[13] = (pwm < ledbright[13]);
    assign led[14] = (pwm < ledbright[14]);  assign led[15] = (pwm < ledbright[15]);

    // ---- I2S master TX -> UDA1334A DAC (Pmod JB) --------------------------------------------
    // Free-running Philips I2S: BCLK = 100MHz/32 = 3.125MHz; 64 BCLK/frame (32-bit slots, 16-bit
    // data MSB-first, 1-bit delay) -> Fs = 100MHz/2048 = 48.8kHz. The engine's stereo output
    // (sampL/sampR) updates at 28kHz, so each frame emits the latest sample (zero-order hold).
    // sampL/sampR are offset-binary (+32768); invert the MSB for two's-complement. UDA1334A needs
    // no MCLK (its internal PLL locks to BCLK/WS). Data changes on BCLK falling edge (i2s_c[4:0]==0),
    // is sampled by the DAC on the rising edge.
    reg [10:0] i2s_c = 11'd0;
    always @(posedge clk100) i2s_c <= i2s_c + 11'd1;   // [4:0]=within-bit, [10:5]=bit index 0..63
    wire [5:0] bidx = i2s_c[10:5];
    reg [15:0] shl = 16'd0, shr = 16'd0; reg ws_r = 1'b0, sd_r = 1'b0;
    always @(posedge clk100) if (i2s_c[4:0] == 5'd0) begin   // BCLK falling edge -> present next bit
        ws_r <= (bidx >= 6'd32);                             // WS: 0..31 = left, 32..63 = right
        if (bidx == 6'd0) begin                              // start of left slot: latch new sample
            shl  <= {~sampL[15], sampL[14:0]};
            shr  <= {~sampR[15], sampR[14:0]};
            sd_r <= 1'b0;                                     // 1-bit I2S delay -> bit 0 carries no data
        end else if (bidx == 6'd32) begin
            sd_r <= 1'b0;                                     // 1-bit delay at the right slot too
        end else if (bidx <= 6'd16) begin                    // left data bits 1..16, MSB first
            sd_r <= shl[15]; shl <= {shl[14:0], 1'b0};
        end else if (bidx >= 6'd33 && bidx <= 6'd48) begin   // right data bits 33..48
            sd_r <= shr[15]; shr <= {shr[14:0], 1'b0};
        end else sd_r <= 1'b0;                               // slot padding (LSBs)
    end
    assign i2s_bclk = i2s_c[4];
    assign i2s_ws   = ws_r;
    assign i2s_sd   = sd_r;
endmodule

`default_nettype wire
