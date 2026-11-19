# 🎬 SubStudio: Local Processing Studio

SubStudio turns raw video files into multi-lingual, subtitled media without anything leaving your
machine. Transcription runs locally with Faster-Whisper; only the translation step calls out to
Google Gemini. Everything is orchestrated from a Next.js dashboard with live progress over SSE.

<p align="center">
  <img src="ressources/screen.png" width="400">
</p>

## 🚀 Key features

* **Local AI transcription** — Faster-Whisper (`tiny` → `large`), CPU-optimised with int8
  quantisation. Word-level timestamps are grouped into subtitle blocks on sentence boundaries.
* **Context-aware translation** — before translating, the filename is sent to Gemini (`gemini-3.8-flash`) to
  build a "story bible" (title, characters, genders, tone, plot). That profile is fed into every
  translation batch so character names and gendered grammar stay consistent, and is also used as
  Whisper's `initial_prompt` to improve transcription of proper nouns.
* **Non-destructive muxing** — existing video, audio and subtitle streams are **copied**, never
  re-encoded; the AI tracks are appended with correct ISO 639-2 language tags and an `AI <LANG>`
  title. Bitmap subtitle tracks (PGS/VobSub) survive intact.
* **Live progress** — Server-Sent Events stream both a progress percentage and the backend's log
  lines into the dashboard's execution terminal.
* **Sidecar SRT reuse** — an existing `.srt` next to the video (or in a `Subs/` or `Subtitles/`
  folder) can be used as the translation source, skipping transcription entirely.

## 🏗️ Architecture

* **Frontend (Next.js 14):** React dashboard, state in a single `StudioContext`.
* **Backend (FastAPI):** `PipelineOrchestrator` runs the batch. Every blocking step
  (Whisper, Gemini, FFmpeg) is dispatched with `asyncio.to_thread`, so the event loop stays free
  to serve the SSE stream and `/api/scan` while a job runs.
* **Shared volume:** the backend mounts your media folder at `/data`. Processing is in-place —
  outputs are written next to the source file, not to a separate directory.

```
User selects videos
      ↓
POST /api/process           → returns immediately, batch runs as an asyncio task
      ↓
GET  /api/events/{file_id}  ← SSE stream (status + log events)
      ↓
translator.get_context_profile()   → "story bible" from the filename
      ↓
transcriber.transcribe()           → SRT from audio   (skipped if a sidecar SRT is used)
      ↓
subtitles.translate_cues()         → translated cues: model sees {id, text} only, timings re-attached by id
      ↓
muxer.mux()                        → {name}_SubStudio.mkv beside the original
```

## 📡 API

All routes are prefixed `/api`, except `/health`.

### `GET /api/scan?target_path=/data`

Recursive walk. Returns folders and videos; results are cached for 2 seconds per folder.

```json
{
  "files": [
    {
      "id": "/data/movies/example.mkv",
      "fileName": "example.mkv",
      "filePath": "/data/movies/example.mkv",
      "is_directory": false,
      "status": "idle",
      "progress": 0,
      "subtitleInfo": {
        "hasSubtitles": true,
        "subType": "embedded",
        "language": "eng",
        "externalPath": null,
        "embeddedTracks": ["eng", "fra"]
      },
      "sourceLang": ["eng"],
      "targetLanguages": ["fr"]
    }
  ]
}
```

`subType` is `"external"` when a sidecar file was found, `"embedded"` when only internal streams
exist, `"manual"` once you pick a file in the import modal, `null` when there are none.

### `POST /api/process`

Queues a batch and returns straight away — it does **not** wait for the work to finish.

```json
{
  "videos": [
    {
      "name": "example.mkv",
      "path": "/data/movies/example.mkv",
      "selectedSrtPath": null,
      "src": "auto",
      "out": ["fr", "es"],
      "workflowMode": "whisper",
      "syncOffset": 0.0,
      "stripExistingSubs": false
    }
  ],
  "globalOptions": {
    "transcriptionEngine": "medium",
    "generateSRT": true,
    "muxIntoMkv": true,
    "cleanUp": false
  }
}
```

