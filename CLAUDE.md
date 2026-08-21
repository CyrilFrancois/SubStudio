# Claude Code — SubStudio Project Instructions

**SubStudio:** a local media pipeline that transcribes video with Faster-Whisper, translates the
subtitles with `gpt-4o-mini`, and muxes them into MKV. FastAPI backend, Next.js 14 frontend,
Docker Compose, **no database**.

---

## 1. Functional objective — the north star of every change

**Every line of code must serve the functional objective and improve how someone actually uses the product.**

Before writing anything, answer:
1. **Who benefits?** Someone processing a single video, someone running a batch overnight, or
   someone watching the result — name the role.
2. **What can they do now that they couldn't before, or what friction is removed?**
3. **Does this make the transcribe → translate → mux pipeline faster, clearer, or more correct?**

If you cannot answer all three, the change is out of scope or needs reframing. A technically
correct fix that makes the workflow worse is still wrong.

---

## 2. State of evolution — always explicit

**Before starting**, state what exists today: which feature is present and in what state
(working / broken / partially implemented / missing), what bugs are present (error message, file,
line), what the UI or API does versus what it should do.

**Then state the target**: what exact behaviour changes, what new capability exists, what bug no
longer occurs, and what could be better than what was asked.

**Gap = the work. Make the gap explicit before touching code.**

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

For each FFmpeg, ffprobe or OpenAI call: name what happens on failure, and make sure the answer is
not "nothing". A swallowed error looks like success. If it cannot be handled, log at ERROR and
re-raise — never absorb it into an empty result.

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

Whisper, OpenAI and FFmpeg calls are **synchronous**. `run_batch` is `async`. Calling them
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
docker compose exec -T backend env | grep OPENAI_API_KEY
```

---

## 4. Working method

**Before touching any code:** read the current state of every file you plan to modify — never edit
from memory or from a summary.

### THOSE NEXT POINTS ARE REALLY IMPORTANT:

- **Bias to action — if you see something to be done, do it. Don't ask.** When the work exposes a
  related bug, dead code, or an obvious next step within the task's intent, complete it in the
  same pass. Only pause for a decision that is genuinely the user's: an irreversible or
  outward-facing action, or a product-direction fork with no sensible default.

- **Do it yourself — hand over ONLY what genuinely requires the user.** You have a real shell,
  Docker, and the repo. Editing `.env`, restarting a container, running a build in a container,
  calling an API with a key already in `.env` — **do them.** Only these belong in `TODOCYRIL.md`:
  an interactive login or OAuth consent, a dashboard action needing their identity (e.g. the
  OpenAI console), a payment, a password only they know, anything irreversible or outward-facing.
  **Committing and pushing is YOURS — see §9.** When a task is a mix, do your part first and hand
  over the smallest possible remainder, saying plainly which is which.

- **Deep fix, never workaround. ALWAYS.** Fix the root cause; do not hide the symptom.
  **The test of a real root fix:** name the mechanism that made the bug *impossible*, not less
  likely. If the honest answer is "it usually works now" or "a later step cleans it up", it is a
  workaround wearing a fix's clothes — say so and finish the job.

  Two specific traps:
  1. **Asking the LLM to behave is a hope, not a fix.** `translator._build_system_prompt` tells the
     model to preserve indices and timestamps. It mostly does. If a value is *computable*, compute
     and enforce it in Python rather than adding another sentence to the prompt.
  2. **A detector is an alarm, not a repair.** Logging that something went wrong is right, but a
     run where it fires is still broken. Ship the detection *and* the repair.

- **Verify before claiming.** Never report a finding as confirmed on the strength of how a
  framework "should" behave. Run it. If you cannot run it, label it explicitly as reasoned rather
  than measured, and say what would settle it. When a measurement contradicts you, say so plainly
  and correct the claim — do not quietly restate it.

- **Always finish the work in hand before moving on.** Complete it, verify it, record it in
  `DONELIST.md`, then start the next thing. No dangling partial edits across files.

- **Ask, don't assume.** Act freely on a clear next step within the task's intent; when the
  **intent, architecture, or requirements themselves** are ambiguous, stop and ask first.

- **Don't touch unrelated code.** Surface the improvement in `TODOLIST.md` instead.

- **Flag uncertainty explicitly.** Name the uncertainty and the assumption you'd otherwise make.

- **A global comment on how you work goes STRAIGHT into this file, by you, immediately.** When the
  user tells you what they expect of your behaviour, that is permanent, not a note for the turn.
  Write it into `CLAUDE.md` in the same reply and say you have done so. The user must never have
  to give the same feedback twice. The same applies to a rule you learn the hard way — write it as
  the rule, not the anecdote.

- **Suggest better ways — including strategic ones.** Raise it as a suggestion, not a silent
  rewrite; the final call stays the user's.

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
- `backend/core/translator.py` — `get_context_profile()` builds a story bible from the filename;
  `refine_and_translate()` sends 30 SRT blocks per request at temperature 0.2, retrying twice.
- `backend/core/muxer.py` — copies existing streams, appends the AI tracks with ISO 639-2 tags.
  Output is `{stem}_SubStudio.mkv` **next to the source**, not in a separate folder.
- `backend/core/subtitle_processor.py` — timestamp offsetting (clamped at 0). Its
  `load_existing_subtitles` / `extract_embedded_subs` are **currently unused** — see `TODOLIST.md`.
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
| `GET /api/scan?target_path=/data` | `{"files": [...]}`. Cached 2 s — **globally, not per path** (known bug, see `TODOLIST.md`). |
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

`.env` (gitignored; template in `.env.example`): `OPENAI_API_KEY`, `NEXT_PUBLIC_MEDIA_PATH`,
`NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_LANGUAGES`. Whisper models cache in the
`whisper_model_cache` volume.

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
- `tailwindcss-animate` is **not installed**, so `animate-in`, `fade-in`, `slide-in-from-*` and
  `zoom-in-95` are inert. Don't add more until the plugin is.

**If you want the token layer or responsive support, that is a real task** — see `TODOLIST.md`.
Don't half-apply it: a screen using `bg-background` today renders transparent.

---

## 8. What NOT to do

- Never use `git add .` or `git add -A` — stage specific paths only
- Never amend, rebase or force-push a commit already on `origin`
- Never bypass hooks (`--no-verify`)
- Never commit `.env` or `TODOCYRIL.md` — both are gitignored. **A `.gitignore` entry does not
  cover an already-tracked file**; if one is tracked, `git rm --cached` it
- Never conclude the frontend is fine from `tsc` alone — run `next build`
- Never call blocking Whisper/OpenAI/FFmpeg code directly from an `async` function
- Never swallow an exception in a background task — log at ERROR and report via SSE
- Do not leave a file over 400 lines without splitting it
- Do not add features beyond what was requested

---

## 9. Session workflow

1. **Current state** — read the files; state what exists and what's broken
2. **Target state** — what will be true after, and who benefits
3. **Gap analysis** — every step, every file, the data flow end to end
4. **Implement** — one pass, no partial implementations
5. **Verify** — the commands in §3, actually run, plus the edge cases that apply
6. **One-line summary** — what changed and what's next; don't restate the diff
7. **Update `DONELIST.md`** — mandatory; prepend at the top
8. **Commit and push**
9. **Think ahead** — append findings to `TODOLIST.md`

### Committing

`origin/main` is production. Before every push: it builds (`tsc` **and** `next build`), the work
is finished, `DONELIST.md` has the entry, the diff has no secrets (`sk-`, `sk_`, `whsec_`, `AKIA`,
`postgres://`), and every path is staged explicitly.

