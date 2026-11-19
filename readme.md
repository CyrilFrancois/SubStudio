<p align="center">
  <img src="ressources/logo.png" width="96" alt="SubStudio">
</p>

# SubStudio

SubStudio puts the right subtitles **inside your video files**, in the languages you want, on your own computer.
You pick videos and languages, press Start, and each file comes back with its subtitle tracks and a small marker in
its name, for example `Ghosts S05E01 (Sub-EN,FR).mkv`. It prefers subtitles written by people. A model writes them
only when no human subtitle exists.

## How it works

### What you do

1. **Select videos** in the library (files or whole folders), with or without existing subtitles.
2. **Select languages.** The default is « Original Version, English, French ». Your choice is remembered.
3. **Start**, then follow the queue.

### What SubStudio does, for each video and each language

It takes the first rung that gives a subtitle (the full process: [doc/user-process.md](doc/user-process.md)).

| # | Situation | What happens |
|---|---|---|
| 1 | The file already carries a subtitle in that language **made by SubStudio** | It is kept. A file already complete stays unchanged: same bytes, same name. Subtitles from elsewhere are not trusted yet (see Status). |
| 2 | Nothing usable in the file | A human subtitle is **downloaded** from free providers, then **re-timed to the audio** (`ffsubsync`). |
| 3 | A human subtitle exists in another language | It is **translated**, with the context looked up for that episode: series, title, summary and character names, from TVmaze. None of it is invented by a model. |
| 4 | No subtitle anywhere | The speech is **transcribed** (Whisper turbo, locally), then translated into the other languages. |

Each video × language ends with a verdict. Either it passed, with its origin (`kept`, `downloaded`,
`translated from en`, `transcribed`), or it failed, with one sentence saying why. A failed language can be retried
on its own.

### The result

The new tracks are written **into the video** by a verified swap:
- **Verified:** SubStudio builds a new file next to the source under a temporary name, then checks it: same video and
  audio streams, same duration, every new track readable.
- **Swapped:** only after the check does the new file take the video's place, under its marked name; the old file is
  removed last. A failed check leaves the source untouched.
- **MP4 becomes MKV,** since MP4 cannot hold normal subtitle tracks.
- **Disk space:** the swap needs free disk equal to one video.

## Status (2026-11-19)

| Part | State |
|---|---|
| Library, coverage per language, settings, Start and the queue (new UI) | works. Jobs are kept in SQLite and survive a restart |
| Ladder: existing → download → translate → transcribe, one verdict per language, retry one language | works |
| Translation context (TVmaze series, episode, summary, character names) | works for TV episodes. Films get their title and year only |
| Translation | **Gemini only, and only with `GEMINI_API_KEY`.** A free **local** translator is in progress (decision D-0003 pending). Without a key, rungs 1, 2 and 4 still work, and a language that needs translating fails with that reason |
| Quality gate (sync, coverage, language, encoding, reading speed) | built and benchmarked (17/17 real tracks judged right, 128/128 seeded faults rejected: [doc/gate-bench.md](doc/gate-bench.md)), not yet called by the ladder. Until it is, a subtitle **not made by SubStudio** is ignored (`count_existing` off) |
| Cue editor (`/api/subtitles`) | not built yet: answers `501` |

## Requirements

- **Windows 10/11** (also runs on Linux and macOS), about **7 GB RAM** and **4 CPU threads** free: SubStudio is
  capped at half of a typical laptop ([D-0001](doc/decisions/D-0001%20local%20free%20models%20within%20half%20the%20machine.md)).
  No GPU needed.
- **Python 3.12** (`py -3.12` on Windows).
- **Node.js** 18.17 or newer (LTS).
- **ffmpeg and ffprobe on PATH:** `winget install Gyan.FFmpeg`, then open a new terminal.
- About 1.6 GB of disk for the Whisper turbo model, downloaded on the first transcription.

## Run it natively (Windows, PowerShell)

