#!/usr/bin/env python3
"""NRSC Clips box worker: polls jobs/*.json in the repo, cuts YouTube sections with yt-dlp + ffmpeg
on this box, uploads results as a GitHub Release, and writes status/jobs/<id>.json for the app."""
import base64, json, os, shutil, subprocess, sys, time, traceback, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

REPO = "Jessesebutinde/nrsc-shorts"
WORK = Path("/workspace/nrsc-shorts/work"); WORK.mkdir(parents=True, exist_ok=True)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
YTDLP = [sys.executable, "-m", "yt_dlp"]
CLIENTS = [None, "web_safari", "tv", "mweb", "android", "ios"]

def log(*a):
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)

GHENV = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GH_TOKEN")}

def gh(method, path, body=None, raw=None, ctype=None, ok404=False):
    """GitHub API via the box's `gh` login (no token handled here)."""
    cmd = ["gh", "api", "-X", method, "-H", "Accept: application/vnd.github+json", path]
    inp = None
    if raw is not None:
        cmd[3:3] = ["-H", "Content-Type: " + (ctype or "application/octet-stream")]; cmd += ["--input", "-"]; inp = raw
    elif body is not None:
        cmd += ["--input", "-"]; inp = json.dumps(body).encode()
    for attempt in range(4):
        p = subprocess.run(cmd, input=inp, capture_output=True, env=GHENV)
        if p.returncode == 0:
            return json.loads(p.stdout) if p.stdout.strip() else None
        err = (p.stderr + p.stdout).decode(errors="ignore")
        if "404" in err and ok404: return None
        if attempt < 3 and not ("401" in err or "403" in err): time.sleep(4 * (attempt + 1)); continue
        raise RuntimeError(f"GitHub {method} {path.split('?')[0]}: {err[-250:]}")

def put_file(path, obj, msg):
    cur = gh("GET", f"/repos/{REPO}/contents/{path}", ok404=True)
    body = {"message": msg, "content": base64.b64encode(json.dumps(obj, indent=2).encode()).decode()}
    if cur: body["sha"] = cur["sha"]
    gh("PUT", f"/repos/{REPO}/contents/{path}", body)

def status(jid, state, message, **kw):
    s = {"id": jid, "state": state, "message": message, "updated": datetime.now(timezone.utc).isoformat(), **kw}
    put_file(f"status/jobs/{jid}.json", s, f"status {jid}: {state}"); log(jid, state, message)

def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout)[-600:])
    return p

def download(url, a, b, out):
    a = max(0, a - 1)  # 1s pre-roll so the cut lands on a clean frame
    last = ""
    for c in CLIENTS:
        for f in out.parent.glob(out.stem + ".*"): f.unlink()
        cmd = YTDLP + ["--no-playlist", "--no-progress", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
                       "--download-sections", f"*{a:.2f}-{b + 1:.2f}", "--merge-output-format", "mkv",
                       "-o", str(out.with_suffix(".%(ext)s")), url]
        if c: cmd[3:3] = ["--extractor-args", f"youtube:player_client={c}"]
        p = subprocess.run(cmd, capture_output=True, text=True)
        got = [f for f in out.parent.glob(out.stem + ".*") if f.suffix != ".part"]
        if p.returncode == 0 and got: return got[0], a
        last = (p.stderr or "")[-400:]; log("yt-dlp failed with client", c, last.strip().splitlines()[-1:] )
    if "Sign in to confirm" in last or "bot" in last: raise RuntimeError("YouTube blocked the download (bot check). " + last[-200:])
    raise RuntimeError("Download failed: " + last[-300:])

def render(src, off, start, end, job, out):
    dur = end - start; ss = start - off
    vert = job.get("format", "vertical") == "vertical"
    if vert and job.get("style") == "crop":
        v = "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[v]"
    elif vert:
        v = ("[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:2[bg];"
             "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]")
    else:
        v = "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1[v]"
    if job.get("title"):
        tf = out.with_suffix(".txt"); tf.write_text(job["title"])
        y = "180" if vert else "60"; fs = 64 if vert else 54
        v = v[:-3] + f"[v0];[v0]drawtext=fontfile={FONT}:textfile={tf}:fontsize={fs}:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=24:x=(w-text_w)/2:y={y}[v]"
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ss:.2f}", "-i", str(src), "-t", f"{dur:.2f}",
         "-filter_complex", v, "-map", "[v]", "-map", "0:a?", "-r", "30", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
         "-movflags", "+faststart", str(out)])

