# Church PC: pack and ship

One script runs after each stream. It makes a small marking copy, finds the pauses, uploads
everything to GitHub, then makes the full-quality source and (if you want) powers the PC off.

## One-time setup (about 20 minutes)

1. **OBS → Settings → Output → Recording.** Record to `D:\NRSC-Recordings`, format MKV, filename
   `%CCYY-%MM-%DD_main` (change `main` per service, or rename the file later).
   **OBS → Settings → Advanced → Recording:** tick *Automatically remux to mp4*.
   **OBS → Settings → General → Output:** tick *Automatically record when streaming*.
2. Install **Python 3** from python.org (tick *Add to PATH*), then in a command prompt:
   `pip install numpy`
3. Install **ffmpeg**: `winget install Gyan.FFmpeg` (or download the build from gyan.dev and add its `bin` folder to PATH).
   Check: `ffmpeg -version`
4. Install **GitHub CLI**: `winget install GitHub.cli`, then `gh auth login` and sign in as the church GitHub account
   (or an account that is a collaborator on the repo).
5. Copy this `church-pc` folder to `D:\NRSC-Shorts` and copy `config.example.json` to `config.json`. Edit the paths if needed.
6. Test by hand on last Sunday's recording:
   `python D:\NRSC-Shorts\pack_and_ship.py --file "D:\NRSC-Recordings\2026-11-01_main.mp4"`
   When it says *marking can start on the phones now*, the service appears in the app.

## Run it by itself (Task Scheduler)

Create a task that runs after each service. Example for Sunday:

- Trigger: weekly, Sunday, 2:15 pm (the stream usually ends by 1 pm; change to suit).
- Action: Start a program
  - Program: `python`
  - Arguments: `D:\NRSC-Shorts\pack_and_ship.py --auto --shutdown`
  - Start in: `D:\NRSC-Shorts`
- Settings: tick *Run task as soon as possible after a scheduled start is missed* and *Wake the computer to run this task*.

Add similar tasks for Wednesday (about 10:30 pm) and Friday (about 11:15 pm).
`--auto` picks the newest recording that is not on GitHub yet and waits until OBS has finished writing it.
`--shutdown` powers the PC off 60 seconds after the upload finishes. Leave it out if someone still needs the PC.

## Useful options

| Option | What it does |
| --- | --- |
| `--source-height 1080` | Full quality at 1080p instead of 720p (sharper tight crops, about twice the upload). |
| `--range 2:00:00-3:10:00` | Ship full quality only for the Word, from 2 h to 3 h 10 min into the recording. The marking copy is always the whole stream. |
| `--tag src-2026-11-01-morning --title "Sunday morning service"` | Name the service yourself. |

## How long it takes

On a laptop without an NVIDIA card: the marking copy and analysis take about 10 minutes per hour of
recording; the 720p source another 15–25 minutes per hour; then the upload at whatever speed the church
internet gives (1 GB takes 7 minutes at 20 Mbps, 70 minutes at 2 Mbps). With an NVIDIA card the encoding
parts are 3–5 times faster.

## If it did not run

Nothing is lost. The next time the PC is on, run the test command above (or let the scheduled task
catch up: it runs the missed task at the next start). Until then the app shows *waiting for the source
video* on any clip you asked it to render.
