// M24 — Verilator harness for the XLS32 Tiliqua core.
//
// Adapted from the SDK's src/top/dsp/sim_dsp_core.cpp. Three differences:
//
//   * No audio input injection. The core ignores its ADC inputs, so driving the codec's input
//     side would only add noise to the thing being measured.
//   * MIDI *is* injected, by bit-banging the `midi_rx` port that stands in for the jack's
//     optoisolator. Since M24 the boot ROM no longer plays a note, so without this the capture
//     is silence.
//   * The captured out0 samples are written as one decimal value per line instead of an SVG,
//     so boards/tiliqua/check_pitch.py can FFT them without a plotting dependency.
//
// XLS_SIM_MS   simulated milliseconds    (default 40; build.sh sets 250)
// XLS_SIM_OUT  output path               (default out0.txt, relative to the run directory)
// XLS_SIM_MIDI which MIDI script to play (default "pitch"; see below)

#if defined VM_TRACE_FST && VM_TRACE_FST == 1
#include <verilated_fst_c.h>
#endif

#include "Vtiliqua_soc.h"
#include "verilated.h"

#include "i2s.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

// Length of BOOT_MIDI in xls_core.py; only used to make the progress print readable.
#define BOOT_MIDI_LEN 36

// One byte to shift out of `midi_rx`, `delay_ms` after the previous one finished (or after
// reset, for the first).
struct MidiEvent {
    uint64_t delay_ms;
    uint8_t  byte;
};

// "pitch": one sustained note-on, A4 on channel 1. The M23 regression, now arriving over the
// wire rather than from the boot ROM, so check_pitch.py keeps working and additionally proves
// the UART, the filters and the byte CDC.
static std::vector<MidiEvent> script_pitch() {
    return { {10, 0x90}, {0, 69}, {0, 100} };
}

// "parts": channels 1-4 in turn, each with its own note *and* its own CC7 volume. The volumes
// are what actually tests per-part routing: synth.x:337 takes the channel nibble's low 2 bits
// as the part, but a part is polyphonic, so four notes in sequence would sound correct even if
// routing collapsed and all four landed on part 0. Four distinct amplitudes cannot.
//
// They are sent over the wire rather than baked into BOOT_MIDI: the boot patch is a product
// decision, not a test fixture, and this way the CC bytes are exercised through the same path.
//
// The gap between channels is long because the notes land on *different* parts: a release tail
// from the loud channel 1 overlapping the quiet channel 4 would inflate the later segments and
// could fail the amplitude ordering for a reason that has nothing to do with routing.
//
// boards/tiliqua/check_midi.py mirrors these four constants to locate the segments. Keep them
// in step.
#define PARTS_LEAD_MS 100
#define PARTS_HOLD_MS 250
#define PARTS_GAP_MS  150
static std::vector<MidiEvent> script_parts() {
    const uint8_t note[4] = {69, 63, 78, 60};   // A4, D#4, F#5, C4
    const uint8_t vol[4]  = {110, 80, 55, 30};  // strictly descending; check_midi.py asserts it
    std::vector<MidiEvent> s;
    for (int ch = 0; ch < 4; ch++) {
        uint64_t lead = ch == 0 ? PARTS_LEAD_MS : PARTS_GAP_MS;
        s.push_back({lead, (uint8_t)(0xB0 | ch)});          // CC7 volume
        s.push_back({0, 7});
        s.push_back({0, vol[ch]});
        s.push_back({0, (uint8_t)(0x90 | ch)});             // note on
        s.push_back({0, note[ch]});
        s.push_back({0, 100});
        s.push_back({PARTS_HOLD_MS, (uint8_t)(0x80 | ch)}); // note off
        s.push_back({0, note[ch]});
        s.push_back({0, 0});
    }
    return s;
}