def safe(s):
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in s.strip())[:40].strip("-") or "clip"

def process(job):
    jid = job["id"]; d = WORK / jid; shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
    clips = job["clips"]; outs = []
    for i, c in enumerate(clips, 1):
        status(jid, "working", f"Downloading clip {i} of {len(clips)}…")
        src, off = download(job["url"], c["start"], c["end"], d / f"src{i}")
        status(jid, "working", f"Cutting clip {i} of {len(clips)}…")
        o = d / f"{i:02d}-{safe(c['name'])}.mp4"; render(src, off, c["start"], c["end"], job, o); outs.append((c["name"], o))
    if job.get("join") and len(outs) > 1:
        status(jid, "working", "Joining clips into one highlight…")
        lst = d / "list.txt"; lst.write_text("".join(f"file '{o}'\n" for _, o in outs))
        h = d / "00-Highlight.mp4"
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(h)])
        outs.insert(0, ("Highlight (all clips)", h))
    status(jid, "working", "Uploading…")
    tag = "clips-" + jid
    rel = gh("GET", f"/repos/{REPO}/releases/tags/{tag}", ok404=True) or gh("POST", f"/repos/{REPO}/releases",
          {"tag_name": tag, "name": f"Clips {jid}", "body": f"{job.get('title') or ''}\nSource: {job['url']}\nBy: {job.get('by')}", "prerelease": True})
    have = {a["name"]: a for a in rel.get("assets", [])}
    results = []
    for name, o in outs:
        if o.name not in have:
            up = rel["upload_url"].split("{")[0].replace("https://api.github.com", "https://uploads.github.com") + "?name=" + o.name
            a = gh("POST", up, raw=o.read_bytes(), ctype="video/mp4")
        else: a = have[o.name]
        results.append({"name": name, "file": o.name, "url": a["browser_download_url"], "size": o.stat().st_size})
    status(jid, "done", "Ready", outputs=results)
    shutil.rmtree(d, ignore_errors=True)

def pending():
    files = gh("GET", f"/repos/{REPO}/contents/jobs", ok404=True) or []
    done = {f["name"] for f in (gh("GET", f"/repos/{REPO}/contents/status/jobs", ok404=True) or [])}
    out = []
    for f in sorted(files, key=lambda f: f["name"]):
        if not f["name"].endswith(".json"): continue
        if f["name"] in done:
            s = json.loads(base64.b64decode(gh("GET", f"/repos/{REPO}/contents/status/jobs/{f['name']}")["content"]))
            if s["state"] in ("done", "failed") or s.get("attempts", 0) >= 3: continue
            if s["state"] == "working" and time.time() - datetime.fromisoformat(s["updated"]).timestamp() < 1800: pass
        out.append(f["name"][:-5])
    return out

def update_ytdlp():
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "--user", "--break-system-packages", "yt-dlp[default]"], capture_output=True)
    log("yt-dlp", subprocess.run(YTDLP + ["--version"], capture_output=True, text=True).stdout.strip())

def main():
    update_ytdlp(); last_up = time.time(); tries = {}
    log("worker started, polling every 15s")
    while True:
        try:
            if time.time() - last_up > 86400: update_ytdlp(); last_up = time.time()
            for jid in pending():
                job = json.loads(base64.b64decode(gh("GET", f"/repos/{REPO}/contents/jobs/{jid}.json")["content"]))
                job["id"] = jid; tries[jid] = tries.get(jid, 0) + 1
                try: process(job)
                except Exception as e:
                    log(jid, "error", traceback.format_exc()[-800:])
                    final = tries[jid] >= 3 or "bot check" in str(e)
                    status(jid, "failed" if final else "queued", (str(e)[:300] if final else f"Retrying ({tries[jid]}/3)…"), attempts=tries[jid])
                    if not final: time.sleep(20)
        except Exception:
            log("poll error", traceback.format_exc()[-500:]); time.sleep(30)
        time.sleep(15)

if __name__ == "__main__":
    main()
