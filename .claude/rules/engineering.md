# SubStudio: engineering rules (verification, architecture, patterns, code style)

Moved verbatim from the pre-harness `SubStudio/CLAUDE.md` (2026-11-18); same authority as `SubStudio/CLAUDE.md`.
Section numbers (§N) are those of the pre-harness file and stay valid: each kept section keeps its heading,
in `SubStudio/CLAUDE.md` or `SubStudio/.claude/rules/*.md`. Map: `doc/harness-migration.md`.

---

## 3. Verification — exercise it, don't reason about it

> This section exists because a review on 2026-08-21 produced three findings that were **wrong or
> overstated** until they were actually run. Reasoning about framework behaviour is not evidence.

### The commands that actually exist here

```bash
# Free RAM is the shared limit (STEWARD, 2026-11-19, workspace doc/04): next build, a whole-project tsc and the
# full test suite wait behind model runs through the heavy lease. Single test files and reads stay free.
H="python3 -I C:/Users/cyril/Documents/Workspace/doc/_tools/threads.py with heavy --"

# Frontend — natively (Node at C:\Users\cyril\tools\node, 2026-11-19), from frontend/
$H npx tsc --noEmit
$H npx next build         # NEXT_PUBLIC_API_URL is baked in here, not read at run time
npm run check:types       # fails when src/lib/api.gen.ts no longer matches backend/api (npm run gen:types)

# Backend — from the repo root, in the worktree's venv (pytest.ini sets the paths)
$H .venv/Scripts/python.exe -m pytest -q
python -c "import ast; ast.parse(open('backend/main.py',encoding='utf-8').read())"

# Runtime
docker compose up --build
docker compose logs -f backend
curl -s http://localhost:8000/health
```

**`tsc` passing is NOT "it builds".** `next build` runs App Router validation that `tsc` does not:
a stray named export from `page.tsx` fails the build while `tsc` stays green. Run both.

**`next dev` hides build errors.** `frontend/Dockerfile` runs `npm run dev`, which compiles lazily
and skips page-export validation. Never conclude "it works" from the dev server alone.

**Bind mounts on Windows are CASE-INSENSITIVE.** A `docker run -v` from this repo will resolve
`./VideoCard` to `videoCard.tsx` and the build will pass — then fail on a real Linux host. To test
filename casing honestly, copy into the container's own filesystem first (`cp -r /src/. /build/`).

### Every external call has a decided outcome

For each FFmpeg, ffprobe or Gemini call: name what happens on failure, and make sure the answer is
not "nothing". A swallowed error looks like success. If it cannot be handled, log at ERROR and
re-raise — never absorb it into an empty result.

**Read what the provider actually returned, not just that it returned text.** A Gemini response
whose `finishReason` is not `STOP` (`MAX_TOKENS`, `SAFETY`, `RECITATION`) is partial or empty;
`translator._generate` raises on it. "It returned text" is not "it returned the 30 blocks".

**Absent, empty and zero are three different things.** An empty list can mean "none" or "the call
failed" — a failed ffprobe must not read as "no subtitle streams". `??` does not catch `""`, so a
defined-but-empty `NEXT_PUBLIC_*` slips past it. **Validate shape, not presence:** a placeholder
passes every "is it set?" check.

**A failed read is never shown as empty.** A frontend call ending in `.catch(() => [])`, `null` or
an empty `catch {}` turns "the backend is down" into "no videos". Show the error state.

**A typed fetch is an unchecked assertion.** `get<T>` validates nothing — read the handler before
asserting a shape (`/api/scan` returns `{"files": [...]}`, not an array).

**Units.** Name the unit of every number: ms vs s, SRT `,` vs `.` decimal, frames vs seconds.

A translation that cannot be completed raises `TranslationFailed` and fails **that language's cell**
(`core/cells.py`, D-0002): the other languages still pass and ship, the video reads « error », and the
source text is never shipped as if it were the translation (it was until 2026-11-19, silently). Keep it so.
With `cleanUp`, the source is deleted only when every cell passed: it is what a retry starts from.