// "panic": two identical chords, each stopped by a channel mode message instead of by note-offs
// -- CC123 on channel 1, CC120 on channel 2. No note-off is ever sent, so an engine that still
// drops 120-127 in `apply_cc`'s catch-all leaves both chords sustaining to the end of the capture.
// That is the regression this script exists to catch; the difference in *how* they stop (CC123
// falls through RELEASE, CC120 cuts) is the second thing check_panic.py grades.
//
// The echo and the chorus are switched off first, by their depth gates (CC95/CC94, fx.py:310).
// XLS_SIM_OUT captures out0, which is the *wet* side of StereoFx, and the echo's delay line is
// long enough to drop a copy of the chord straight into the window that asks whether the chord
// stopped. The question here is about the engine, so the effects are taken out of the answer.
//
// boards/tiliqua/check_panic.py mirrors these constants to locate its windows. Keep them in step.
#define PANIC_LEAD_MS 100
#define PANIC_HOLD_MS 200
#define PANIC_TAIL_MS 300
static std::vector<MidiEvent> script_panic() {
    const uint8_t chord[3] = {60, 64, 67};      // C major, three voices on one part
    const uint8_t mode[2]  = {123, 120};        // group 0 releases, group 1 is cut dead
    std::vector<MidiEvent> s;
    s.push_back({1, 0xB0}); s.push_back({0, 95}); s.push_back({0, 0});   // echo depth 0
    s.push_back({0, 0xB0}); s.push_back({0, 94}); s.push_back({0, 0});   // chorus depth 0
    for (int g = 0; g < 2; g++) {
        uint64_t lead = g == 0 ? PANIC_LEAD_MS : PANIC_TAIL_MS;
        for (int i = 0; i < 3; i++) {
            s.push_back({i == 0 ? lead : 0, (uint8_t)(0x90 | g)});
            s.push_back({0, chord[i]});
            s.push_back({0, 100});
        }
        s.push_back({PANIC_HOLD_MS, (uint8_t)(0xB0 | g)});
        s.push_back({0, mode[g]});
        s.push_back({0, 0});
    }
    return s;
}

