#!/usr/bin/env python3
"""
NRSC Shorts - pack and ship (runs on the church PC right after a stream).

Takes the OBS recording and:
  1. makes mark.mp4      - small 360p copy the phone app plays while marking
  2. analyses it         - loudness envelope, pauses, camera switches
  3. uploads mark.mp4 + writes sources/<tag>.json   (marking can start now)
  4. makes source_NNN.mp4 - 30-minute segments at full quality for rendering
  5. uploads them, marks the source as ready
  6. optionally shuts the PC down

Needs: ffmpeg + ffprobe on PATH, GitHub CLI (gh) signed in to the church account
(run `gh auth login` once), Python 3.10+, numpy (pip install numpy).

Examples
  python pack_and_ship.py --file "D:\\NRSC-Recordings\\2026-11-01_main.mp4"
  python pack_and_ship.py --auto --shutdown          # newest unshipped recording, then power off
  python pack_and_ship.py --file ... --source-height 1080 --range 2:00:00-3:10:00

Config lives in config.json next to this script (see config.example.json).
"""
import argparse
import base64
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "config.json").read_text()) if (HERE / "config.json").exists() else {}
REPO = CFG.get("repo", "Jessesebutinde/nrsc-shorts")
RECORDINGS = Path(CFG.get("recordings_dir", "D:/NRSC-Recordings"))
WORK = Path(CFG.get("work_dir", str(HERE / "work")))
SEGMENT = int(CFG.get("segment_seconds", 1800))
ENV_PER_SEC = 2


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def run(cmd, capture=False, check=True):
    log("$", " ".join(str(c) for c in cmd)[:300])
    r = subprocess.run([str(c) for c in cmd], text=True, stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed:\n{r.stderr[-2000:]}")
    return r


def gh(*args, check=True, capture=True):
    cmd = ["gh", *args]
    if args and args[0] == "release":
        cmd += ["-R", REPO]
    return run(cmd, capture=capture, check=check)


def probe(path):
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,codec_name",
             "-show_entries", "format=duration", "-of", "json", path], capture=True)
    j = json.loads(r.stdout)
    st = j["streams"][0]
    return int(st["width"]), int(st["height"]), st.get("codec_name", ""), float(j["format"]["duration"])


def hms(s):
    s = int(s)
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def parse_time(t):
    parts = [float(p) for p in t.split(":")]
    return sum(v * 60 ** i for i, v in enumerate(reversed(parts)))


_NVENC = None


def have_nvenc():
    """True only if a tiny NVENC test encode really works (ffmpeg lists the encoder even without a GPU)."""
    global _NVENC
    if _NVENC is None:
        r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "nullsrc=s=256x256:d=0.2",
                            "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True)
        _NVENC = r.returncode == 0 and not CFG.get("no_nvenc", False)
        log("NVIDIA encoder:", "yes" if _NVENC else "no, using libx264")
    return _NVENC


def video_codec(quality_crf, bitrate_k=None):
    """libx264 by default; NVIDIA NVENC when present (much faster on the church laptop)."""
    if have_nvenc():
        args = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(quality_crf + 2)]
        if bitrate_k:
            args += ["-b:v", f"{bitrate_k}k", "-maxrate", f"{int(bitrate_k * 1.3)}k"]
        return args
    args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(quality_crf)]
    if bitrate_k:
        args += ["-maxrate", f"{int(bitrate_k * 1.3)}k", "-bufsize", f"{bitrate_k * 2}k"]
    return args


# ----------------------------------------------------------------------------- steps

def make_mark(src, out):
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
         "-vf", "scale=-2:360", *video_codec(30, 400), "-r", "30",
         "-c:a", "aac", "-b:a", "48k", "-ac", "1", "-movflags", "+faststart", out])


def analyse(mark):
    """Loudness envelope (2 per second, base36), pauses and camera switches, all from the small copy."""
    log("analysing audio")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mark), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    n = ENV_PER_SEC
    try:
        import numpy as np
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        step = 8000 // n
        m = len(x) // step
        rms = np.sqrt((x[:m * step].reshape(m, step) ** 2).mean(axis=1))
        db = 20 * np.log10(rms + 1e-6)
        lv = np.clip((db + 50) / 50 * 35, 0, 35).astype(int)   # -50 dB .. 0 dB -> 0..35
        env = "".join("0123456789abcdefghijklmnopqrstuvwxyz"[int(v)] for v in lv)
    except ImportError:
        env = ""
        log("numpy missing: no loudness strip (pip install numpy)")

    log("finding pauses")
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(mark), "-af", "silencedetect=n=-35dB:d=0.4", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    starts = [float(v) for v in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(v) for v in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    pauses = [[round(s, 2), round(e, 2)] for s, e in zip(starts, ends)][:6000]

    log("finding camera switches")
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(mark), "-vf", "select='gt(scene,0.35)',showinfo", "-an", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    scenes = [round(float(v), 2) for v in re.findall(r"pts_time:([\d.]+)", r.stderr)][:4000]
    return env, pauses, scenes


def make_segments(src, out_dir, height, rng=None):
    """Full-quality source in SEGMENT-second pieces named by absolute segment index,
    so a clip at second t always lives in segment floor(t / SEGMENT)."""
    w, h, codec, dur = probe(src)
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = (rng if rng else (0, dur))
    first, last = int(start // SEGMENT), int(math.ceil(end / SEGMENT)) - 1
    made = []
    for s in range(first, last + 1):
        t0, t1 = s * SEGMENT, min((s + 1) * SEGMENT, dur)
        if t1 - t0 < 1:
            continue
        out = out_dir / f"source_{s:03d}.mp4"
        if out.exists():
            made.append(out); continue
        vf = f"scale=-2:{height}" if h > height else "null"
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t0:.3f}", "-to", f"{t1:.3f}", "-i", src,
             "-vf", vf, *video_codec(23, 4500 if height >= 1080 else 2500), "-r", "30",
             "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-movflags", "+faststart", out])
        made.append(out)
    return made, dur


