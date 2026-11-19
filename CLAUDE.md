# SubStudio: project rules (inherit the workspace CLAUDE.md)

**What this file answers:** what is specific to SubStudio, for Claude in every thread tagged `[SubStudio]`. Everything
shared lives in `../CLAUDE.md`; where they differ, this file wins. The engineering rules (verification, architecture,
patterns, code style) are in `.claude/rules/engineering.md`; committing, secrets, consoles, security and the shell
traps in `.claude/rules/git-and-secrets.md`. Both load always.

Section numbers (§N) are those of the pre-harness file and stay valid: each kept section keeps its heading,
in `SubStudio/CLAUDE.md` or `SubStudio/.claude/rules/*.md`. Map: `doc/harness-migration.md`.

## Context and goal

**SubStudio:** a local media pipeline that transcribes video with Faster-Whisper, translates the
subtitles (today: Google Gemini `gemini-3.8-flash` on branch `feat/gemini-and-stop-job`, OpenAI on `main`), and muxes
them into MKV. FastAPI backend, Next.js 14 frontend, Docker Compose, **no database**.

**Model constraint (Cyril, 2026-11-19; `doc/decisions/D-0001 local free models within half the machine.md`):** the
best models that are **free and run locally**, using **at most half the computer** (4 of 8 threads, ≤ 7 GB RAM on
this Ryzen 5 7520U / 15 GB laptop, no CUDA). A cloud API is never the default path. The target design and the
candidate models: `doc/architecture-review-2026-11-19.md`.

- **For whom:** Cyril, in three roles: processing a single video, running a batch overnight, watching the result.
- **Goal:** a library where every video carries correct, well-timed subtitles in the wanted languages, with no
  manual step between dropping a file in `/data` and playing `{stem}_SubStudio.mkv`.
- **The user process (Cyril, 2026-11-19; D-0002): `doc/user-process.md` is the product spec and wins over the
  reviews.** Select files → languages (default OV · EN · FR, remembered) → per video × language: keep a good existing
  subtitle, else download, else translate with context, else ASR + OV correction + translate; every result passes a
  sync + quality gate; the file is updated in place with a `(Sub-OV,EN,FR)` name marker (Cyril confirmed in-place
  replacement everywhere, `C:\Videos` included, 2026-11-19; only by the verified swap of « Forbidden » below).
- **Metric (inferred, not measured yet):** share of a batch that ends in a playable MKV with every requested
  track, and minutes of attention per video. Basis: the overnight-batch role; wrong if Cyril only processes
  single videos.

## 1. Functional objective — the north star of every change

**Every line of code must serve the functional objective and improve how someone actually uses the product.**

Before writing anything, answer:
1. **Who benefits?** Someone processing a single video, someone running a batch overnight, or
   someone watching the result — name the role.
2. **What can they do now that they couldn't before, or what friction is removed?**
3. **Does this make the transcribe → translate → mux pipeline faster, clearer, or more correct?**

If you cannot answer all three, the change is out of scope or needs reframing. A technically
correct fix that makes the workflow worse is still wrong.

## Phase and what it forbids
- **Phase:** build, active since 2026-11-18 (paused 2026-08-21 to 2026-11-18). Local only: no hosted deployment.
- **Test corpus (Cyril, 2026-11-19): `C:\Videos` is the media test folder.** Today: `C:\Videos\Ghosts\Saison 5\`,
  22 MKV episodes of Ghosts (2021) S05E01–E22, 14 GB, 720p/1080p, H.264/HEVC, English audio. Every video there is
  to be subtitled, and **is treated as having no subtitles**: 15 carry tracks made by an older SubStudio, which are
  not good and carry no marker telling them from release tracks.
- **Every pre-existing subtitle is untrusted by default,** embedded tracks AND sidecars (the old SubStudio wrote
  `{stem}.{lang}.srt` by default). The setting `count_existing` (« Count subtitles not made by this SubStudio ») is
  OFF by default: such a subtitle then never counts as coverage, never makes the planner skip a language, and is
  never used as a scoring reference; the UI shows the language as missing with « ignored: <lang> <embedded|sidecar> ».
  What the new SubStudio writes is recognised by ONE rule, `backend/core/provenance.py` (muxed track title
  « SubStudio {lang} », sidecars listed in the per-video manifest), and does count.
- **Forbidden:** `cleanUp=true`, or deleting or overwriting any source video by any path other than the **verified
  swap** (in `C:\Videos` too: 14 GB to re-fetch); touching anything outside `C:\Videos` beyond the exact files a test
  created. **The verified swap is the one allowed way to replace a source** (Cyril, 2026-11-19, D-0002): write the new
  file next to the source under a temporary name, verify it (same video and audio streams, duration within 1 s, every
  new subtitle track present and decodable), then atomic rename onto the source; a failed check leaves the source
  untouched and deletes only the temp file. Any other output is a new file next to the source, removed by exact name
  when a test made it. Commit and push are Claude's
  (`.claude/rules/git-and-secrets.md` §9).

## Sources of record
| Source | URL or path | Access | Notes |
|---|---|---|---|
| Code | https://github.com/CyrilFrancois/SubStudio (public), local `SubStudio/` | Cyril's GitHub account | `origin/main` is production; renamed from videosToSubtitledvideos on 2026-11-18 |
| App | http://localhost:3000 (UI), http://localhost:8000/health (API) | `docker compose up --build` | no database; state in memory |
| Test corpus | `C:\Videos` (mounted as `/data` in Docker; `SUBSTUDIO_MEDIA_ROOT` natively) | local | Cyril's test videos; rules in « Phase and what it forbids » |
| Gemini key | https://aistudio.google.com/apikey | Cyril's Google account | value lives in `SubStudio/.env` only |
| Backlog | workspace `TODOLIST.MD`, `### [SubStudio]` below the marker | Claude keeps it; Cyril's notes go above the marker | ordered by the architecture review |
| Pre-harness files | `_archive/2026-11-18_*-before-harness.md` (gitignored) | local | byte-exact copies |