### Boundaries that have actually bitten this project

- **A video with no audio track.** `-map 0:a` aborts the whole mux with "matches no streams".
  Every stream map must carry `?`.
- **Bitmap subtitles (PGS/VobSub).** `-c:s srt` cannot convert them — FFmpeg refuses with
  "Subtitle encoding currently only possible from text to text or bitmap to bitmap". Existing
  streams must be `-c:s copy`; only newly-added SRTs get `-c:s:N srt`.
- **Paths with spaces and non-ASCII.** `file_id` is a full path used as the SSE routing key. It
  must be URL-encoded exactly **once** — encoding twice yields a key no emitter ever writes to,
  and the stream silently delivers nothing.
- **Subtitles past the end of the video.** `subtitles.drop_after` (called by `core/output/inplace.py`) drops them; a player will
  otherwise hold a black frame.
- **Zero-byte and corrupt media.** `extract_audio` returns `""`, which must raise, not proceed.

### Blocking work must leave the event loop

Whisper, Gemini and FFmpeg calls are **synchronous**. `run_batch` is `async`. Calling them
directly holds the loop: measured on 2026-08-21, a `/health` probe issued 1 s into an 8 s job
waited **7023 ms**. Every such call goes through `asyncio.to_thread`.

Two consequences that must be preserved:
- **`asyncio.Queue` is not thread-safe.** Because the pipeline now runs off-loop, `EventManager`
  hands data back with `loop.call_soon_threadsafe`. Do not call `put_nowait` from a worker thread.
- **Do not use `BackgroundTasks` for the batch.** Uvicorn withholds the HTTP response until they
  finish, so `POST /api/process` would hang for the entire job. Use `asyncio.create_task`, keep a
  reference, and attach a done-callback that logs exceptions.

### Config takes effect somewhere

Storing a value is not the same as the running thing reading it. `docker compose restart` does
**not** re-read `env_file`; `NEXT_PUBLIC_*` is baked in at **build** time. After any config
change: `docker compose down && docker compose up --build`, then verify the *running* container:

```bash
# Length + fingerprint only — never `env | grep` a key: that prints the value into the transcript.
docker compose exec -T backend sh -c 'printf %s "$GEMINI_API_KEY" | wc -c; printf %s "$GEMINI_API_KEY" | sha256sum | cut -c1-12'
```

### A 200 is not proof your change is running

- Grep the served output (or the logs) for a string only the new code emits.
- **`uvicorn --reload` restarts the backend on any `backend/*.py` save.** That kills every
  in-memory job and SSE stream silently. Never edit backend code while a test batch runs.
- On Windows bind mounts, file-change events may not reach `next dev` / `--reload`. "The container
  has my file" is not "the process serves it".
- `frontend/.next` is an anonymous volume: `rm -rf frontend/.next` on the host deletes nothing.
- **Exit 137 / "Killed" is memory, not code.** Rerun it; don't search the diff. Whisper `large`
  in the Docker VM, or `next build` beside the dev server, can OOM.
- "Connection refused" may be the stack being down: `docker compose ps -a` before reading it as a bug.

### Live checks use the shipped path, on seeded cases

- **A live AI check runs on the shipped model and code path** — call `translate_cues` with
  the configured backend (`GEMINI_MODEL` for Gemini), never a hand-built prompt or another model.
- **Seed the case the guarded branch needs.** When a bug hides behind "does this file have X",
  make a file that has X: no audio, PGS subs, a non-ASCII name with spaces, zero bytes, subs past
  the end.
- **A check that cannot fail proves nothing.** Break the code once and watch the check go red
  before trusting its green. A step that is suddenly instant, or prints `(cached)`, did not run.
- **Clean up only what your test created, by exact name.** Output lands next to the source in the
  user's real library: delete the exact `{stem}_SubStudio.mkv`, never a glob. Never test with
  `cleanUp=true` on real media. Restore any changed setting from the value you read before.