From the repository root, in two terminals. The backend does **not** read `.env` by itself: the variables are set in
its terminal.

**Terminal 1: backend** (http://localhost:8000)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt
$env:SUBSTUDIO_MEDIA_ROOT = "C:/Videos"     # the folder holding your videos
# Optional, cloud translation until the local translator lands: $env:GEMINI_API_KEY = "<your key>"
Set-Location backend
..\.venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

On later runs, skip the first two lines: re-creating `.venv` fails while a backend started from it is running.

Check it: open http://localhost:8000/health (shows `{"status":"online"}`), then
http://localhost:8000/api/library (lists your videos). The first scan probes every video and takes a few seconds.

**Terminal 2: frontend** (http://localhost:3000)

```powershell
Set-Location frontend
npm install
npm run dev
```

Open **http://localhost:3000**. The frontend calls the backend at `http://localhost:8000`. To use another address,
put `NEXT_PUBLIC_API_URL=http://<host>:<port>` in `frontend/.env.local`, then restart `npm run dev`.

## Run it with Docker (alternative)

```powershell
Copy-Item .env.example .env    # then edit .env: NEXT_PUBLIC_MEDIA_PATH = your videos folder
docker compose up --build
```

- **Library:** the folder in `NEXT_PUBLIC_MEDIA_PATH` is mounted as `/data` in the backend container.
- **Backend address:** fixed at `http://localhost:8000` by `docker-compose.yml`; the value in `.env` is not used there.
- **Config changes:** `docker compose restart` does not re-read `.env`. After a change, run
  `docker compose down`, then `docker compose up --build`.
- **Not re-checked:** this path was not re-checked for this readme. Docker's virtual machine on Windows also adds
  its own memory use.

## Configuration

Every setting is an environment variable. `.env.example` lists them with their defaults and no secrets.

| Variable | Default | What it does |
|---|---|---|
| `SUBSTUDIO_MEDIA_ROOT` | `/data` | The library folder (native: e.g. `C:/Videos`). Nothing outside it is read or written. |
| `SUBSTUDIO_STATE_DIR` | `backend/state` | SubStudio's own files: settings, the job queue (`jobs.sqlite3`), the context cache. Never inside the library. |
| `SUBSTUDIO_WORK_DIR` | `<temp>/SubStudio/work` | Per-video intermediate files (downloads, re-timed subtitles). |
| `SUBSTUDIO_DOWNLOADS` | `1` | `0` turns off every online lookup: subtitle sites and TVmaze (offline runs, tests). |
| `SUBSTUDIO_API_STUB` | off | `1` simulates jobs so the UI can be tried without processing anything; the UI shows a banner. |
| `WHISPER_MODEL` | `turbo` | faster-whisper model name. |
| `ASR_THREADS` | `4` | CPU threads for transcription (the budget is 4). |
| `GEMINI_API_KEY` | empty | Cloud translation until the local translator lands (https://aistudio.google.com/apikey). |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Must be a Gemini 3.x model. |
| `OPENSUBTITLES_USERNAME`, `OPENSUBTITLES_PASSWORD` | empty | Optional free OpenSubtitles.com account: adds the largest catalogue and exact-file matches. Without it, only keyless providers are asked. |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Where the frontend finds the backend. Applied when `npm run dev` starts. |
| `NEXT_PUBLIC_MEDIA_PATH` | none | Docker only: the host folder mounted as `/data`. |
| `NEXT_PUBLIC_LANGUAGES` | built-in list | Old dashboard only: the languages it offers. The new UI keeps them in Settings. |

## Models and what leaves your computer

| Step | Runs | Model or source |
|---|---|---|
| Transcription | locally | Whisper large-v3 **turbo** via faster-whisper, int8, voice-activity detection, 4 threads |
| Quality gate | locally | Voice-activity detection and text checks |
| Translation | cloud today, local planned | Gemini, which receives the subtitle text to translate, only with a key. A free local model chosen on the bench is in progress (D-0003) |
| Download | network | subliminal's free providers, plus OpenSubtitles.com with an account. They receive the file name, and some providers a hash of the file |
| Context | network | [TVmaze](https://www.tvmaze.com) (free, no key, CC BY-SA). It receives the series title. Cached per series |
| Re-timing, writing | locally | ffsubsync, ffmpeg |

No video or audio is ever uploaded. `SUBSTUDIO_DOWNLOADS=0` keeps SubStudio off the network, apart from Gemini if a
key is set.

## API

The backend serves its own reference at http://localhost:8000/docs.

**UI contract** (`backend/api`, the new UI):

| Route | What it does |
|---|---|
| `GET /api/library` | Videos under the media root × wanted languages, with each cell's coverage and verdict. |
| `POST /api/jobs`, `GET /api/jobs`, `DELETE /api/jobs/{id}` | The queue: start videos, list jobs, cancel one. |
| `GET /api/jobs/{id}/logs` | A job's log lines, oldest first. |
| `GET /api/events` | One server-sent event stream for every tab. |
| `GET /api/settings`, `PUT /api/settings` | Wanted languages and `count_existing`, stored under `SUBSTUDIO_STATE_DIR`. |
| `GET /api/system` | Media root, loaded model, CPU and RAM use, and the resource budget. |
| `GET`/`PUT /api/subtitles/{video_id}/{lang}` | The cue editor: `501` until it is built. |

**Pipeline** (`backend/main.py`, used by the old dashboard at `/legacy`):

| Route | What it does |
|---|---|
| `POST /api/process` | Starts a batch: `{"videos": [{"name": "...", "path": "...", "out": ["en","fr"]}], "globalOptions": {}}`. Returns at once; `409` while another job runs. |
| `GET /api/events/{file_id}` | Server-sent events for one video: status, log lines, and `cells` (one verdict per language). `file_id` is the full path, URL-encoded once. |
| `GET /api/cells/{file_id}` | The last verdicts of that video. |
| `POST /api/retry` | Re-runs one language of a video from its last run (`409` while another job runs). |
| `POST /api/abort`, `DELETE /api/abort/{file_id}` | Stop every job, or one. |
| `GET /api/scan?target_path=...` | Folder listing for the old dashboard. |
| `GET /health` | `{"status":"online"}` |

## Tests

```powershell
.venv\Scripts\python -m pip install -r backend\requirements-dev.txt
.venv\Scripts\python -m pytest -q
```

Run from the repository root. Needs ffmpeg on PATH. The tests build their own short clips in a temporary folder,
never touch your library, and turn online lookups off. Model and gate benchmarks: [bench/README.md](bench/README.md).

## Project layout

```
backend/
  main.py              FastAPI app, pipeline routes, the orchestrator
  api/                 the UI contract: library, queue (SQLite), settings, system, events
  core/
    acquire/           rungs 1–2: existing subtitles, downloads, re-timing
    ladder.py          runs the four rungs for one video
    cells.py           one verdict per video × language
    gate/              the quality gate: sync, coverage, language, encoding, reading speed
    subtitles/         the typed cue list and the translator contract
    context/           looked-up series and episode facts, names to keep
    transcriber.py     Whisper turbo → typed result
    translator.py      Gemini backend
    output/            the verified in-place swap and the file-name marker
frontend/              Next.js 14: the library and queue (/), settings (/settings), the old dashboard (/legacy)
bench/                 model and gate benchmarks on this machine
doc/                   process, reviews, decisions
```

## Credits

Episode facts come from [TVmaze](https://www.tvmaze.com), licensed CC BY-SA. Transcription uses
[faster-whisper](https://github.com/SYSTRAN/faster-whisper), downloads use
[subliminal](https://github.com/Diaoul/subliminal), and re-timing uses [ffsubsync](https://github.com/smacke/ffsubsync).