## Vocabulary
| Term | Meaning |
|---|---|
| `workflowMode` | `whisper` (always transcribe), `hybrid` (sidecar SRT if found, else Whisper), `srt` (same lookup, also falls back to Whisper) |
| `stripExistingSubs` | a separate boolean, not a mode |
| `cleanUp` | **deletes the source video**, not temp files; no UI for it |
| `file_id` | the video's full path, the SSE routing key; URL-encoded exactly once |
| sidecar | an `.srt` next to the video, or in `Subs/` / `Subtitles/` |
| `{stem}_SubStudio.mkv` | the output, written next to the source |

## People
| Person | Role | Ask them about |
|---|---|---|
| Cyril | owner, only user | product direction, what to process, anything touching `cleanUp` or his library |

## Checks before calling work done
- The commands of `.claude/rules/engineering.md` §3, actually run: `tsc --noEmit` **and** `next build` (in a
  container, on a case-sensitive copy when casing matters), the backend `ast.parse`, `docker compose up --build`,
  `curl -s http://localhost:8000/health`, and the edge cases of « Boundaries that have actually bitten ».
- The reply's systems line (workspace `CLAUDE.md` §8) is, for SubStudio: what was pushed (subject line + SHA), or
  `nothing pushed: <why>`.

## 4. Working method

What is specific to SubStudio; the rest of the working method is workspace `CLAUDE.md` §4.

- **Do it yourself — hand over ONLY what genuinely requires the user.** You have a real shell,
  Docker, and the repo. Editing `.env`, restarting a container, running a build in a container,
  calling an API with a key already in `.env` — **do them.** Only these belong in the workspace `TODOCYRIL.MD`:
  an interactive login or OAuth consent, a dashboard action needing their identity (e.g. the
  Google AI Studio console), a payment, a password only they know, anything irreversible or outward-facing.
  **Committing and pushing is YOURS — see §9.** When a task is a mix, do your part first and hand
  over the smallest possible remainder, saying plainly which is which.


- **Verify before claiming.** Never report a finding as confirmed on the strength of how a
  framework "should" behave. Run it. If you cannot run it, label it explicitly as reasoned rather
  than measured, and say what would settle it. When a measurement contradicts you, say so plainly
  and correct the claim — do not quietly restate it.

- **Deep fix, never workaround** (workspace `CLAUDE.md` §4), with SubStudio's own examples:

  Two specific traps:
  1. **Asking the LLM to behave is a hope, not a fix.** The translator used to be asked to keep SRT
     indices and timestamps; now it never sees a timing (`core/subtitles`, ids only). If a value is
     *computable*, compute and enforce it in Python rather than adding another sentence to the prompt.
  2. **A detector is an alarm, not a repair.** Logging that something went wrong is right, but a
     run where it fires is still broken. Ship the detection *and* the repair.
  3. **The symptom cleared downstream.** If the error count is green but the invariant is still
     violated, the bug is alive. Three symptoms with one cause is the cause asking to be fixed —
     if you can write "each patch was a workaround for the same thing", the refactor is the task.
     A bigger fix is not a reason to defer it: say so and start it.

  Generated output is checked against its inputs: every answer is matched to the source cues *by
  id*, a batch whose id set differs is rejected, and timings come from the source only. Prompt only for what needs judgement; gate the rest deterministically.

- **A comment is not a mechanism.** "Keep these in sync" is not what keeps backend JSON and
  `types.ts` in sync — one source, or a check, is.

## Decisions
Decision records go in `doc/decisions/` (`D-NNNN title.md`, skill `decision-record`); the folder is created with the first one.
How this file was split from the pre-harness one: `doc/harness-migration.md`.