Response: `{"status": "accepted", "count": 1}`

> ⚠️ **`cleanUp` deletes your source video** after a successful mux. It does *not* mean
> "clean up temporary files" — temp audio and temp SRTs are always removed regardless. There is
> deliberately no UI control for this; it is `false` unless you call the API directly.

> `src` is accepted but currently ignored — Whisper always auto-detects the source language.

### `GET /api/events/{file_id}` (SSE)

`file_id` is the video's full path, URL-encoded **once**. Frames arrive on the default `message`
event (there are no named event types), each carrying one JSON object:

```jsonc
// progress update
{"type": "status", "fileId": "/data/m.mkv", "status": "transcribing", "progress": 62, "message": "…"}
// a backend log line, mirrored into the dashboard terminal
{"type": "log",    "fileId": "/data/m.mkv", "level": "INFO", "message": "Transcription Progress: 60%"}
```

`status` is one of `queued`, `processing`, `transcribing`, `translating`, `done`, `error`,
`cancelled`. Progress runs 5 → 100. A `: keepalive` comment is sent every 15 s of silence. The
stream stays open until the client closes it.

### `POST /api/abort`

Stops every running **and queued** job of the batch: `{"status": "aborting", "count": N, "files": [...]}`.
A running FFmpeg child is killed, the partial `_SubStudio.mkv` is removed, and each job reports
`cancelled` on its SSE stream. The dashboard's **STOP BATCH** button calls it.

### `DELETE /api/abort/{file_id}`

Stops one job (`file_id` URL-encoded once). `404` means it is not running any more. The card's
**Cancel** button calls it.

### `POST /api/retry`

Re-runs **one language** of a video from its last run (body `{video, language, globalOptions}`), then rebuilds the
outputs from every language that passed. `404` when that video was not processed since the backend started, `409`
while it is running. Each video × language has its own verdict (`GET /api/cells/{file_id}`, SSE frames of type
`cells`): `passed` with its origin, or `failed` with one sentence saying why.

### `GET /health`

`{"status": "online"}` once the orchestrator has been constructed.

## 🎯 Workflow modes

Set per video, or globally in the sidebar. The value travels as `workflowMode`:

| Mode | Value sent | Behaviour |
|---|---|---|
| **Pure: AI only** | `whisper` | Always transcribes with Whisper, then translates. |
| **Hybrid: SRT if found else AI** | `hybrid` | Uses `selectedSrtPath`, or `<video>.srt` beside the file; falls back to Whisper. |
| **Legacy: SRT refining only** | `srt` | Same lookup as hybrid — and **also falls back to Whisper** if no sidecar SRT is found. |

`stripExistingSubs` is a separate boolean, not a mode: it drops the source file's existing
subtitle tracks from the output instead of carrying them over.

> **Embedded subtitle tracks cannot currently be used as a translation source.** They are
> detected and shown on the badge, but `hybrid`/`srt` only look for *sidecar files*. A video whose
> only subtitles are embedded will be transcribed from audio. This is a known gap on the backlog.

## 🚦 Getting started

### 1. Configure

Copy `.env.example` to `.env` and fill it in:

```env
GEMINI_API_KEY=...                     # required, used for translation
NEXT_PUBLIC_MEDIA_PATH=C:/Videos        # your media folder, mounted as /data
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_LANGUAGES='{"English":"en", "French":"fr"}'
```

`.env` is gitignored. `NEXT_PUBLIC_*` values are baked in at **build** time — after changing any
of them you must rebuild, not just restart:

```bash
docker compose down
docker compose up --build
```

### 2. Launch

```bash
docker compose up --build
```

### 3. Open the studio

👉 http://localhost:3000

On first run the backend downloads the selected Whisper model into the `whisper_model_cache`
volume (~75 MB for `base`, ~1.5 GB for `large`). Subsequent runs reuse it.

## 📋 Prerequisites

