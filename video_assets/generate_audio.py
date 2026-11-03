import os
import subprocess
import numpy as np
import wave

SAMPLE_RATE = 44100
TOTAL_DURATION = 18.0  # seconds
TOTAL_SAMPLES = int(SAMPLE_RATE * TOTAL_DURATION)

def run_cmd(cmd):
    subprocess.run(cmd, shell=True, check=True)

def generate_tone(freq, duration, decay=True, wave_type='sine'):
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), False)
    if wave_type == 'sine':
        sig = np.sin(2 * np.pi * freq * t)
    elif wave_type == 'saw':
        sig = 2 * (t * freq - np.floor(0.5 + t * freq))
    elif wave_type == 'square':
        sig = np.sign(np.sin(2 * np.pi * freq * t))
    if decay:
        env = np.exp(-t * (4.0 / duration))
        sig = sig * env
    return sig

def create_synth_track(duration=18.0):
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), False)
    # 128 BPM = ~0.46875s per beat, 16th note = ~0.117s
    bpm = 128.0
    beat_dur = 60.0 / bpm
    sixteenth = beat_dur / 4.0
    
    music = np.zeros(len(t))
    
    # 1. Driving Synth Bassline (D minor: D2, F2, G2, A2, C3)
    bass_notes = [73.42, 73.42, 87.31, 73.42, 98.00, 73.42, 110.00, 73.42] # Hz
    num_bass_steps = int(duration / (beat_dur / 2.0))
    for i in range(num_bass_steps):
        freq = bass_notes[i % len(bass_notes)]
        start_idx = int(i * (beat_dur / 2.0) * SAMPLE_RATE)
        dur = beat_dur / 2.0 * 0.95
        sub_t = np.linspace(0, dur, int(SAMPLE_RATE * dur), False)
        # Filtered saw + sub sine
        sub = np.sin(2 * np.pi * freq * sub_t) * 0.7
        saw = (2 * (sub_t * freq - np.floor(0.5 + sub_t * freq))) * 0.3
        env = np.exp(-sub_t * 6.0)
        note = (sub + saw) * env
        end_idx = min(start_idx + len(note), len(music))
        music[start_idx:end_idx] += note[:end_idx - start_idx] * 0.35

    # 2. Cyber Arpeggio (16th notes: D4, F4, A4, C5, D5, C5, A4, F4)
    arp_notes = [293.66, 349.23, 440.00, 523.25, 587.33, 523.25, 440.00, 349.23]
    num_arp_steps = int(duration / sixteenth)
    for i in range(num_arp_steps):
        freq = arp_notes[i % len(arp_notes)]
        start_idx = int(i * sixteenth * SAMPLE_RATE)
        dur = sixteenth * 0.8
        sub_t = np.linspace(0, dur, int(SAMPLE_RATE * dur), False)
        note = np.sin(2 * np.pi * freq * sub_t) * np.exp(-sub_t * 18.0)
        end_idx = min(start_idx + len(note), len(music))
        music[start_idx:end_idx] += note[:end_idx - start_idx] * 0.18

    # 3. High-tech Digital Ticks / Hi-hats
    for i in range(int(duration / (beat_dur / 2.0))):
        start_idx = int((i * (beat_dur / 2.0) + (beat_dur / 4.0)) * SAMPLE_RATE)
        dur = 0.04
        sub_t = np.linspace(0, dur, int(SAMPLE_RATE * dur), False)
        noise = (np.random.rand(len(sub_t)) * 2 - 1) * np.exp(-sub_t * 80.0)
        end_idx = min(start_idx + len(noise), len(music))
        if start_idx < len(music):
            music[start_idx:end_idx] += noise[:end_idx - start_idx] * 0.12

    # 4. Kick drum on every beat
    num_beats = int(duration / beat_dur)
    for i in range(num_beats):
        start_idx = int(i * beat_dur * SAMPLE_RATE)
        dur = 0.15
        sub_t = np.linspace(0, dur, int(SAMPLE_RATE * dur), False)
        # Pitch sweep from 150Hz down to 40Hz
        freq_sweep = 150.0 * np.exp(-sub_t * 30.0) + 45.0
        phase = 2 * np.pi * np.cumsum(freq_sweep) / SAMPLE_RATE
        kick = np.sin(phase) * np.exp(-sub_t * 18.0)
        end_idx = min(start_idx + len(kick), len(music))
        if start_idx < len(music):
            music[start_idx:end_idx] += kick[:end_idx - start_idx] * 0.45

    # Master limiter / normalizer
    music = np.tanh(music) * 0.4
    return music

