"""Generate the DSLX sine LUT and the DDS phase increment for milestone 1."""
import math

N = 256
vals = [max(0, min(255, round(127.5 + 127.5 * math.sin(2 * math.pi * i / N)))) for i in range(N)]
print("// 256-entry sine LUT, u8 centered at 128")
line = "const SINE: u8[256] = u8[256]:[" + ", ".join(str(v) for v in vals) + "];"
print(line)

SR = 4000
for f, name in [(440, "A4")]:
    inc = round(f / SR * 2**32)
    print(f"// {name} {f} Hz at {SR} Hz sample rate")
    print(f"const NOTE_INC: u32 = u32:{inc};")

# a few sanity values for the DSLX tests
print(f"// sine[0]={vals[0]} sine[64]={vals[64]} sine[128]={vals[128]} sine[192]={vals[192]}")