* **Docker & Docker Compose** (v20.10+)
* **RAM:** 4 GB minimum; 8 GB+ for `medium` and above
* **Python 3.10+** and **FFmpeg 4.4+** only if running outside Docker (both are in the image)

**Video:** `.mp4` `.mkv` `.avi` `.mov` `.wmv` — **Subtitles:** `.srt` `.vtt` `.ass` `.ssa`

## 🧠 Model selection

| Model | Params | Notes |
|---|---|---|
| `tiny` | 39 M | Fastest; fine for clear, non-technical speech |
| `base` | 74 M | Good speed/accuracy balance |
| `small` | 244 M | Better with accents |
| `medium` | 769 M | Default; recommended for most content |
| `large` | 1.5 B | Most accurate, slowest — maps to large-v3 |

Switching size unloads the previous model and runs `gc.collect()` before loading the new one, so
memory does not accumulate across changes.

## 🐛 Troubleshooting

**Backend won't start** — `docker logs media-ai-backend`. For
`ModuleNotFoundError: faster_whisper`, rebuild: `docker compose build --no-cache backend`.

**Frontend can't reach the backend** — check `NEXT_PUBLIC_API_URL`, then
`curl http://localhost:8000/health`. Remember it is baked in at build time.

**Progress bar never moves** — confirm the SSE stream is open in the browser's Network tab
(`/api/events/...`, type `eventsource`). The `file_id` must be encoded exactly once.

**Muxing fails** — the backend logs FFmpeg's stderr at ERROR level. Existing subtitle streams are
copied, so bitmap tracks are safe; a failure here is usually a corrupt source file.

**Out of memory** — drop to a smaller model. int8 quantisation is already on.

**Subtitles out of sync** — set **Sync (s)** on the video's card (`syncOffset` in the API): it
shifts every timestamp, positive = later, negative = earlier.

## 📂 Project structure

```text
.
├── backend/
│   ├── core/
│   │   ├── __init__.py
│   │   ├── events.py              # EventManager: thread-safe SSE broadcast
│   │   ├── muxer.py               # FFmpeg muxing + ISO 639-2 tagging
│   │   ├── scanner.py             # Recursive walk + subtitle detection
│   │   ├── subtitles/             # Cue list (read any format/charset, shift, clip, SRT) + Translator contract
│   │   ├── subtitle_processor.py  # Embedded text-track extraction
│   │   ├── transcriber.py         # Faster-Whisper + audio extraction
│   │   └── translator.py          # GeminiTranslator (optional backend) + story bible
│   ├── Dockerfile
│   ├── main.py                    # FastAPI app + PipelineOrchestrator
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── app/                   # globals.css, layout.tsx, page.tsx
│   │   ├── components/
│   │   │   ├── dashboard/         # GlobalProgress, SubImportModal, VideoCard, VideoList
│   │   │   └── layout/            # Sidebar
│   │   └── lib/                   # api.ts, sse.ts, studio-context.tsx, types.ts
│   ├── public/logo.png
│   ├── Dockerfile
│   ├── package.json
│   ├── postcss.config.js
│   ├── tailwind.config.ts
│   └── tsconfig.json
├── data/                          # Media volume (gitignored)
├── ressources/                    # logo.png, screen.png
├── .env.example
├── .gitignore
├── .claude/rules/                 # Engineering and git rules for Claude Code
├── CLAUDE.md                      # Working instructions for Claude Code
├── doc/                           # Project notes (how the Claude rules are organised)
├── docker-compose.yml
└── readme.md
```

## 🤝 Contributing

1. Fork, branch (`git checkout -b feature/amazing-feature`)
2. Verify before opening a PR — `npx tsc --noEmit` **and** `npx next build` must both pass
   (`next build` catches App Router errors that `tsc` alone does not)
3. Commit, push, open a PR

## 🙏 Acknowledgments

* **Faster-Whisper** — Systran's optimised Whisper implementation
* **FFmpeg** — multimedia framework
* **Google Gemini** — `gemini-3.8-flash` for translation
* **Next.js** — React framework