def generate_all_sfx():
    sfx_map = {}
    
    # SFX 1: Radar ping (at t=0.5s)
    t = np.linspace(0, 0.4, int(SAMPLE_RATE * 0.4), False)
    ping = (np.sin(2 * np.pi * 880 * t) + 0.5 * np.sin(2 * np.pi * 1760 * t)) * np.exp(-t * 9.0)
    sfx_map['radar'] = ping * 0.35

    # SFX 2: Alert Chime (at t=2.0s) - dual tone chime
    t = np.linspace(0, 0.6, int(SAMPLE_RATE * 0.6), False)
    alert = (np.sin(2 * np.pi * 1046.50 * t) + np.sin(2 * np.pi * 1318.51 * t)) * np.exp(-t * 5.0)
    # Add punchy sub click
    click = np.sin(2 * np.pi * 150 * t[:int(SAMPLE_RATE*0.05)]) * np.exp(-t[:int(SAMPLE_RATE*0.05)] * 40.0)
    alert[:len(click)] += click * 0.8
    sfx_map['alert'] = alert * 0.6

    # SFX 3: Audit Success Chimes (at t=6.5s, 7.0s, 7.5s)
    t = np.linspace(0, 0.25, int(SAMPLE_RATE * 0.25), False)
    sfx_map['audit_chime'] = (np.sin(2 * np.pi * 1567.98 * t) + 0.4 * np.sin(2 * np.pi * 2093.00 * t)) * np.exp(-t * 12.0) * 0.4

    # SFX 4: Sniper Laser Lock & Shoot (at t=10.2s)
    t = np.linspace(0, 0.5, int(SAMPLE_RATE * 0.5), False)
    f_sweep = 2400.0 * np.exp(-t * 10.0) + 120.0
    phase = 2 * np.pi * np.cumsum(f_sweep) / SAMPLE_RATE
    laser = np.sin(phase) * np.exp(-t * 5.0)
    sfx_map['sniper_laser'] = laser * 0.5

    # SFX 5: Broadcast Confirmed / Cash Ring (at t=11.5s)
    t = np.linspace(0, 0.8, int(SAMPLE_RATE * 0.8), False)
    ring = (np.sin(2 * np.pi * 1760 * t) + np.sin(2 * np.pi * 2637 * t) + np.sin(2 * np.pi * 3520 * t)) * np.exp(-t * 4.0)
    sfx_map['confirmed'] = ring * 0.55

    # SFX 6: Sub Drop Riser (at t=13.5s)
    t = np.linspace(0, 1.2, int(SAMPLE_RATE * 1.2), False)
    sub = np.sin(2 * np.pi * (80.0 * np.exp(-t * 2.0)) * t) * np.exp(-t * 2.0)
    sfx_map['sub_drop'] = sub * 0.65

    return sfx_map