int main(int argc, char** argv) {

    VerilatedContext* contextp = new VerilatedContext;
    contextp->commandArgs(argc, argv);
    Vtiliqua_soc* top = new Vtiliqua_soc{contextp};

#if defined VM_TRACE_FST && VM_TRACE_FST == 1
    Verilated::traceEverOn(true);
    VerilatedFstC* tfp = new VerilatedFstC;
    top->trace(tfp, 99);
    tfp->open("simx.fst");
#endif

    const char *ms_env   = getenv("XLS_SIM_MS");
    const char *out_env  = getenv("XLS_SIM_OUT");
    const char *midi_env = getenv("XLS_SIM_MIDI");
    const uint64_t sim_ms = ms_env ? strtoull(ms_env, nullptr, 10) : 40;
    const std::string out_path = out_env ? out_env : "out0.txt";
    const std::string midi_name = midi_env ? midi_env : "pitch";
    const std::vector<MidiEvent> midi_script =
        midi_name == "parts" ? script_parts() :
        midi_name == "panic" ? script_panic() : script_pitch();
    // contextp->time() counts picoseconds below (the loop advances by 1000 ps per step).
    const uint64_t sim_time = sim_ms * 1000000000ULL;

    // Reset is held across real clock edges, not just pulsed before the loop starts as in the
    // SDK's own harness. Amaranth registers carry init values and come up correct without ever
    // seeing an asserted reset; the XLS engine does not -- its proc state (including the bit
    // that says "a state is live") is only established by a synchronous reset, so with a
    // zero-length pulse it sits dead forever and never asserts `_midi_in_rdy`.
    const uint64_t reset_ns = 2000;   // ~24 audio cycles, ~120 sync cycles

    contextp->timeInc(1);
    top->rst_sync = 1;
    top->rst_audio = 1;
    top->rst_fast = 1;
    top->midi_rx = 1;      // the optoisolated jack idles high
    top->eval();

    uint64_t ns_in_s = 1e9;
    uint64_t ns_in_sync_cycle  = ns_in_s /  SYNC_CLK_HZ;
    uint64_t ns_in_audio_cycle = ns_in_s / AUDIO_CLK_HZ;
    uint64_t ns_in_fast_cycle  = ns_in_s /  FAST_CLK_HZ;

    // The bit period is derived from the receiver's own divisor, NOT from a literal 31250 baud.
    // ns_in_sync_cycle is 1e9/60e6 truncated to 16, so the simulated sync clock is 62.5 MHz --
    // 4.17% fast, the same class of artefact as the 12.5 MHz mclk. A transmitter at a true
    // 31250 baud would slip 42% of a bit by the stop bit against a receiver dividing that fast
    // clock by 1920, and would fail for a reason that does not exist in hardware. Dividing the
    // same clock by the same 1920 keeps the harness testing the design instead of the timebase.
    // (Hardware baud accuracy is a separate and purely arithmetic claim: 60e6/31250 = 1920,
    // exactly, zero error.)
    const uint64_t midi_bit_ns = 1920 * ns_in_sync_cycle;

    printf("sync domain is: %i KHz (%llu ns/cycle)\n",  SYNC_CLK_HZ/1000,
           (unsigned long long)ns_in_sync_cycle);
    printf("audio clock is: %i KHz (%llu ns/cycle)\n", AUDIO_CLK_HZ/1000,
           (unsigned long long)ns_in_audio_cycle);
    printf("midi script '%s': %zu bytes at %llu ns/bit\n",
           midi_name.c_str(), midi_script.size(), (unsigned long long)midi_bit_ns);
    printf("simulating %llu ms -> '%s'\n",
           (unsigned long long)sim_ms, out_path.c_str());

    // Scripted MIDI transmitter: 8N1, LSB first. `frame` holds start bit, 8 data bits and stop
    // bit in shift order; `tx_bit` is -1 while idle.
    size_t   midi_idx  = 0;
    int      midi_bit  = -1;
    uint16_t midi_frame = 0;
    uint64_t midi_next_ns = 0;
    uint64_t midi_gate_ns = midi_script.empty()
        ? 0 : reset_ns + midi_script[0].delay_ms * 1000000ULL;

    // Spelled out rather than relying on CTAD, which needs -std=c++17 and Verilator's
    // default here is older.
    I2SDriver<Vtiliqua_soc> i2s_driver(top);

    while (contextp->time() < sim_time && !contextp->gotFinish()) {

        uint64_t timestamp_ns = contextp->time() / 1000;

        if (timestamp_ns >= reset_ns && top->rst_sync) {
            top->rst_sync = 0;
            top->rst_audio = 0;
            top->rst_fast = 0;
        }

        if (timestamp_ns % (ns_in_sync_cycle/2) == 0) {
            top->clk_sync = !top->clk_sync;
        }

        if (midi_bit < 0) {
            if (midi_idx < midi_script.size() && timestamp_ns >= midi_gate_ns) {
                midi_frame = ((uint16_t)midi_script[midi_idx].byte << 1) | 0x200;
                midi_bit = 0;
                midi_next_ns = timestamp_ns + midi_bit_ns;
                top->midi_rx = midi_frame & 1;    // start bit, always 0
            }
        } else if (timestamp_ns >= midi_next_ns) {
            midi_bit++;
            if (midi_bit >= 10) {
                midi_bit = -1;
                top->midi_rx = 1;                 // back to idle
                midi_idx++;
                if (midi_idx < midi_script.size()) {
                    midi_gate_ns = timestamp_ns
                                 + midi_script[midi_idx].delay_ms * 1000000ULL;
                }
            } else {
                top->midi_rx = (midi_frame >> midi_bit) & 1;
                midi_next_ns += midi_bit_ns;
            }
        }

        if (timestamp_ns % (ns_in_audio_cycle/2) == 0) {
            top->clk_audio = !top->clk_audio;
            i2s_driver.post_edge();
        }

        if (timestamp_ns % (ns_in_fast_cycle/2) == 0) {
            top->clk_fast = !top->clk_fast;
        }

        contextp->timeInc(1000);
        top->eval();
#if defined VM_TRACE_FST && VM_TRACE_FST == 1
        tfp->dump(contextp->time());
#endif
    }

#if defined VM_TRACE_FST && VM_TRACE_FST == 1
    tfp->close();
#endif

    printf("boot rom idx %u/%u, midi bytes in %u/%zu, engine samples %u, "
           "resampler inputs %u, codec writes %u\n",
           (unsigned)top->dbg_rom, (unsigned)BOOT_MIDI_LEN,
           (unsigned)top->dbg_midi, midi_idx,
           (unsigned)top->dbg_eng, (unsigned)top->dbg_res, (unsigned)top->dbg_out);

    FILE *f = fopen(out_path.c_str(), "w");
    if (!f) {
        fprintf(stderr, "cannot open '%s' for writing\n", out_path.c_str());
        return 1;
    }
    const auto &samples = i2s_driver.get_captured_samples(0);
    for (auto &y : samples) {
        fprintf(f, "%d\n", (int)y);
    }
    fclose(f);
    printf("captured %zu samples on out0 -> '%s'\n", samples.size(), out_path.c_str());

    return 0;
}
