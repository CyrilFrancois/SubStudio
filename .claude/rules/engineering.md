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
# Frontend — no Node on the host, so run it in a container
docker run --rm -v "$PWD/frontend:/app" -w /app node:18-alpine ./node_modules/.bin/tsc --noEmit
docker run --rm -v "$PWD/frontend:/app" -w /app node:18-alpine ./node_modules/.bin/next build

# Backend — syntax at minimum; there is no mypy/pylint/pytest in requirements.txt
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

Concrete trap in this codebase: `translator.refine_and_translate` catches a failed batch and
appends **the untranslated source text** as a fallback. That is deliberate (one bad batch should
not lose the file) but it means a wholly-failed translation silently returns the original
language. If you touch that path, keep the ERROR log.

### Boundaries that have actually bitten this project

- **A video with no audio track.** `-map 0:a` aborts the whole mux with "matches no streams".
  Every stream map must carry `?`.
- **Bitmap subtitles (PGS/VobSub).** `-c:s srt` cannot convert them — FFmpeg refuses with
  "Subtitle encoding currently only possible from text to text or bitmap to bitmap". Existing
  streams must be `-c:s copy`; only newly-added SRTs get `-c:s:N srt`.
- **Paths with spaces and non-ASCII.** `file_id` is a full path used as the SSE routing key. It
  must be URL-encoded exactly **once** — encoding twice yields a key no emitter ever writes to,
  and the stream silently delivers nothing.
- **Subtitles past the end of the video.** `muxer.process_srt_content` drops them; a player will
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

- **A live AI check runs on the shipped model and code path** — call `refine_and_translate` with
  the configured `GEMINI_MODEL`, never a hand-built prompt or another model.
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

- `backend/main.py` — `PipelineOrchestrator` + the four routes. Blocking steps dispatched with
  `asyncio.to_thread`; the batch runs as an `asyncio.create_task`.
- `backend/core/scanner.py` — recursive `os.scandir` of `/data`; per-video ffprobe for embedded
  subtitle streams; sidecar lookup in `.`, `Subs/`, `Subtitles/`. Guards against escaping
  `base_path`.
- `backend/core/transcriber.py` — Faster-Whisper, lazy model load, `gc.collect()` on size change.
  Word timestamps grouped into blocks on sentence end or 70 chars.
- `backend/core/translator.py` — Gemini over its REST API (`httpx`, no SDK), `thinkingLevel: low`.
  `get_context_profile()` builds a story bible from the filename; `refine_and_translate()` sends
  30 SRT blocks per request at temperature 0.2, retrying twice. `_generate` raises on any HTTP
  error, missing candidate, or `finishReason != STOP`.
- `backend/core/muxer.py` — copies existing streams, appends the AI tracks with ISO 639-2 tags.
  Output is `{stem}_SubStudio.mkv` **next to the source**, not in a separate folder.
- `backend/core/subtitle_processor.py` — timestamp offsetting (clamped at 0). Its
  `load_existing_subtitles` / `extract_embedded_subs` are **currently unused** — see the workspace `TODOLIST.MD` (« [SubStudio] backlog »).
- `backend/core/events.py` — `EventManager`, one broadcast channel per `file_id`, thread-safe.

### Frontend (Next.js 14, TypeScript)

- `src/app/page.tsx` — owns `StudioState`, SSE subscriptions, batch dispatch.
- `src/lib/studio-context.tsx` — `StudioContext` + `useStudio`. **This must not live in
  `page.tsx`**: the App Router rejects non-standard exports from a page module and `next build`
  fails with "StudioContext is not a valid Page export field".
- `src/lib/types.ts` — single source of truth; must mirror the backend's actual JSON.
- `src/lib/api.ts` — REST client. `src/lib/sse.ts` — EventSource wrapper.
- `src/components/dashboard/` — `VideoList`, `VideoCard`, `GlobalProgress`, `SubImportModal`.
- `src/components/layout/Sidebar.tsx` — global settings and batch launch.

### API contracts

| Route | Notes |
|---|---|
| `GET /api/scan?target_path=/data` | `{"files": [...]}`. Cached 2 s — **globally, not per path** (known bug, see the workspace `TODOLIST.MD`). |
| `POST /api/process` | `{"status":"accepted","count":N}`, returns immediately. |
| `GET /api/events/{file_id:path}` | SSE. `file_id` = full path, encoded **once**. |
| `GET /health` | `{"status":"online"}`. |

**SSE frames are unnamed** — everything arrives on the default `message` event with a `type`
discriminator of `"status"` or `"log"`. There is no `event:` line, so `addEventListener("transcribing", …)`
can never fire. Progress runs 5 → 100.

**`workflowMode`** is one of `whisper` (always transcribe), `hybrid` (sidecar SRT if found, else
Whisper) or `srt` (same lookup, and also falls back to Whisper). `stripExistingSubs` is a separate
boolean, not a mode.

**`cleanUp` deletes the source video.** It is not "remove temp files". There is no UI for it.

### Configuration

`.env` (gitignored; template in `.env.example`): `GEMINI_API_KEY`, optional `GEMINI_MODEL`
(default `gemini-3.8-flash`; must be a 3.x model because the request sends `thinkingLevel`),
`NEXT_PUBLIC_MEDIA_PATH`, `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_LANGUAGES`, `WHISPER_MODEL`.
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
