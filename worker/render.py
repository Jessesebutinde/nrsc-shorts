#!/usr/bin/env python3
"""
NRSC Shorts - render worker.

Reads every recipe in recipes/, renders the ones whose status is behind,
uploads the result to a GitHub Release and writes status/<id>.json.

Runs in GitHub Actions (default) or on any PC with ffmpeg + gh installed:

    python3 worker/render.py                      # everything that needs work
    python3 worker/render.py --recipe recipes/x.json --local-src work/src --no-upload --no-git

Recipe format (written by the phone app, see README):
{
  "id": "src-2026-11-01-main-k3f9a2",
  "rev": 3,                   # app bumps this on every save
  "want": "draft" | "final",
  "source": "src-2026-11-01-main",
  "title": "You are not the one holding God",
  "outputs": ["whatsapp_long", "whatsapp_saver", "tiktok"],
  "clips": [
    {"in": 2472.4, "out": 2518.9, "frame_x": 0.5, "look": "tight",
     "join_after": {"type": "jcut", "lead": 1.5}},
    {"in": 2601.1, "out": 2644.0, "frame": [{"t": 0, "x": 0.5}, {"t": 20.5, "x": 0.3}],
     "join_after": {"type": "fade", "dur": 0.5}},
    {"in": 3010.0, "out": 3048.3}
  ]
}
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPES = ROOT / "recipes"
STATUS = ROOT / "status"
SOURCES = ROOT / "sources"
WORK = Path(os.environ.get("NRSC_WORK", ROOT / "work"))
REPO = os.environ.get("GH_REPO", "")

FPS = 30
DEFAULT_SEGMENT = 1800  # seconds per source segment (pack_and_ship uses the same)
JOIN_DEFAULTS = {"fade": 0.5, "dip": 0.6, "jcut": 1.5, "lcut": 1.5}

# Output presets. Sizes are the vertical 9:16 canvas.
PRESETS = {
    "draft": {"w": 540, "h": 960, "suffix": "draft",
              "v": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "30"], "a": ["-b:a", "64k"]},
    "whatsapp_long": {"w": 720, "h": 1280, "suffix": "WhatsApp_HD",
                      "v": ["-c:v", "libx264", "-preset", "medium", "-b:v", "2500k", "-maxrate", "2800k", "-bufsize", "5600k"],
                      "a": ["-b:a", "128k"]},
    "whatsapp_saver": {"w": 540, "h": 960, "suffix": "WhatsApp_Saver",
                       "v": ["-c:v", "libx264", "-preset", "medium", "-b:v", "1100k", "-maxrate", "1300k", "-bufsize", "2600k"],
                       "a": ["-b:a", "96k"]},
    "tiktok": {"w": 1080, "h": 1920, "suffix": "TikTok_1080",
               "v": ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-maxrate", "8M", "-bufsize", "16M"],
               "a": ["-b:a", "160k"]},
    # WhatsApp Status: 30 s max, must stay under 16 MB. Bitrate is worked out from the length.
    "status": {"w": 720, "h": 1280, "suffix": "WhatsApp_Status", "v": None, "a": ["-b:a", "96k"]},
}


# ----------------------------------------------------------------------------- helpers

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def run(cmd, check=True, capture=False):
    log("$", " ".join(str(c) for c in cmd)[:400])
    res = subprocess.run([str(c) for c in cmd], text=True,
                         stdout=subprocess.PIPE if capture else None,
                         stderr=subprocess.PIPE)
    if check and res.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({res.returncode}):\n{res.stderr[-3000:]}")
    return res


def ffprobe(path):
    res = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height", "-show_entries", "format=duration",
               "-of", "json", path], capture=True)
    j = json.loads(res.stdout)
    st = j["streams"][0]
    return int(st["width"]), int(st["height"]), float(j["format"]["duration"])


def even(n):
    n = int(round(n))
    return n if n % 2 == 0 else n - 1


def slugify(s, limit=40):
    s = re.sub(r"[^A-Za-z0-9]+", "-", s or "").strip("-")
    return (s[:limit].rstrip("-")) or "clip"


def fmt_len(seconds):
    return f"{int(round(seconds))}s" if seconds < 60 else f"{int(round(seconds / 60))}min"


def read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


# ----------------------------------------------------------------------------- planning

def plan_pieces(clips, seg_len):
    """Split clips at segment boundaries. Returns a list of pieces in order:
    {clip, seg, vin, vout, ain, aout, t0 (seconds into the clip where this piece starts),
     join (type to use AFTER this piece), join_p (parameters)}"""
    pieces = []
    for ci, clip in enumerate(clips):
        cin, cout = float(clip["in"]), float(clip["out"])
        if cout - cin < 0.5:
            raise ValueError(f"clip {ci + 1} is shorter than 0.5 s")
        join = (clip.get("join_after") or {}).get("type", "cut") if ci < len(clips) - 1 else "cut"
        jp = clip.get("join_after") or {}
        prev_join = (clips[ci - 1].get("join_after") or {}) if ci > 0 else {}
        # audio shifts for J / L cuts
        lead_in = float(prev_join.get("lead", JOIN_DEFAULTS["jcut"])) if prev_join.get("type") == "jcut" else 0.0
        lag_in = float(prev_join.get("lag", JOIN_DEFAULTS["lcut"])) if prev_join.get("type") == "lcut" else 0.0
        lead_out = float(jp.get("lead", JOIN_DEFAULTS["jcut"])) if join == "jcut" else 0.0
        lag_out = float(jp.get("lag", JOIN_DEFAULTS["lcut"])) if join == "lcut" else 0.0

        t = cin
        first = True
        while t < cout - 1e-6:
            seg = int(t // seg_len)
            seg_end = (seg + 1) * seg_len
            pend = min(cout, seg_end)
            last = pend >= cout - 1e-6
            vin, vout = t - seg * seg_len, pend - seg * seg_len
            ain, aout = vin, vout
            if first:
                ain = max(0.0, vin - lead_in + lag_in)      # J-cut before: start audio early; L-cut before: start audio late
            if last:
                aout = min(seg_len, vout - lead_out + lag_out)  # J-cut after: end audio early; L-cut after: end audio late
            pieces.append({
                "clip": ci, "seg": seg, "vin": vin, "vout": vout, "ain": ain, "aout": aout,
                "t0": t - cin, "join": join if last else "cut", "join_p": jp if last else {},
                "look": clip.get("look", "tight"), "frame_x": clip.get("frame_x", 0.5),
                "frame": clip.get("frame"),
            })
            t = pend
            first = False
    return pieces


def frame_expr(piece, cw, W):
    """Crop x position for the 9:16 window, as a number or an ffmpeg expression over t."""
    span = W - cw
    pts = piece.get("frame")
    if not pts:
        return str(int(round(span * float(piece.get("frame_x", 0.5)))))
    pts = sorted(({"t": float(p["t"]) - piece["t0"], "x": float(p["x"])} for p in pts), key=lambda p: p["t"])
    # value before the first point = first point's x
    expr = str(int(round(span * pts[0]["x"])))
    for p in pts:
        if p["t"] <= 0:
            expr = str(int(round(span * p["x"])))
            continue
        expr = f"if(lt(t,{p['t']:.3f}),{expr},{int(round(span * p['x']))})"
    return expr


def build_graph(pieces, seg_files, W, H):
    """Return (ffmpeg input args, filter_complex string, total duration)."""
    inputs = []
    chains = []
    v_labels, a_labels, vdur, adur = [], [], [], []
    for i, p in enumerate(pieces):
        f = seg_files[p["seg"]]
        sw, sh, _ = ffprobe(f)
        inputs += ["-ss", f"{p['vin']:.3f}", "-to", f"{p['vout']:.3f}", "-i", f]
        inputs += ["-ss", f"{p['ain']:.3f}", "-to", f"{p['aout']:.3f}", "-i", f]
        vi, ai = 2 * i, 2 * i + 1
        if p["look"] == "stage":
            chains.append(
                f"[{vi}:v]fps={FPS},split[b{i}][f{i}];"
                f"[b{i}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=20[bg{i}];"
                f"[f{i}]scale={W}:-2[fg{i}];"
                f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,format=yuv420p,setsar=1,settb=AVTB,setpts=PTS-STARTPTS[v{i}]")
        else:
            cw = even(sh * 9 / 16)
            x = frame_expr(p, cw, sw)
            chains.append(
                f"[{vi}:v]fps={FPS},crop={cw}:{sh}:'{x}':0,scale={W}:{H},format=yuv420p,setsar=1,settb=AVTB,setpts=PTS-STARTPTS[v{i}]")
        ad = p["aout"] - p["ain"]
        chains.append(
            f"[{ai}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS,"
            f"afade=t=in:d=0.02,afade=t=out:st={max(0.0, ad - 0.02):.3f}:d=0.02[a{i}]")
        v_labels.append(f"v{i}"); a_labels.append(f"a{i}")
        vdur.append(p["vout"] - p["vin"]); adur.append(ad)

    cv, ca, cvd, cad = v_labels[0], a_labels[0], vdur[0], adur[0]
    for i in range(1, len(pieces)):
        j = pieces[i - 1]["join"]
        jp = pieces[i - 1]["join_p"]
        if j in ("fade", "dip"):
            d = float(jp.get("dur", JOIN_DEFAULTS[j]))
            d = max(0.1, min(d, cvd - 0.1, vdur[i] - 0.1))
            tr = "fade" if j == "fade" else "fadeblack"
            chains.append(f"[{cv}][{v_labels[i]}]xfade=transition={tr}:duration={d:.3f}:offset={cvd - d:.3f}[vx{i}]")
            chains.append(f"[{ca}][{a_labels[i]}]acrossfade=d={d:.3f}[ax{i}]")
            cvd += vdur[i] - d; cad += adur[i] - d
        else:  # cut, jcut, lcut: pictures and sounds are simply joined; the J/L shift already sits in the audio windows
            chains.append(f"[{cv}][{v_labels[i]}]concat=n=2:v=1:a=0[vx{i}]")
            chains.append(f"[{ca}][{a_labels[i]}]concat=n=2:v=0:a=1[ax{i}]")
            cvd += vdur[i]; cad += adur[i]
        cv, ca = f"vx{i}", f"ax{i}"
    chains.append(f"[{ca}]loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
    chains.append(f"[{cv}]null[vout]")
    return inputs, ";".join(chains), cvd


def encode(inputs, graph, out, preset, extra=None):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
           *preset["v"], "-c:a", "aac", *preset["a"], "-ar", "48000",
           "-movflags", "+faststart", "-shortest", *(extra or []), out]
    run(cmd)


def transcode(master, out, preset, duration):
    v = preset["v"]
    if v is None:  # status preset: fit under 15 MB
        kbps = int(min(2500, max(300, (15.0 * 8000 / max(duration, 1)) - 100)))
        v = ["-c:v", "libx264", "-preset", "medium", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k"]
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", master,
         "-vf", f"scale={preset['w']}:{preset['h']}", *v, "-c:a", "aac", *preset["a"],
         "-movflags", "+faststart", out])


# ----------------------------------------------------------------------------- github

def gh(*args, check=True):
    env = dict(os.environ)
    if REPO:
        env["GH_REPO"] = REPO
    res = subprocess.run(["gh", *args], text=True, capture_output=True, env=env)
    if check and res.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {res.stderr.strip()[-1500:]}")
    return res


def fetch_segment(tag, name, dest_dir, local_src=None):
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    if dest.exists():
        return dest
    if local_src:
        src = Path(local_src) / tag / name
        if src.exists():
            shutil.copy(src, dest)
            return dest
        raise FileNotFoundError(f"{src} not found")
    res = gh("release", "download", tag, "--pattern", name, "--dir", str(dest_dir), check=False)
    if res.returncode != 0 or not dest.exists():
        raise FileNotFoundError(f"{name} is not in release {tag} yet ({res.stderr.strip()[-300:]})")
    return dest


def ensure_release(tag, title):
    if gh("release", "view", tag, check=False).returncode != 0:
        gh("release", "create", tag, "--title", title, "--notes", "Made by NRSC Shorts.")


def upload(tag, path):
    gh("release", "upload", tag, str(path), "--clobber")
    return f"https://github.com/{REPO}/releases/download/{tag}/{path.name}"


def git_push_status(msg, enabled):
    if not enabled:
        return
    run(["git", "config", "user.name", "nrsc-shorts bot"])
    run(["git", "config", "user.email", "actions@users.noreply.github.com"])
    run(["git", "add", "status"])
    if run(["git", "diff", "--cached", "--quiet"], check=False).returncode == 0:
        return
    run(["git", "commit", "-m", msg])
    for attempt in range(4):
        if run(["git", "push"], check=False).returncode == 0:
            return
        run(["git", "pull", "--rebase", "--autostash"], check=False)
        time.sleep(3)
    log("warning: could not push status")


# ----------------------------------------------------------------------------- main

def write_status(rid, data, push):
    STATUS.mkdir(exist_ok=True)
    data["id"] = rid
    data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (STATUS / f"{rid}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    git_push_status(f"status: {rid} {data.get('stage')}", push)


def needs_work(recipe, status):
    want = recipe.get("want", "draft")
    if not status or status.get("rev") != recipe.get("rev"):
        return True
    stage = status.get("stage")
    if want == "draft":
        return stage not in ("draft_ready", "final_ready")
    return stage != "final_ready"


def render(recipe, args):
    rid = recipe["id"]
    want = recipe.get("want", "draft")
    src = read_json(SOURCES / f"{recipe['source']}.json")
    if not src:
        raise RuntimeError(f"no sources/{recipe['source']}.json - has pack and ship run for this service?")
    seg_len = int(src.get("segment_seconds", DEFAULT_SEGMENT))
    pieces = plan_pieces(recipe["clips"], seg_len)
    total = sum(p["vout"] - p["vin"] for p in pieces)
    wd = WORK / rid
    wd.mkdir(parents=True, exist_ok=True)
    seg_dir = WORK / "src" / recipe["source"]
    seg_files = {}
    for s in sorted({p["seg"] for p in pieces}):
        seg_files[s] = fetch_segment(recipe["source"], f"source_{s:03d}.mp4", seg_dir, args.local_src)

    prev = read_json(STATUS / f"{rid}.json", {}) or {}
    files = dict(prev.get("files", {})) if prev.get("rev") == recipe.get("rev") else {}
    month = (src.get("date") or time.strftime("%Y-%m-%d"))[:7]
    clips_tag = f"clips-{month}"
    if not args.no_upload:
        ensure_release(clips_tag, f"Clips {month}")

    if want == "draft" or "draft" not in files:
        inputs, graph, dur = build_graph(pieces, seg_files, PRESETS["draft"]["w"], PRESETS["draft"]["h"])
        out = wd / f"{rid}_draft.mp4"
        encode(inputs, graph, out, PRESETS["draft"])
        files["draft"] = upload(clips_tag, out) if not args.no_upload else str(out)
        if want == "draft":
            return {"stage": "draft_ready", "rev": recipe.get("rev"), "files": files,
                    "duration": round(dur, 2), "message": "Draft ready. Watch it, then Make final."}

    # finals: one high quality master, then every preset from it
    inputs, graph, dur = build_graph(pieces, seg_files, 1080, 1920)
    master = wd / "master.mp4"
    encode(inputs, graph, master, {"v": ["-c:v", "libx264", "-preset", "medium", "-crf", "18"], "a": ["-b:a", "192k"]})
    date = src.get("date", "")
    base = f"Namasuba_{date}_{slugify(recipe.get('title'))}_{fmt_len(dur)}"
    sizes = {}
    for key in recipe.get("outputs") or ["whatsapp_long", "whatsapp_saver", "tiktok"]:
        preset = PRESETS.get(key)
        if not preset or key == "draft":
            continue
        if key == "status" and dur > 30.5:
            continue
        out = wd / f"{base}_{preset['suffix']}.mp4"
        transcode(master, out, preset, dur)
        sizes[key] = round(out.stat().st_size / 1e6, 1)
        files[key] = upload(clips_tag, out) if not args.no_upload else str(out)
    return {"stage": "final_ready", "rev": recipe.get("rev"), "files": files, "sizes_mb": sizes,
            "duration": round(dur, 2), "message": "Final files ready. Send the WhatsApp file as a document."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recipe", help="render only this recipe file")
    ap.add_argument("--local-src", help="folder holding <tag>/source_NNN.mp4 instead of downloading")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--no-git", action="store_true", help="do not commit/push status files")
    ap.add_argument("--force", action="store_true", help="render even if status says done")
    ap.add_argument("--keep", action="store_true", help="keep the work folder (for testing)")
    args = ap.parse_args()
    push = not args.no_git

    recipe_files = [Path(args.recipe)] if args.recipe else sorted(RECIPES.glob("*.json"))
    todo = []
    for rf in recipe_files:
        recipe = read_json(rf)
        if not recipe or "id" not in recipe or recipe.get("want") == "none":
            continue  # "none" = saved for later, nothing to render yet
        status = read_json(STATUS / f"{recipe['id']}.json")
        if args.force or needs_work(recipe, status):
            todo.append(recipe)
    log(f"{len(todo)} recipe(s) to render")
    for recipe in todo:
        rid = recipe["id"]
        write_status(rid, {"stage": "rendering", "rev": recipe.get("rev"),
                           "message": f"Rendering the {recipe.get('want', 'draft')}…"}, push)
        try:
            result = render(recipe, args)
            write_status(rid, result, push)
            log("done", rid, result["stage"])
        except FileNotFoundError as e:
            write_status(rid, {"stage": "waiting_source", "rev": recipe.get("rev"),
                               "message": f"Source video not uploaded yet: {e}. Run pack and ship on the church PC."}, push)
            log("waiting", rid, e)
        except Exception as e:  # noqa: BLE001
            write_status(rid, {"stage": "error", "rev": recipe.get("rev"), "message": str(e)[-1500:]}, push)
            log("error", rid, e)
        finally:
            if not args.keep:
                shutil.rmtree(WORK / rid, ignore_errors=True)


if __name__ == "__main__":
    main()