- A symptom that won't reproduce: ask what the reporter's setup does differently (VLC, mpv, Plex
  each treat MKV subtitle tracks and language tags differently).


---

## 5. Architecture

**Video discovery → transcription → translation → muxing.** No database; all state is in memory
and lost on restart.

### Backend (FastAPI, Python 3.10)

- `backend/main.py` — the FastAPI app: routes and the lifespan that builds the orchestrator.
- `backend/core/pipeline.py` — `PipelineOrchestrator` and the job models: per video, plan → rungs 1–2 →
  3–4 through `core/ladder.py` (every result gated by `core/gate`) → the in-place update. Blocking steps
  go through `asyncio.to_thread`; a batch runs as an `asyncio.create_task`.
- `backend/core/scanner.py` — recursive `os.scandir` of `/data`; per-video ffprobe for embedded
  subtitle streams; sidecar lookup in `.`, `Subs/`, `Subtitles/`. Guards against escaping
  `base_path`.
- `backend/core/transcriber.py` — Faster-Whisper, lazy model load, `gc.collect()` on size change.
  Word timestamps grouped into blocks on sentence end or 70 chars.
- `backend/core/subtitles/` — **the one subtitle representation**: `Cue(id, start_ms, end_ms, text)`.
  `cues.py` reads any SRT/VTT/ASS (legacy code page chosen from the detected language), shifts,
  clips, wraps and writes SRT; nothing else parses or prints a timestamp. `translate.py` holds the
  `Translator` contract (`translate([{id, text}], target, context) -> [{id, text}]`, no timing ever
  sent) and `translate_cues`, which batches, rejects an answer whose id set differs (retry once,
  then split down to one cue), re-attaches timings by id, and raises `TranslationFailed`. A cue still wrong
  ALONE stays in the source language, recorded in `left_in_source` and on the cell's `attempts` (`produce_cell`
  passes it); over 10 % of the cues left (`MAX_ECHO_SHARE`) fails the language, and the gate's « untranslated »
  check names the cues.
- `backend/core/cells.py` — one `Cell` per video × language: `pending → running → passed | failed`, an
  `Origin` (`kept`, `downloaded`, `translated from en`, `transcribed`) or a one-sentence reason. A source already
  in the wanted language (detected) is kept, never translated. `VideoRun` keeps the source and every cell's cues
  in memory for `POST /api/retry`. The ladder (`core/acquire`) and the quality gate plug in here.
