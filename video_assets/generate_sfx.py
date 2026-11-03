import wave
import numpy as np

SAMPLE_RATE = 44100
DURATION = 14.0
TOTAL_SAMPLES = int(SAMPLE_RATE * DURATION)

master = np.zeros(TOTAL_SAMPLES, dtype=np.float32)

def add_click(t_sec, gain=0.35):
    idx = int(t_sec * SAMPLE_RATE)
    dur = 0.04
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), False)
    # High-pitch sharp click transient
    click = (np.sin(2 * np.pi * 2200 * t) * 0.6 + np.sin(2 * np.pi * 800 * t) * 0.4) * np.exp(-t * 120.0)
    end = min(idx + len(click), TOTAL_SAMPLES)
    master[idx:end] += click[:end - idx] * gain

def add_whoosh(t_sec, gain=0.2):
    idx = int(t_sec * SAMPLE_RATE)
    dur = 0.6
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), False)
    f = 120 + 200 * np.sin(np.pi * (t / dur))
    phase = 2 * np.pi * np.cumsum(f) / SAMPLE_RATE
    whoosh = np.sin(phase) * np.sin(np.pi * (t / dur)) * 0.5
    # Add gentle pink noise
    noise = (np.random.rand(len(t)) * 2 - 1) * np.sin(np.pi * (t / dur)) * 0.15
    sig = (whoosh + noise) * gain
    end = min(idx + len(sig), TOTAL_SAMPLES)
    master[idx:end] += sig[:end - idx]

# Clicks
add_whoosh(1.6, gain=0.18)
add_click(3.1, gain=0.4)
add_click(6.4, gain=0.35)
add_click(7.5, gain=0.35)
add_whoosh(8.0, gain=0.15)
add_click(8.7, gain=0.4)
add_whoosh(11.5, gain=0.2)

# Subtle ambient drone (-32 dB)
t = np.linspace(0, DURATION, TOTAL_SAMPLES, False)
ambient = np.sin(2 * np.pi * 65.41 * t) * 0.02 + np.sin(2 * np.pi * 130.81 * t) * 0.015
# Fade in / out
fade = int(SAMPLE_RATE * 0.5)
ambient[:fade] *= np.linspace(0, 1, fade)
ambient[-fade:] *= np.linspace(1, 0, fade)
master += ambient

master = np.clip(master, -0.95, 0.95)
int_master = (master * 32767.0).astype(np.int16)

with wave.open("/Users/maxdi/.gemini/antigravity/scratch/agent-id-monitor/video_assets/screen_studio_sfx.wav", "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(SAMPLE_RATE)
    wf.writeframes(int_master.tobytes())

print("SFX generated!")