Message format — `<type>: <feature> — <why it matters>`, where `type` is one of
`feat` · `fix` · `perf` · `refactor` · `docs` · `chore`.

```
fix: SSE progress — encode file_id once so events reach the dashboard
fix: FFmpeg muxing — copy existing subtitle streams so bitmap tracks survive
perf: pipeline — run Whisper/OpenAI/FFmpeg off the event loop with to_thread
```

A push is not finished until you know the outcome. If you can't watch it, say so plainly.

### How every reply ENDS — three sections, in this order, always

1. **What I'm least confident about** — named uncertainties in THIS change, not generic caveats.
2. **What you're probably missing** — consequences, second-order effects, adjacent risks.
3. **What I suggest next** — a concrete recommendation (finish current work, then uncertainties,
   then the next `TODOLIST.md` item).

---

## 10. Project log files

### `DONELIST.md` — completed work (Claude-managed)

Mandatory: a task is not finished until it has an entry. One `## Title` heading plus a short
paragraph, **prepended at the top**. Never edit an existing entry — if a later change corrects an
earlier one, write a new entry. Each entry carries its verification state on its own line:

- `**Verified locally:** <what was run and what it returned>`
- `**Verified in production on <YYYY-MM-DD>:** <what was probed and what it returned>`
- `**Not verified** (<why, and what would settle it>)`

Trim from the bottom when the file passes ~100 lines.

### `TODOLIST.md` — the backlog (user-managed)

Do not edit, reorder or remove the user's items. You may **append** at the very end. Every
appended entry starts with a date stamp `[YYYY-MM-DD]`.

### `TODOCYRIL.md` — what the user must do (Claude-managed, gitignored)

Only genuinely-theirs actions (§4). Every entry is a follow-along runbook — the user should never
have to think or search. Keep it short and current; date-stamp every item; delete an item as soon
as they confirm it's done.