- `backend/core/context/` — `get_context(video_path, cues)` → `TranslationContext` with looked-up facts only
  (user-process §2 rung 3): `guessit` on the file name, TVmaze for an episode (keyless, CC BY-SA, ≥ 20 calls/10 s:
  https://www.tvmaze.com/api), characters and proper nouns counted from the cues as the glossary. A name shared by
  several shows needs the filename's year, else nothing is looked up. Network failure → what is known plus
  `missing` (one sentence). Cached per series in `<STATE_DIR>/context/tvmaze/` (`api/config.STATE_DIR`,
  `SUBSTUDIO_STATE_DIR`).
- `backend/core/translators/` — `make_translator()` picks the backend from `TRANSLATOR` (`local` default,
  `gemini` opt-in). `llamacpp.py` —
  `LlamaCppTranslator`, the default: TranslateGemma 4B Q4_K_M on a native llama.cpp server, 10 cues per
  call as `id|text` lines, prompt rendered from the model's own chat template (`/props`), a reply cut at
  the token limit raises `BatchMismatch`. The reply parser reads the id with any separator or plain whitespace
  (NBSP included) and joins an unnumbered line BETWEEN two answers to the one before (chatter before the first or
  after the last is rejected); the id set check catches a merged cue. A
  batch of ONE cue is sent as plain text with no id and must come back as the translation alone (one line, or one per
  speaker), so the last step of a split never fails on the format; anything else is kept in the source language. `llama_server.py` starts the server on the first batch
  (`--threads 4`, all layers on the iGPU) and `unload()` (part of the contract) stops it after each video's
  translations, under a lock shared by batches and retries, so Whisper and the LLM are never loaded together
  (D-0001). The source language comes from `detect_language` or Whisper; unknown, the cell fails at once.
  Choice and numbers: `doc/decisions/D-0003`.
- `backend/core/translator.py` — `GeminiTranslator`, an optional backend of that contract: Gemini
  REST (`httpx`, no SDK), `thinkingLevel: low`, JSON output with a response schema, 30 cues per
  call, temperature 0.2. Its context comes from `core/context.get_context` (filename + TVmaze lookup + the
  names in the source cues, never invented), built per source by the ladder.
  `_generate` raises on any HTTP error, missing candidate, or `finishReason != STOP`.
- `backend/core/output/` — the only output: `write_run` updates the source **in place** by a verified swap
  (temp file → verify streams, flags, duration, new tracks → atomic rename), adds the `(Sub-EN,FR)` marker of the
  actual languages, tags each new track with its origin (`core/provenance`), leaves an untouched video as it was.
- `backend/core/subtitle_processor.py` — finds the first embedded text track and extracts it as
  cues (raises on failure). **Currently unused** — see the workspace `TODOLIST.MD` (« [SubStudio] backlog »).
- `backend/core/events.py` — `EventManager`, one broadcast channel per `file_id`, thread-safe.

### Frontend (Next.js 14, TypeScript)

Screens of D-0002 / ux-review §2, built on the UI API (`backend/api`, below):
- `src/app/page.tsx` — Library: coverage matrix (video × wanted language), search, filters, select,
  Start. `src/components/library/` — `LibraryTable`, `CoverageChip` (icon + text, never colour
  alone), `VideoDetails` (subtitles found and why not counted, per-video overrides).
- `src/app/settings/page.tsx` — the one language list (OV + ISO 639-1), `count_existing`; saved on change.
- `src/app/queue/page.tsx` — batch bar (by `Job.batch_id`), running job's stage stepper with ETA, up next with Cancel,
  history with reasons and logs. `src/components/queue/`.
- `src/app/review/page.tsx` — one video × language as lines (`/review?video=<id>&lang=<code>`, opened from a validated
  Library cell or a finished job): source beside the subtitle, the lines the gate would object to flagged, a dark-frame
  preview with reading speed, edit and Save. The flags and their limits come from the backend (`api/review.flag_cues`,
  core/gate THRESHOLDS), never from a frontend copy. `src/components/review/`.
- `src/components/shell/AppShell.tsx` — navigation, stub-mode banner, backend status.
- `src/lib/api.gen.ts` — **generated** from the backend's OpenAPI (`npm run gen:types`); `types.ts`
  only aliases it. Never hand-write a backend shape. `src/lib/api.ts` — REST client (every failure
  throws the backend's sentence). `src/lib/events.ts` — the one `GET /api/events` stream.
- A page module exports only the page: the App Router fails `next build` on any other export. (The old
  dashboard on `/api/process` was deleted on 2026-11-19 once Queue and Review covered it.)

### API contracts

| Route | Notes |
|---|---|
| `GET /api/scan?target_path=/data` | `{"files": [...]}`. Cached 2 s — **globally, not per path** (known bug, see the workspace `TODOLIST.MD`). |
| `POST /api/process` | `{"status":"accepted","count":N}`, returns immediately. |
| `GET /api/events/{file_id:path}` | SSE. `file_id` = full path, encoded **once**. |
| `POST /api/retry` | `{video, language, globalOptions}`: re-runs one cell from the source kept in memory, rebuilds the output. `404` before a first run, `409` while the video runs. |
| `GET /api/cells/{file_id:path}` | `{"fileId", "cells": [{language, state, origin, rung, reason}]}` of the last run. |
| `GET /health` | `{"status":"online"}`. |

UI API (`backend/api`, models in `backend/api/schemas.py` = the contract; agreed with workspace-64, 2026-11-19).
Videos are addressed by `LibraryVideo.id` (hash of the path), never by the path in a URL.

| Route | Notes |
|---|---|
| `GET /api/library` | Every video under `SUBSTUDIO_MEDIA_ROOT` × every wanted language: `passed` (with origin) / `missing` / `unchecked` (a subtitle SubStudio did not make, not judged by the gate yet: it counts only once the job store's verdict record passes it, D-0002) / `pending` / `running` / `failed` / `unknown` (ffprobe failed). ffprobe cached by path+mtime+size. What counts as ours: `core/provenance.py`, the one rule. |
| `GET/PUT /api/settings` | `{languages, count_existing}`, JSON under `SUBSTUDIO_STATE_DIR`, written atomically. |
| `POST/GET /api/jobs`, `DELETE /api/jobs/{id}`, `GET /api/events` | The job store (SQLite) or, with `SUBSTUDIO_API_STUB=1`, simulated jobs. `/api/events` is one SSE stream, frames discriminated by `kind` (`job`, `log` with the backend's `ts`, `library`). |
| `GET/PUT /api/subtitles/{id}/{lang}` | `CueList`; GET adds each line's `flags` and the gate's `limits` (`api/review.flag_cues`). GET reads the video's « SubStudio <lang> » track (`api/subtitles.py`); `source_text` only while the run is in memory. PUT saves the text only, through the verified swap, tags the track `SUBSTUDIO_REVIEWED=yes` (the Library shows « reviewed »), and may rename a file that had no marker: the response's `video_id` is then the new one. 404/409/422/500 each carry one sentence. Stub: a sample, PUT not simulated. |
| `GET /api/system` | `stub`, media root, CPU/RAM against the D-0001 budget. |

**SSE frames are unnamed** — everything arrives on the default `message` event with a `type`
discriminator of `"status"`, `"log"` or `"cells"` (`CellVerdict[]` in `sse.ts`). There is no `event:` line, so `addEventListener("transcribing", …)`
can never fire. Progress runs 5 → 100.

**`workflowMode`** is one of `whisper` (always transcribe), `hybrid` (sidecar SRT if found, else
Whisper) or `srt` (same lookup, and also falls back to Whisper). `stripExistingSubs` is a separate
boolean, not a mode.

**`cleanUp` deletes the source video.** It is not "remove temp files". There is no UI for it.

### Configuration

`.env` (gitignored; template in `.env.example`): `TRANSLATOR` (`local` default), `LLAMA_SERVER_BIN`,
`LLAMA_MODEL_PATH`, `LLAMA_GPU_LAYERS`, `LLAMA_PORT` (defaults under `%LOCALAPPDATA%\SubStudio\`);
only with `TRANSLATOR=gemini`: `GEMINI_API_KEY`, optional `GEMINI_MODEL`
(default `gemini-3.8-flash`; must be a 3.x model because the request sends `thinkingLevel`),
`NEXT_PUBLIC_MEDIA_PATH`, `NEXT_PUBLIC_API_URL`,
`WHISPER_MODEL`. UI API: `SUBSTUDIO_MEDIA_ROOT` (default `/data`), `SUBSTUDIO_STATE_DIR` (default
`backend/state`, gitignored; never inside the library), `SUBSTUDIO_API_STUB=1` (simulated jobs,
the UI shows a banner).
Whisper models cache in the `whisper_model_cache` volume. Key at
https://aistudio.google.com/apikey.

---

## 6. Key patterns

### Progress reporting

Every long step takes an `on_progress(file_id, status, percent, message)` callback that reaches
the SSE stream. **Never skip progress reporting** — a user staring at 0% assumes a crash.
Callbacks fire from worker threads, so `EventManager` must keep marshalling them back to the loop.

### Error logging and propagation

```python
except subprocess.CalledProcessError as e:
    logger.error(f"FFmpeg failed: {e.stderr}")
    on_progress(file_id, "error", 0, f"Muxing failed: {e.stderr[:100]}")
    raise
```

**The tell:** can the user see what went wrong? A bare "processing failed" is a bug. Errors reach
the UI two ways — the `status: "error"` event, and the log bridge that mirrors every `logger.*`
call into the dashboard terminal.

### Never build FFmpeg commands as strings

Always the array form; never `shell=True`. Filenames are attacker-controlled in practice.

```python
subprocess.run(["ffmpeg", "-i", video_path, ...], capture_output=True)
```

### Model caching

`_get_model` reuses the loaded model and releases the old one with `gc.collect()` on a size
change. Without that, switching `large` → `tiny` leaks the large model.


---

## 7. Code style

- **File header, one line, every file** — names the responsibility and the exports:
  ```python
  # muxer.py — VideoMuxer: injects translated SRTs into MKV with ISO 639-2 tags. Exports: VideoMuxer.
  ```
  ```tsx
  // VideoList.tsx — Dashboard: recursive folder/video tree with selection. Exports: default VideoList.
  ```
  Update it whenever the file's job changes.
- **Comments explain WHY, not what.** A comment earns its place when it records a non-obvious
  constraint or a bug it prevents ("the `?` stops ffmpeg aborting on a file with no audio"). The
  existing backend docstrings are fine — leave them; don't add ceremonial ones to new code.
- **Hard limit: 400 lines per file.** Split into a sub-directory when exceeded.
- **No speculative abstractions.** Three similar lines beat a premature helper; abstract at 4+
  callsites.
- **No dead code.** An API client method with no callsite, or a backend helper with no caller, is
  a lie about what the product does. Delete it or wire it up.
- **No backward-compat code.** No shims, dual-read paths, renamed unused `_vars`, re-exports of
  removed things, or `// removed` comments. When an API shape changes, change backend and frontend
  in the same commit and delete the old path — all state is in memory, nothing needs migrating.
- **Grep before writing.** Shared pieces live in `lib/api.ts`, `lib/sse.ts`, `lib/types.ts`,
  `lib/studio-context.tsx`. If a shared piece doesn't exist, create it, then use it.

### UI conventions — describe reality, not an aspiration

The app is a **dark, dense, desktop-first studio**. There is **no semantic token layer**:
`tailwind.config.ts` has an empty `theme.extend`, so `bg-background`, `text-foreground`,
`border-border` and `bg-primary` **do not exist** and will render as nothing. Match what is there:

- Surfaces `bg-[#0a0a0a]` / `bg-[#0d0d0d]`, borders `border-white/5` / `border-white/10`.
- Accent indigo (`bg-indigo-600`, `text-indigo-400`); emerald = success, red = destructive,
  amber = warning. **Status is never colour-only** — pair it with an icon or text.
- Icons from `lucide-react`, never inline emoji in the UI. Icon-only buttons need `aria-label`
  **and** `title`.
- Dashboard `text-sm`; sidebar and dense panels `text-xs` / `text-[10px]`.
- Settings auto-apply on change — no Save buttons.
- A single choice from a fixed set is a `<select>`, not a row of buttons.
- Never leave a blank: provide loading, empty, success and error states. **Empty beats fake** — no
  placeholder art, no fabricated data.
- **A Tailwind class built at runtime ships no CSS.** `` `bg-${tone}-500` `` renders nothing; write
  complete literals (a map of whole strings is fine). Verify styling by computed style, not by
  finding the class in the DOM.
- `tailwindcss-animate` is **not installed**, so `animate-in`, `fade-in`, `slide-in-from-*` and
  `zoom-in-95` are inert. Don't add more until the plugin is.

**If you want the token layer or responsive support, that is a real task** — see the workspace `TODOLIST.MD`.
Don't half-apply it: a screen using `bg-background` today renders transparent.
