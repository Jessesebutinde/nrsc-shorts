#!/usr/bin/env python3
"""
Self-test for render.py: builds a fake 2-segment source whose picture shows the
time and whose audio pitch encodes the time, renders a 3-clip recipe that uses
every join type, then checks lengths, cut points and the J-cut by measuring the
audio pitch in the output.

    python3 worker/selftest.py
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work-selftest"
SEG = 60
TAG = "src-test-service"


def sh(cmd):
    subprocess.run(cmd, check=True, text=True, capture_output=True)


def make_source():
    d = WORK / "src" / TAG
    d.mkdir(parents=True, exist_ok=True)
    for s in range(2):
        t0 = s * SEG
        # pitch = 100 + 10 * floor(t) Hz, t counted from the start of the whole service
        sh(["ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size=1280x720:rate=30:duration={SEG}",
            "-f", "lavfi", "-i", f"aevalsrc='0.5*sin(2*PI*t*(100+10*floor(t+{t0})))':s=48000:d={SEG}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k",
            "-shortest", str(d / f"source_{s:03d}.mp4")])
    (ROOT / "sources" / f"{TAG}.json").write_text(json.dumps({
        "tag": TAG, "title": "Self-test service", "date": "2026-01-01", "service": "test",
        "duration": 2 * SEG, "segment_seconds": SEG, "segment_count": 2, "source_ready": True}))


def dominant_hz(path, at, dur=0.4):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(at), "-t", str(dur), "-i", path,
                          "-ac", "1", "-ar", "48000", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.fft.rfftfreq(len(x), 1 / 48000)[int(np.argmax(spec[5:])) + 5]


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


def main():
    shutil.rmtree(WORK, ignore_errors=True)
    make_source()
    recipe = {
        "id": "selftest-clip", "rev": 1, "want": "final", "source": TAG, "title": "Self test clip",
        "outputs": ["whatsapp_long", "whatsapp_saver", "tiktok", "status"],
        "clips": [
            {"in": 10.0, "out": 20.0, "frame_x": 0.5, "join_after": {"type": "jcut", "lead": 1.5}},
            {"in": 55.0, "out": 70.0, "frame": [{"t": 0, "x": 0.2}, {"t": 8, "x": 0.9}],
             "join_after": {"type": "fade", "dur": 0.5}},
            {"in": 100.0, "out": 112.0, "look": "stage"},
        ]}
    rpath = WORK / "recipe.json"
    rpath.write_text(json.dumps(recipe))
    env = dict(os.environ, NRSC_WORK=str(WORK))
    subprocess.run([sys.executable, str(ROOT / "worker" / "render.py"), "--recipe", str(rpath),
                    "--local-src", str(WORK / "src"), "--no-upload", "--no-git", "--force", "--keep"],
                   check=True, env=env, stdout=subprocess.DEVNULL)
    status = json.loads((ROOT / "status" / "selftest-clip.json").read_text())
    assert status["stage"] == "final_ready", status
    print("status:", json.dumps(status, indent=1))

    out = status["files"]["whatsapp_long"]
    ok = True
    d = duration(out)
    exp = 10 + 15 + 12 - 0.5
    print(f"length {d:.2f}s, expected {exp:.2f}s")
    ok &= abs(d - exp) < 0.2

    # pitch checks: output time -> expected source time
    checks = [
        (1.0, 11.0, "clip 1 audio"),
        (9.0, 54.0, "J-cut: clip 2 audio already playing before its picture"),
        (12.0, 57.0, "clip 2 audio"),
        (20.0, 65.0, "clip 2 after the segment boundary"),
        (30.0, 105.5, "clip 3 audio after the fade"),
    ]
    for at, src_t, label in checks:
        hz = dominant_hz(out, at)
        want = 100 + 10 * int(src_t)
        good = abs(hz - want) < 6
        ok &= good
        print(f"{'OK ' if good else 'BAD'} {label}: at {at}s heard {hz:.0f} Hz, expected {want} Hz")
    for key, mb in status.get("sizes_mb", {}).items():
        print(f"{key}: {mb} MB")
    # frame grabs to look at by eye
    for at, name in [(0.0, "clip1_start"), (10.0, "clip2_start"), (24.6, "clip3_start")]:
        sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(at), "-i", out, "-frames:v", "1", str(WORK / f"{name}.png")])
    (ROOT / "status" / "selftest-clip.json").unlink()
    (ROOT / "sources" / f"{TAG}.json").unlink()
    print("SELFTEST", "PASSED" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
