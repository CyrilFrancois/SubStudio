# TODOLIST — SubStudio backlog

> User-managed. Claude appends findings at the very end, date-stamped. Claude never edits,
> reorders or removes existing items.

---

## Backlog

- [2026-08-21] **Abort / cancel a running job.** `api.abortAll()` and `api.cancelJob()` call
  `/api/abort` and `/api/abort/{id}`, which do not exist on the backend, and neither has a
  callsite or any UI. `translator.refine_and_translate` receives
  `task_manager=type('Task',(object,),{'is_aborted': False})` — a class literal whose flag is
  permanently `False`, so the abort check inside the translation loop can never fire. Doing this
  properly needs a real per-job task registry threaded through orchestrator → transcriber →
  translator → muxer, plus a subprocess kill for the FFmpeg/ffprobe children. Deliberately NOT
  done in the 2026-08-21 pass: it is a new feature, not a fix. The dead client methods were
  removed so nothing looks half-wired.

- [2026-08-21] **Upload an SRT from the browser.** `SubImportModal` takes a `File` object but
  never sends it anywhere; `api.uploadSubtitle()` posted to `/api/subtitles/upload`, which does
  not exist. As of 2026-08-21 the modal is honest: it only accepts a file that already sits in
  the video's folder (server-side path), and `selectedSrtPath` now actually reaches the backend.
  A real upload endpoint (`python-multipart` is already in `requirements.txt`) would let you drop
  a file from anywhere. Needs a path-traversal guard — the destination is user-supplied.

- [2026-08-21] **Embedded subtitle tracks are detected but never used.**
  `SubtitleProcessor.load_existing_subtitles` and `extract_embedded_subs` are fully implemented
  and have zero callers. The scanner probes embedded streams and the card shows an "embedded"
  badge, which implies they can be a translation source — they cannot. "Legacy: SRT Refining
  Only" silently falls through to a full Whisper run when there is no sidecar `.srt`. Wiring
  these in would make the badge tell the truth and save a transcription pass on any remux that
  already carries a text subtitle track.

- [2026-08-21] **No design system.** `tailwind.config.ts` has `theme: { extend: {} }` — none of
  `bg-background`, `text-foreground`, `border-border`, `bg-primary` exist. Every component uses
  raw hex (`bg-[#0a0a0a]`) and literal colours (`text-white`, `text-gray-500`). Defining the
  token layer would make theming possible and let the CLAUDE.md §7 rules become real rather than
  aspirational. Until then those rules are documented as aspirational, not enforced.

- [2026-08-21] **`tailwindcss-animate` is not installed.** 12 usages of `animate-in`, `fade-in`,
  `slide-in-from-*`, `zoom-in-95` across `GlobalProgress.tsx`, `SubImportModal.tsx` and
  `VideoList.tsx` are inert — the classes resolve to nothing. Either add the plugin
  (`npm i -D tailwindcss-animate` + register it) or drop the classes.

- [2026-08-21] **The app is desktop-only.** `Sidebar` is a fixed `w-80`; `VideoCard`'s control row
  is an unprefixed `grid-cols-12`; the SRT language picker and the subtitle-source tooltip are
  `group-hover`-only, so they are unreachable by touch. Below ~900px the layout is unusable.

- [2026-08-21] **No tests, no type checking on the backend.** `mypy`, `pylint` and `pytest` are
  not in `requirements.txt` and there is no config for any of them. The highest-value first tests
  would be `SubtitleProcessor.apply_offset` (timestamp arithmetic, negative clamping) and
  `VideoMuxer.process_srt_content` (block dropping past `max_duration`) — both are pure functions
  with no I/O.

- [2026-08-21] **Line-splitting logic exists three times at three widths.**
  `transcriber._build_srt_block` (35 chars), `PipelineOrchestrator.split_long_lines` (50) and
  `muxer.process_srt_content` (55) each re-implement "find the space nearest the midpoint", and
  all three run in sequence on the same text. Should be one function in `subtitle_processor.py`
  with one configured width.

- [2026-08-21] **Concurrent batches share one Whisper model.** Two overlapping `POST /api/process`
  calls both reach `VideoTranscriber._get_model`; if they request different sizes they will
  thrash — unload/reload per file. `PipelineOrchestrator.active_jobs` guards a single *path*, not
  the model. A lock around `_get_model`, or a single batch queue, would fix it.

- [2026-08-21] **No `LICENSE` file.** `readme.md` claims MIT in two places and says "See LICENSE".
  Add the MIT text or drop the claim.

- [2026-08-21] **`/api/scan` cache is global, not per-path.** `PipelineOrchestrator.get_files`
  keys its 2-second debounce on time only, not on `target_path`. Two scans of *different*
  folders within 2s return the first folder's results. Harmless today (the UI only ever scans
  `/data`) but wrong the moment folder navigation is used — `actions.navigate` already exists and
  passes a path.

- [2026-08-21] **`VideoJob.src` is accepted and ignored.** The frontend sends a source language;
  the backend never reads it and never passes `language=` to `WhisperModel.transcribe`, so
  detection is always automatic. Either wire it through or remove it from the payload. The
  Sidebar's SRC dropdown is already hard-disabled to `auto`, which is at least honest.

- [2026-08-21] **`syncOffset` has no UI.** The backend applies it correctly
  (`SubtitleProcessor.apply_offset`) and the payload carries it, but nothing can set it, so it is
  always `0`. A per-card number input would make the existing backend capability reachable.

- [2026-08-21] **No lockfile.** `frontend/` has no `package-lock.json`, so every image build resolves
  dependencies afresh and two builds can differ. A `package-lock.json` was generated incidentally
  during verification on 2026-08-21 and deliberately NOT committed — adding one changes how installs
  behave (`npm ci` becomes possible, `npm install` starts respecting pins) and that is your call.
  If you want it: run `npm install` in `frontend/`, commit the lockfile, and switch the Dockerfile
  to `npm ci`.
