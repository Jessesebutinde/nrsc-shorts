# Namasuba Shorts

Turn a church stream into clean, shareable clips (30-second shorts up to 4-minute stories) from a phone,
with the church PC switched off during the week. Everything lives in this one free GitHub repo:

| Part | Where | What it does |
| --- | --- | --- |
| **Phone app** | `docs/` → GitHub Pages | Watch the small copy, mark IN/OUT, arrange, choose joins, ask for a draft or final |
| **Recipes & status** | `recipes/`, `status/`, `sources/` | The whole "database": one small JSON file per clip, per render, per service |
| **Render worker** | `worker/render.py` + `.github/workflows/render.yml` | GitHub Actions wakes on every saved recipe, cuts with FFmpeg, uploads the files |
| **Pack and ship** | `church-pc/` | Runs on the church PC after each stream: marking copy + full-quality source → Releases |
| **Videos** | GitHub Releases | `src-<date>-<service>` holds the source (deleted after 14 days); `clips-<month>` holds finished clips |

The phone never touches the heavy video. It writes a recipe; GitHub does the cooking.

## Set up once (20 minutes, all from a phone browser except the church PC part)

1. **Create this repo** on GitHub as `nrsc-shorts`, **public** (public repos get GitHub Actions free with no monthly cap)
   and push these files to the `main` branch.
2. **Turn on Pages:** repo → Settings → Pages → Source *Deploy from a branch* → Branch `main`, folder `/docs` → Save.
   After a minute the app is at `https://<owner>.github.io/nrsc-shorts/`. Add it to your home screen.
3. **Allow the workflow to write:** repo → Settings → Actions → General → Workflow permissions →
   *Read and write permissions* → Save. (It needs this to upload clips and write status files.)
4. **Make a token for each phone:** github.com → Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate. Repository access: *Only select repositories* → `nrsc-shorts`.
   Permissions → Repository → **Contents: Read and write**. Copy it into the app's Settings screen.
   Brian needs to be a collaborator on the repo (Settings → Collaborators) and makes his own token.
5. **Church PC:** follow `church-pc/README.md`.

## Using it

1. Sunday: the church PC ships the recording by itself (or someone runs the script). The service appears in the app's Library.
2. Open the service → **New clip** → play, tap **IN** and **OUT**. Nudge by ±0.1 s, **Snap to pause** to land on a breath.
   Drag the **frame** slider so the gold 9:16 window follows the preacher; **Frame here** adds a frame point for long clips
   where the camera switches; **Stage** keeps the whole picture with a blurred background instead.
3. **Arrange** the pieces, choose the **Joins** (cut, fade, J-cut, L-cut, dip to black), **Confirm** → **Make draft**.
4. 1–3 minutes later the draft plays in the app. **Make final** gives the WhatsApp HD, data-saver and TikTok/Shorts files.
5. Download the WhatsApp file and send it as a **document** (paperclip → Document) so WhatsApp does not squash it.

## Recipe format

```json
{
  "id": "2026-11-01-main__k3f9a2",
  "rev": 3,
  "want": "draft",
  "source": "src-2026-11-01-main",
  "title": "You are not the one holding God",
  "outputs": ["whatsapp_long", "whatsapp_saver", "tiktok"],
  "clips": [
    { "in": 2472.4, "out": 2518.9, "frame_x": 0.5, "look": "tight", "join_after": { "type": "jcut", "lead": 1.5 } },
    { "in": 2601.1, "out": 2644.0, "frame": [ { "t": 0, "x": 0.5 }, { "t": 20.5, "x": 0.3 } ], "join_after": { "type": "fade", "dur": 0.5 } },
    { "in": 3010.0, "out": 3048.3, "look": "stage" }
  ]
}
```

Times are seconds into the service recording. `want` is `none` (saved), `draft` or `final`. The app bumps `rev`
on every save; the worker renders when the status file is behind the recipe.

## Output presets (worker/render.py)

| Preset | Picture | Bitrate | 4-minute file |
| --- | --- | --- | --- |
| draft | 540×960 | crf 30 | ~15 MB |
| whatsapp_long | 720×1280 | 2500 kbps | ~79 MB (send as a document) |
| whatsapp_saver | 540×960 | 1100 kbps | ~36 MB (send as a document) |
| tiktok | 1080×1920 | crf 20, max 8 Mbps | ~150–240 MB |
| status | 720×1280 | fitted under 15 MB | only when the clip is 30 s or shorter |

## Testing

- `python3 worker/selftest.py` renders a 3-clip recipe (J-cut, fade, segment boundary, stage look) from a generated
  video and checks the cut points by the pitch of the audio.
- `python3 worker/app_smoketest.py` drives the phone app in headless Chrome against a fake GitHub (needs Playwright).

## Costs and limits

GitHub Actions is free for public repos, Releases allow files under 2 GiB, Pages serves the app, the API allows
5,000 calls an hour per token. No Supabase, no monthly subscription. If GitHub ever objects to rendering video in
Actions, `worker/render.py` runs unchanged on any PC with ffmpeg and `gh` (`python3 worker/render.py` in a clone of the repo).