def write_source_json(tag, data):
    path = f"sources/{tag}.json"
    content = base64.b64encode(json.dumps(data).encode("utf-8")).decode()
    r = gh("api", f"repos/{REPO}/contents/{path}", check=False)
    body = {"message": f"source {tag}", "content": content}
    if r.returncode == 0:
        body["sha"] = json.loads(r.stdout)["sha"]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(body, f); tmp = f.name
    try:
        gh("api", "-X", "PUT", f"repos/{REPO}/contents/{path}", "--input", tmp)
    finally:
        os.unlink(tmp)


def ship(file, tag, title, height, rng, shutdown):
    file = Path(file)
    date = re.search(r"(\d{4}-\d{2}-\d{2})", tag).group(1) if re.search(r"\d{4}-\d{2}-\d{2}", tag) else time.strftime("%Y-%m-%d")
    service = tag.replace(f"src-{date}-", "")
    wd = WORK / tag
    wd.mkdir(parents=True, exist_ok=True)
    w, h, codec, dur = probe(file)
    log(f"{file.name}: {w}x{h} {codec}, {hms(dur)}")

    mark = wd / "mark.mp4"
    if not mark.exists():
        log("making the marking copy (360p)")
        make_mark(file, mark)
    env, pauses, scenes = analyse(mark)

    log("uploading the marking copy")
    if gh("release", "view", tag, check=False).returncode != 0:
        gh("release", "create", tag, "--title", title, "--notes", f"Service recording {date} ({service}). Deleted automatically after 14 days.")
    gh("release", "upload", tag, str(mark), "--clobber")
    meta = {"tag": tag, "title": title, "date": date, "service": service, "duration": round(dur, 2),
            "segment_seconds": SEGMENT, "source_height": height, "source_ready": False,
            "range": [rng[0], rng[1]] if rng else None, "pauses": pauses, "scenes": scenes, "env": env}
    write_source_json(tag, meta)
    log("marking can start on the phones now")

    log(f"making {height}p source segments" + (f" for {hms(rng[0])}-{hms(rng[1])}" if rng else ""))
    segs, _ = make_segments(file, wd, height, rng)
    for s in segs:
        log(f"uploading {s.name} ({s.stat().st_size / 1e6:.0f} MB)")
        gh("release", "upload", tag, str(s), "--clobber")
    meta["source_ready"] = True
    meta["segment_count"] = len(segs)
    write_source_json(tag, meta)
    log("DONE - source ready for rendering")
    shutil.rmtree(wd, ignore_errors=True)
    if shutdown:
        log("shutting down in 60 s")
        if os.name == "nt":
            subprocess.run(["shutdown", "/s", "/t", "60"])
        else:
            subprocess.run(["shutdown", "-h", "+1"])


def tag_from_file(file):
    """2026-11-01_main.mp4 -> src-2026-11-01-main ; falls back to the file's date."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})[_ -]+([A-Za-z0-9-]+)", Path(file).stem)
    if m:
        return f"src-{m.group(1)}-{m.group(2).lower()}"
    d = time.strftime("%Y-%m-%d", time.localtime(Path(file).stat().st_mtime))
    return f"src-{d}-{re.sub(r'[^a-z0-9]+', '-', Path(file).stem.lower()).strip('-')}"


def newest_unshipped():
    files = sorted(RECORDINGS.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        files = sorted(RECORDINGS.glob("*.mkv"), key=lambda p: p.stat().st_mtime, reverse=True)
    r = gh("api", f"repos/{REPO}/contents/sources", check=False)
    shipped = {x["name"].replace(".json", "") for x in json.loads(r.stdout)} if r.returncode == 0 else set()
    for f in files:
        if tag_from_file(f) in shipped:
            continue
        # wait until OBS has finished writing it
        size = -1
        while f.stat().st_size != size:
            size = f.stat().st_size; time.sleep(30)
        return f
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", help="the OBS recording (mp4 or mkv)")
    ap.add_argument("--auto", action="store_true", help="pick the newest recording that has not been shipped")
    ap.add_argument("--tag", help="override, e.g. src-2026-11-01-main")
    ap.add_argument("--title", help="shown in the app, e.g. 'Sunday main service'")
    ap.add_argument("--source-height", type=int, default=int(CFG.get("source_height", 720)), help="720 (default) or 1080")
    ap.add_argument("--range", help="only ship full quality for this part, e.g. 2:00:00-3:10:00 (marking copy is always the whole stream)")
    ap.add_argument("--shutdown", action="store_true", help="power the PC off when done")
    args = ap.parse_args()

    if args.auto:
        f = newest_unshipped()
        if not f:
            log("nothing new to ship"); return
        args.file = str(f)
    if not args.file:
        ap.error("give --file or --auto")
    tag = args.tag or tag_from_file(args.file)
    title = args.title or tag.replace("src-", "").replace("-", " ")
    rng = None
    if args.range:
        a, b = args.range.split("-")
        rng = (parse_time(a), parse_time(b))
    ship(args.file, tag, title, args.source_height, rng, args.shutdown)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log("ERROR:", e)
        sys.exit(1)