def main():
    out_dir = "/Users/maxdi/.gemini/antigravity/scratch/agent-id-monitor/video_assets"
    os.makedirs(out_dir, exist_ok=True)
    
    # 1. Generate Voiceovers using macOS say
    vo_scripts = [
        ("vo1.aiff", "Stealth fair-mints on BNB Chain happen in seconds. Catch them at Block Zero."),
        ("vo2.aiff", "Track 5,000 smart wallets with automated anti-rug and honeypot audits."),
        ("vo3.aiff", "Simulate and snipe live drops with one-click execution."),
        ("vo4.aiff", "Twenty-four-seven Live Radar on Web and Terminal. Dominate BNB Chain today.")
    ]
    
    vo_wavs = []
    for filename, text in vo_scripts:
        aiff_path = os.path.join(out_dir, filename)
        wav_path = os.path.join(out_dir, filename.replace(".aiff", ".wav"))
        run_cmd(f'say -v Daniel -r 215 -o "{aiff_path}" "{text}"')
        run_cmd(f'ffmpeg -i "{aiff_path}" -ar {SAMPLE_RATE} -ac 1 -y "{wav_path}" 2>/dev/null')
        vo_wavs.append(wav_path)
    
    # Load VO data
    vo_tracks = []
    for w in vo_wavs:
        with wave.open(w, 'rb') as wf:
            frames = wf.readframes(wf.getnframes())
            data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            vo_tracks.append(data)
    
    # 2. Master Audio Canvas (18.0s)
    master = np.zeros(TOTAL_SAMPLES)
    
    # Music Track
    music = create_synth_track(TOTAL_DURATION)
    
    # Voiceover Timings (in seconds)
    # Scene 1: 0.0 - 4.5s -> VO at 0.4s
    # Scene 2: 4.5 - 9.0s -> VO at 4.8s
    # Scene 3: 9.0 - 13.5s -> VO at 9.3s
    # Scene 4: 13.5 - 18.0s -> VO at 13.8s
    vo_schedule = [0.4, 4.8, 9.3, 13.8]
    
    ducking_mask = np.ones(TOTAL_SAMPLES)
    for start_t, vo_data in zip(vo_schedule, vo_tracks):
        start_idx = int(start_t * SAMPLE_RATE)
        end_idx = min(start_idx + len(vo_data), TOTAL_SAMPLES)
        dur_samples = end_idx - start_idx
        master[start_idx:end_idx] += vo_data[:dur_samples] * 0.95
        # Duck music during VO
        pad = int(SAMPLE_RATE * 0.1)
        duck_start = max(0, start_idx - pad)
        duck_end = min(TOTAL_SAMPLES, end_idx + pad)
        ducking_mask[duck_start:duck_end] = 0.35  # Duck music by 65%
    
    # Apply ducking to music
    master += (music * ducking_mask)
    
    # 3. Add SFX
    sfx = generate_all_sfx()
    sfx_events = [
        (0.3, sfx['radar']),
        (1.8, sfx['alert']),
        (3.0, sfx['radar']),
        (4.5, sfx['sub_drop']),
        (6.2, sfx['audit_chime']),
        (7.0, sfx['audit_chime']),
        (7.8, sfx['audit_chime']),
        (9.0, sfx['sub_drop']),
        (10.5, sfx['sniper_laser']),
        (11.4, sfx['confirmed']),
        (13.5, sfx['sub_drop'])
    ]
    
    for t_sfx, sound in sfx_events:
        idx = int(t_sfx * SAMPLE_RATE)
        end = min(idx + len(sound), TOTAL_SAMPLES)
        master[idx:end] += sound[:end - idx]
    
    # Master Fade In / Out
    fade_in = int(SAMPLE_RATE * 0.2)
    master[:fade_in] *= np.linspace(0, 1, fade_in)
    fade_out = int(SAMPLE_RATE * 0.5)
    master[-fade_out:] *= np.linspace(1, 0, fade_out)
    
    # Normalize & Soft Clip
    master = np.clip(master, -0.98, 0.98)
    int_master = (master * 32767.0).astype(np.int16)
    
    master_path = os.path.join(out_dir, "master_audio.wav")
    with wave.open(master_path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(int_master.tobytes())
    
    print(f"Master audio successfully generated at {master_path} (Duration: {TOTAL_DURATION}s)")

if __name__ == "__main__":
    main()

