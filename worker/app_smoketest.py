#!/usr/bin/env python3
"""
Smoke-test the phone app in headless Chrome with a fake GitHub behind it.

    python3 worker/app_smoketest.py        (needs: pip install playwright, ffmpeg, Google Chrome)
"""
import base64
import http.server
import json
import re
import subprocess
import threading
from functools import partial
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "work-apptest"
OUT.mkdir(exist_ok=True)
TAG = "src-2026-11-01-main"

# fake repo contents: path -> text
repo = {}
puts = []


def seed():
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=120",
                    "-f", "lavfi", "-i", "sine=f=440:d=120", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                    "-c:a", "aac", "-b:a", "48k", "-movflags", "+faststart", "-shortest", str(OUT / "mark.mp4")], check=True)
    env = "".join("0123456789abcdefghijklmnopqrstuvwxyz"[(i * 7) % 36] for i in range(240))
    repo[f"sources/{TAG}.json"] = json.dumps({"tag": TAG, "title": "Sunday main", "date": "2026-11-01", "service": "main",
                                              "duration": 120, "segment_seconds": 1800, "segment_count": 1, "source_ready": True,
                                              "pauses": [[9.5, 10.2], [20.0, 20.8], [41.0, 41.6]], "scenes": [15.0, 30.0], "env": env})


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    """Serves docs/ plus the test video, with HTTP Range support so <video> can seek."""
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/media/"):
            f = OUT / self.path.split("/media/", 1)[1]
            data = f.read_bytes()
            rng = self.headers.get("Range")
            if rng:
                m = re.match(r"bytes=(\d*)-(\d*)", rng)
                a = int(m.group(1) or 0); b = int(m.group(2) or len(data) - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {a}-{b}/{len(data)}")
                chunk = data[a:b + 1]
            else:
                self.send_response(200); chunk = data
            self.send_header("Accept-Ranges", "bytes"); self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(chunk))); self.end_headers()
            self.wfile.write(chunk)
            return
        super().do_GET()


def serve_docs():
    handler = partial(RangeHandler, directory=str(ROOT / "docs"))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8765), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def fake_github(route, request):
    url = request.url
    m = url.split("api.github.com", 1)[1].split("?")[0]
    if m == "/repos/Jessesebutinde/nrsc-shorts":
        return route.fulfill(json=({"full_name": "Jessesebutinde/nrsc-shorts", "permissions": {"push": True}}))
    if m.startswith("/repos/Jessesebutinde/nrsc-shorts/contents/"):
        path = m.split("/contents/", 1)[1]
        if request.method == "GET":
            if path in repo:
                return route.fulfill(json={"type": "file", "name": path.split("/")[-1], "sha": "abc",
                                           "content": base64.b64encode(repo[path].encode()).decode()})
            listing = [{"type": "file", "name": p.split("/")[-1]} for p in repo if p.startswith(path + "/")]
            if listing or path in ("recipes", "status", "sources"):
                return route.fulfill(json=listing)
            return route.fulfill(status=404, json={"message": "Not Found"})
        if request.method == "PUT":
            body = request.post_data_json
            repo[path] = base64.b64decode(body["content"]).decode()
            puts.append((path, body))
            return route.fulfill(json={"content": {"sha": "new"}})
        if request.method == "DELETE":
            repo.pop(path, None)
            return route.fulfill(json={})
    return route.fulfill(status=404, json={"message": f"unhandled {m}"})


def fake_video(route, request):
    """Answer Range requests like a real file host, so the <video> can seek."""
    data = (OUT / "mark.mp4").read_bytes()
    rng = request.headers.get("range")
    if rng:
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        a = int(m.group(1) or 0); b = int(m.group(2) or len(data) - 1)
        return route.fulfill(status=206, body=data[a:b + 1], headers={
            "Content-Type": "video/mp4", "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {a}-{b}/{len(data)}", "Content-Length": str(b - a + 1)})
    return route.fulfill(status=200, body=data, headers={"Content-Type": "video/mp4", "Accept-Ranges": "bytes",
                                                         "Content-Length": str(len(data))})


