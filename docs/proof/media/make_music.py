"""Original ambient/lo-fi track, stdlib only. Usage: synth.py out.wav seconds"""
import math, random, struct, sys, wave
SR, DUR = 44100, float(sys.argv[2])
N = int(SR * DUR)
BPM = 84; beat = 60 / BPM
random.seed(7)
def hz(m): return 440 * 2 ** ((m - 69) / 12)
# Am9 - Fmaj7 - Cmaj7 - G6, two bars each (8 beats)
chords = [[57, 60, 64, 67, 71], [53, 57, 60, 64, 69], [48, 55, 59, 64, 67], [55, 59, 62, 64, 71]]
bass = [45, 41, 48, 43]
buf = [0.0] * N
def add(start, dur, f, amp, kind):
    s0, n = int(start * SR), int(dur * SR)
    for i in range(n):
        j = s0 + i
        if j >= N: break
        t = i / SR
        if kind == "pad":
            env = min(1, t / 1.2) * min(1, (dur - t) / 1.5)
            v = math.sin(2*math.pi*f*t) + 0.5*math.sin(2*math.pi*f*1.003*t) + 0.25*math.sin(2*math.pi*f*2*t)
        elif kind == "pluck":
            env = math.exp(-t * 5) * min(1, t / 0.005)
            v = math.sin(2*math.pi*f*t) + 0.3*math.sin(2*math.pi*f*3*t)*math.exp(-t*12)
        else:  # bass
            env = min(1, t / 0.02) * math.exp(-t * 1.2)
            v = math.sin(2*math.pi*f*t)
        buf[j] += amp * env * v
bar = 4 * beat
t, k = 0.0, 0
while t < DUR:
    c = chords[k % 4]
    for m in c: add(t, 2 * bar + 0.8, hz(m), 0.035, "pad")
    add(t, 2 * bar, hz(bass[k % 4]), 0.16, "bass")
    add(t + 4 * beat, 2 * bar - 4 * beat, hz(bass[k % 4]), 0.12, "bass")
    if t > 2 * bar:  # arpeggio enters after the intro
        pat = [0, 2, 4, 3, 1, 3, 4, 2]
        for s in range(16):
            n = c[pat[s % 8]] + 12
            add(t + s * beat / 2, beat * 1.5, hz(n), 0.05 * (0.8 + 0.4 * random.random()), "pluck")
    t += 2 * bar; k += 1
# soft echo + gentle low-pass
d = int(beat * 0.75 * SR)
for i in range(d, N): buf[i] += 0.28 * buf[i - d]
y, a = 0.0, 0.25
for i in range(N): y += a * (buf[i] - y); buf[i] = y
peak = max(abs(x) for x in buf) or 1
with wave.open(sys.argv[1], "w") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes(b"".join(struct.pack("<h", int(32000 * 0.85 * x / peak)) for x in buf))
