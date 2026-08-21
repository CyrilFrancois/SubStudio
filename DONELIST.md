# DONELIST — completed work, newest first

## Documentation rewrite — `readme.md` and `CLAUDE.md` against the actual codebase
**Verified locally:** every claim re-checked against source by grep; the `+0.4s offset`,
`/data/outputs/`, `GET /scan`, `pure`/`sidecar`/`strip_existing` and named-SSE-event claims were
confirmed absent from the code before removal.

`CLAUDE.md` was largely a template imported from an unrelated Go/SQL/Stripe project: it referenced
`factory_ai_verify.go`, `GenerateSections → ApplySections → GenerateTriggers`, `.up.sql`/`.down.sql`
migrations, Go struct fields, Stripe payouts, JPY minor units, Cloud Run revisions and Gemini token
accounting — none of which exist here. It also mandated `mypy`/`pylint`/`pytest` (absent from
`requirements.txt`), cited a non-existent "§15" three times, required three log files that did not
exist, asserted `.env` was gitignored when it was tracked, and prescribed semantic Tailwind tokens
(`bg-background`, `text-foreground`) that `tailwind.config.ts` does not define — following which
would render UI transparent. Rewritten to describe what is actually here, including a §3 that
records the verification traps found this session.

`readme.md` carried fourteen factual errors, the most consequential being `cleanUp` documented as
"removes temporary audio files" when it deletes the source video, and a "+0.4s synchronization
offset" headline feature that does not exist in any form.

## Frontend — SSE progress, log terminal, build failure and type errors
**Verified locally:** `tsc --noEmit` and `next build` both run on a case-sensitive filesystem
(source copied into container-local ext4, not a Windows bind mount).

- **Progress bars were frozen at 5% forever.** `page.tsx` ran `encodeURIComponent` on the file id
  and `sse.ts` encoded it again; the backend decoded `%252F…` to a key no emitter writes to, so
  the stream delivered nothing. Now encoded exactly once, in `sse.ts` only. Because
  `Sidebar.isAnyFileProcessing` gates the PROCESS button on status, this also latched the whole UI
  after one batch.
- **`next build` was impossible.** `page.tsx` exported `StudioContext` and `useStudio`, which the
  App Router rejects ("not a valid Page export field"). Moved to `src/lib/studio-context.tsx`;
  four components repointed. Invisible before because the Dockerfile runs `next dev`.
- **`videoCard.tsx` → `VideoCard.tsx`.** `VideoList` imported `./VideoCard`; confirmed on
  container-local ext4 that the file does not exist under that name on a case-sensitive host.
- **Execution terminal was permanently empty.** `state.logs` was never written — `page.tsx`
  handled only `type: "status"` and dropped every `type: "log"` frame the backend sends. Now
  appended per file, capped at 400 lines. Removed the "(NOT WORKING…)" placeholder.
- **Manual SRT import never reached the backend.** The modal wrote `subtitleInfo.externalPath`
  while `POST /api/process` reads `selectedSrtPath`, which stayed `null`. Both are now set, and
  scan seeds `selectedSrtPath` from any detected sidecar.
- **Six `tsc` errors cleared** by making `types.ts` match the backend: added `processing` to
  `ProcessingStatus`, added `selectedSrtPath` to `VideoFile`, and corrected `SubtitleInfo` from
  `{languages[], count, srtPath}` to the shape `scanner.py` actually emits
  (`{language, externalPath, embeddedTracks}`).
- **Removed dead client code** — `abortAll`, `cancelJob` and `uploadSubtitle` called
  `/api/abort`, `/api/abort/{id}` and `/api/subtitles/upload`, none of which exist on the backend,
  and none of which had a callsite. Logged in `TODOLIST.md` as real features to build.

## Backend — event loop, thread-safety and FFmpeg stream handling
**Verified locally:** blocking measured with a concurrent probe (7023 ms before, see below);
muxer exercised against real fixtures built with ffmpeg.

- **The pipeline held the asyncio event loop.** `run_batch` is `async` but called synchronous
  Whisper, OpenAI and FFmpeg code directly. Measured: a `/health` probe issued 1 s into an 8 s job
  waited **7023 ms**. During a real transcription nothing else could be served — no SSE flush, no
  `/api/scan`, no healthcheck. All four blocking calls now go through `asyncio.to_thread`, and
  `/api/scan` too (it shells out to ffprobe per file).
- **`POST /api/process` did not return until the whole batch finished.** Uvicorn withholds the
  response until `BackgroundTasks` complete. Replaced with `asyncio.create_task` plus a retained
  reference and a done-callback that logs exceptions — otherwise a crashed batch would vanish
  silently.
- **`asyncio.Queue` is not thread-safe**, and moving the pipeline off-loop meant `emit()` now
  fires from worker threads. `EventManager` binds the loop at startup and marshals every broadcast
  back with `call_soon_threadsafe`. Added a 15 s `: keepalive` so idle streams are not dropped.
- **Muxing failed outright on most BluRay remuxes.** `-map 0` with `-c:s srt` asks FFmpeg to
  convert image-based subtitles (PGS/VobSub) to text, which it refuses. Existing streams are now
  `-c:s copy`; only the newly generated SRTs get `-c:s:N srt`. Verified: a fixture with an
  existing `eng` track muxed correctly, keeping `eng` and adding `fra`/"AI FR".
- **Muxing aborted on a video with no audio track.** `-map 0:a` errors with "matches no streams".
  All maps now carry `?`. Verified: old form fails with FFmpeg's own "add a trailing '?'" message,
  new form succeeds.

## Repository hygiene — tracked `.env`, bytecode, ignore rules
**Verified locally:** `git ls-files` confirmed `.env` was tracked; all four committed revisions
inspected and each contains only the placeholder `[YOUR GPT KEY HERE]`.

`.gitignore` listed `.env`, but the file was committed in `87f159a` before that rule existed and
**a `.gitignore` entry has no effect on an already-tracked file**. The working copy held a live
`sk-proj-…` key, so the next `git commit -a` would have published it. Untracked with
`git rm --cached`; added `.env.example` as the committed template. Also untracked eight
`__pycache__/*.pyc` files and added `node_modules/`, `.next/`, `__pycache__/` and `TODOCYRIL.md`
to `.gitignore`. Key rotation is the user's to do — `TODOCYRIL.md` item 1.