def main():
    seed()
    serve_docs()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--autoplay-policy=no-user-gesture-required"])
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.route("https://api.github.com/**", fake_github)
        page.route("https://github.com/**/releases/download/**", fake_video)
        page.goto("http://127.0.0.1:8765/index.html")

        # settings
        page.fill("#cfg-owner", "Jessesebutinde"); page.fill("#cfg-repo", "nrsc-shorts")
        page.fill("#cfg-token", "github_pat_test"); page.fill("#cfg-name", "Jesse")
        page.click("#cfg-test"); page.wait_for_selector("#cfg-msg.ok")
        page.click("#cfg-save")
        page.wait_for_selector("#lib-list .card")
        page.screenshot(path=str(OUT / "1_library.png"))

        # open source → new clip
        page.click("#lib-list .card")
        page.wait_for_selector("#clips-new")
        page.click("#clips-new")
        page.wait_for_selector("#s-mark:not([hidden])")
        page.wait_for_function("document.querySelector('#video').readyState >= 1")
        page.evaluate("document.querySelector('#video').currentTime = 10.4")
        page.wait_for_timeout(400)
        page.click("#mark-in")
        page.evaluate("document.querySelector('#video').currentTime = 25.0")
        page.wait_for_timeout(400)
        page.click("#mark-out")
        page.click("#snap")                 # OUT 25.0 -> nearest pause [20.0,20.8] is 4.2 away: no snap
        page.evaluate("document.querySelector('#video').currentTime = 41.2")
        page.wait_for_timeout(300)
        page.click("#mark-out"); page.click("#snap")   # snaps OUT to 41.3
        page.click("[data-nudge='0.5']")
        page.click("#frame-here")
        # second clip after the first one's OUT: IN auto-starts a new clip
        page.evaluate("document.querySelector('#video').currentTime = 60.0")
        page.wait_for_timeout(300)
        page.click("#mark-in")
        page.evaluate("document.querySelector('#video').currentTime = 75.5")
        page.wait_for_timeout(300)
        page.click("#mark-out")
        page.click("#look")
        page.screenshot(path=str(OUT / "2_mark.png"))

        page.click("#mark-next"); page.wait_for_selector("#s-arrange:not([hidden])")
        page.click("#arr-list .card:nth-child(2) button[title='Up']")
        page.screenshot(path=str(OUT / "3_arrange.png"))
        page.click("#arr-next"); page.wait_for_selector("#s-joins:not([hidden])")
        page.click(".joinbtn"); page.click(".joinpick button:nth-child(3)")   # J-cut
        page.screenshot(path=str(OUT / "4_joins.png"))
        page.click("#join-next"); page.wait_for_selector("#s-confirm:not([hidden])")
        page.fill("#conf-title", "You are not the one holding God")
        page.click("#conf-draft")
        page.wait_for_function("document.querySelectorAll('#conf-status .st-stage').length && document.querySelector('#conf-status .st-stage').textContent === 'Queued'")
        page.screenshot(path=str(OUT / "5_confirm.png"))

        rid, body = puts[-1]
        recipe = json.loads(base64.b64decode(body["content"]).decode())
        print("saved recipe:", json.dumps(recipe, indent=1))
        assert rid.startswith("recipes/2026-11-01-main__")
        assert recipe["want"] == "draft" and recipe["rev"] == 1 and len(recipe["clips"]) == 2
        c0, c1 = recipe["clips"]
        assert c0["in"] == 60.0 and c0["out"] == 75.5 and c0["look"] == "stage", c0      # moved up
        assert c0["join_after"]["type"] == "jcut" and c0["join_after"]["lead"] == 1.5
        assert c1["in"] == 10.4 and abs(c1["out"] - 41.8) < 0.01 and "join_after" not in c1, c1
        assert c1["frame"] and c1["frame"][0]["t"] == 0, c1

        # the workflow writes a status: the app should pick it up and show the draft
        repo[f"status/{recipe['id']}.json"] = json.dumps({"id": recipe["id"], "rev": 1, "stage": "draft_ready",
                                                            "files": {"draft": "https://github.com/Jessesebutinde/nrsc-shorts/releases/download/clips-2026-11/x_draft.mp4"},
                                                            "message": "Draft ready."})
        page.wait_for_function("document.querySelector('#conf-status .st-stage').textContent === 'Draft ready'", timeout=15000)
        page.wait_for_selector("#conf-draftbox:not([hidden])")
        page.screenshot(path=str(OUT / "6_draft.png"))
        print("page errors:", errors)
        assert not errors, errors
        browser.close()
    print("APP SMOKETEST PASSED")


if __name__ == "__main__":
    main()
