// M23 — Verilator harness for the XLS32 Tiliqua core.
//
// Adapted from the SDK's src/top/dsp/sim_dsp_core.cpp. Two differences:
//
//   * No input injection. The core ignores its ADC inputs and plays a fixed boot patch, so
//     driving the codec's input side would only add noise to the thing being measured.
//   * The captured out0 samples are written as one decimal value per line instead of an SVG,
//     so boards/tiliqua/check_pitch.py can FFT them without a plotting dependency.
//
// XLS_SIM_MS  simulated milliseconds     (default 250 -> ~12200 samples; build.sh sets it too)
// XLS_SIM_OUT output path                (default out0.txt, relative to the run directory)

#if defined VM_TRACE_FST && VM_TRACE_FST == 1
#include <verilated_fst_c.h>
#endif

#include "Vtiliqua_soc.h"
#include "verilated.h"

#include "i2s.h"

#include <cstdio>
#include <cstdlib>
#include <string>

// Length of BOOT_MIDI in xls_core.py; only used to make the progress print readable.
#define BOOT_MIDI_LEN 12

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

    const char *ms_env  = getenv("XLS_SIM_MS");
    const char *out_env = getenv("XLS_SIM_OUT");
    const uint64_t sim_ms = ms_env ? strtoull(ms_env, nullptr, 10) : 40;
    const std::string out_path = out_env ? out_env : "out0.txt";
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
    top->eval();

    uint64_t ns_in_s = 1e9;
    uint64_t ns_in_sync_cycle  = ns_in_s /  SYNC_CLK_HZ;
    uint64_t ns_in_audio_cycle = ns_in_s / AUDIO_CLK_HZ;
    uint64_t ns_in_fast_cycle  = ns_in_s /  FAST_CLK_HZ;

    printf("sync domain is: %i KHz (%llu ns/cycle)\n",  SYNC_CLK_HZ/1000,
           (unsigned long long)ns_in_sync_cycle);
    printf("audio clock is: %i KHz (%llu ns/cycle)\n", AUDIO_CLK_HZ/1000,
           (unsigned long long)ns_in_audio_cycle);
    printf("simulating %llu ms -> '%s'\n",
           (unsigned long long)sim_ms, out_path.c_str());

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

    printf("boot rom idx %u/%u, engine samples %u, resampler inputs %u, codec writes %u\n",
           (unsigned)top->dbg_rom, (unsigned)BOOT_MIDI_LEN,
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
