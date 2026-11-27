#!/usr/bin/env python3
"""Cross-platform Python script to synthesize sound effects and ambience beds with ffmpeg.
All generated files are 48 kHz stereo WAV in sounds/.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOUNDS = os.path.join(ROOT, "sounds")
os.makedirs(SOUNDS, exist_ok=True)

OUT_OPTS = ["-ar", "48000", "-ac", "2"]

COMMANDS = [
    # pop: short sine blip falling 880 -> 440 Hz
    (
        "pop.wav",
        ["-f", "lavfi", "-i", "aevalsrc='sin(2*PI*(880*t-1833.33*t*t))*exp(-28*t)':d=0.12:s=48000"]
    ),
    # stamp: low 90 Hz thud + noise burst
    (
        "stamp.wav",
        [
            "-f", "lavfi", "-i", "sine=frequency=90:duration=0.18:sample_rate=48000",
            "-f", "lavfi", "-i", "anoisesrc=color=pink:duration=0.05:sample_rate=48000",
            "-filter_complex", "[0:a]afade=t=out:st=0:d=0.18,lowpass=f=300[a];[1:a]afade=t=out:st=0:d=0.05,lowpass=f=400,volume=0.6[b];[a][b]amix=inputs=2:normalize=0,volume=3.2,alimiter=limit=0.8"
        ]
    ),
    # whoosh: filtered-noise sweep rising 300 -> 4800 Hz (~0.5s)
    (
        "whoosh.wav",
        [
            "-f", "lavfi", "-i", "anoisesrc=color=white:duration=0.5:sample_rate=48000",
            "-filter_complex", "[0:a]asplit=5[s0][s1][s2][s3][s4];[s0]atrim=0:0.1,asetpts=PTS-STARTPTS,bandpass=f=300:width_type=o:width=2[w0];[s1]atrim=0.1:0.2,asetpts=PTS-STARTPTS,bandpass=f=700:width_type=o:width=2[w1];[s2]atrim=0.2:0.3,asetpts=PTS-STARTPTS,bandpass=f=1400:width_type=o:width=2[w2];[s3]atrim=0.3:0.4,asetpts=PTS-STARTPTS,bandpass=f=2600:width_type=o:width=2[w3];[s4]atrim=0.4:0.5,asetpts=PTS-STARTPTS,bandpass=f=4800:width_type=o:width=2[w4];[w0][w1][w2][w3][w4]concat=n=5:v=0:a=1,afade=t=in:st=0:d=0.15,afade=t=out:st=0.3:d=0.2,volume=14,alimiter=limit=0.7"
        ]
    ),
    # boom: sine chirp 90 -> 30 Hz with decay + low noise tail (~1.2s)
    (
        "boom.wav",
        [
            "-f", "lavfi", "-i", "aevalsrc='sin(2*PI*(90*t-35*t*t))*exp(-3.2*t)':d=1.2:s=48000",
            "-f", "lavfi", "-i", "anoisesrc=color=brown:duration=1.2:sample_rate=48000",
            "-filter_complex", "[1:a]lowpass=f=220,afade=t=out:st=0:d=1.2,volume=0.8[n];[0:a][n]amix=inputs=2:normalize=0,volume=1.1,alimiter=limit=0.95"
        ]
    ),
    # rumble: brown noise, lowpassed, swelling in and out (~1.6s)
    (
        "rumble.wav",
        [
            "-f", "lavfi", "-i", "anoisesrc=color=brown:duration=1.6:sample_rate=48000",
            "-af", "lowpass=f=130,afade=t=in:st=0:d=0.5,afade=t=out:st=0.9:d=0.7,volume=2.2,alimiter=limit=0.9"
        ]
    ),
    # riser: tone sweeping 200 -> 2600 Hz + rising noise
    (
        "riser.wav",
        [
            "-f", "lavfi", "-i", "aevalsrc='sin(2*PI*(200*t+1000*t*t))*pow(t/1.2,2)*0.7':d=1.2:s=48000",
            "-f", "lavfi", "-i", "anoisesrc=color=white:duration=1.2:sample_rate=48000",
            "-filter_complex", "[1:a]highpass=f=1500,lowpass=f=6000,volume=0.25,afade=t=in:st=0:d=1.1[n];[0:a][n]amix=inputs=2:normalize=0,afade=t=out:st=1.1:d=0.1"
        ]
    ),
    # tick: high click
    (
        "tick.wav",
        ["-f", "lavfi", "-i", "aevalsrc='sin(2*PI*2200*t)*exp(-180*t)':d=0.05:s=48000"]
    ),
    # click: mid click
    (
        "click.wav",
        ["-f", "lavfi", "-i", "aevalsrc='sin(2*PI*1200*t)*exp(-120*t)':d=0.06:s=48000"]
    ),
    # ding: chime
    (
        "ding.wav",
        ["-f", "lavfi", "-i", "aevalsrc='(sin(2*PI*1318*t)+0.4*sin(2*PI*2636*t))*exp(-5*t)*0.6':d=0.9:s=48000"]
    ),
    # swish: airy transition noise
    (
        "swish.wav",
        [
            "-f", "lavfi", "-i", "anoisesrc=color=white:duration=0.25:sample_rate=48000",
            "-af", "highpass=f=1500,lowpass=f=7000,afade=t=in:st=0:d=0.08,afade=t=out:st=0.1:d=0.15,volume=0.9"
        ]
    ),
    # bed_drone: 8s loopable low harmonic drone
    (
        "bed_drone.wav",
        ["-f", "lavfi", "-i", "aevalsrc='0.35*sin(2*PI*55*t)+0.25*sin(2*PI*82.5*t+0.5*sin(2*PI*0.25*t))+0.15*sin(2*PI*110*t)':d=8:s=48000"]
    ),
    # bed_wind: 8s loopable wind bed
    (
        "bed_wind.wav",
        [
            "-f", "lavfi", "-i", "anoisesrc=color=pink:duration=8:sample_rate=48000",
            "-af", "bandpass=f=500:width_type=h:width=700,afade=t=in:st=0:d=0.6,afade=t=out:st=7.4:d=0.6,volume=1.4"
        ]
    ),
    # bed_tense: 8s loopable suspense drone with subtle low throb
    (
        "bed_tense.wav",
        [
            "-f", "lavfi", "-i", "aevalsrc='0.4*sin(2*PI*48*t*(1+0.03*sin(2*PI*0.5*t)))+0.2*sin(2*PI*72*t)+0.1*sin(2*PI*96*t)':d=8:s=48000",
            "-af", "lowpass=f=220,volume=1.2"
        ]
    ),
    # bed_jungle: 8s loopable ambient nature bed
    (
        "bed_jungle.wav",
        [
            "-f", "lavfi", "-i", "anoisesrc=color=pink:duration=8:sample_rate=48000",
            "-f", "lavfi", "-i", "aevalsrc='0.08*sin(2*PI*4200*t)*pow(0.5+0.5*sin(2*PI*6*t),6)':d=8:s=48000",
            "-filter_complex", "[0:a]bandpass=f=600:width_type=h:width=800,volume=0.7[a];[1:a]volume=0.4[b];[a][b]amix=inputs=2:normalize=0,afade=t=in:st=0:d=0.5,afade=t=out:st=7.5:d=0.5"
        ]
    ),
]

def main():
    print(f"[make_sounds] Generating audio library in {SOUNDS}...")
    for filename, args in COMMANDS:
        out_path = os.path.join(SOUNDS, filename)
        cmd = ["ffmpeg", "-v", "error", "-y"] + args + OUT_OPTS + [out_path]
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print(f"FAILED: {filename}", file=sys.stderr)
        else:
            print(f"  OK: {filename}")
    print("[make_sounds] Done.")

if __name__ == "__main__":
    main()
