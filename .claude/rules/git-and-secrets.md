# SubStudio: committing, secrets, consoles, security, several sessions

Moved verbatim from the pre-harness `SubStudio/CLAUDE.md` (2026-11-18); same authority as `SubStudio/CLAUDE.md`.
This project sets its own git flow: commit and push are Claude's (workspace `code-and-data.md` « Version control »
allows it). Section numbers (§N) are those of the pre-harness file and stay valid: each kept section keeps its heading,
in `SubStudio/CLAUDE.md` or `SubStudio/.claude/rules/*.md`. Map: `doc/harness-migration.md`.

---

## 8. What NOT to do

- Never use `git add .` or `git add -A` — stage specific paths only
- Never amend, rebase or force-push a commit already on `origin`
- Never bypass hooks (`--no-verify`)
- Never commit `.env`, `SECRETS.MD` or `TODOCYRIL.md` — all gitignored. **A `.gitignore` entry does not
  cover an already-tracked file**; if one is tracked, `git rm --cached` it
- Never conclude the frontend is fine from `tsc` alone — run `next build`
- Never call blocking Whisper/Gemini/FFmpeg code directly from an `async` function
- Never print a secret value into the transcript (§11)
- Never edit `backend/*.py` while a test batch runs — `--reload` kills it
- Never invent URLs, console paths or IDs — only ones from the user, the code, or a live lookup
- Never swallow an exception in a background task — log at ERROR and report via SSE
- Do not leave a file over 400 lines without splitting it
- Do not add features beyond what was requested

---

## 9. Session workflow

1. **Current state** — read the files; state what exists and what's broken
2. **Target state** — what will be true after, and who benefits
3. **Gap analysis** — every step, every file, the data flow end to end. Pre-flight: what is the
   flow (scanner → orchestrator → transcriber/translator/muxer → `on_progress` → `EventManager`
   → SSE → `sse.ts` → `types.ts` → component)? What breaks if a step is missing? Which
   downstream consumer is unchecked? Order changes to a shared shape so no intermediate state is
   broken: backend producer → route JSON → `types.ts` → `api.ts`/`sse.ts` → component. After a
   removal, `grep -r "<symbol>" frontend/src backend` must return nothing.
4. **Implement** — one pass, no partial implementations
5. **Verify** — the commands in §3, actually run, plus the edge cases that apply
6. **One-line summary** — what changed and what's next; don't restate the diff
7. **Update the workspace `DONELIST.MD`** — mandatory: a `## [YYYY-MM-DD] [SubStudio] Title` entry with its status line (workspace `CLAUDE.md` §6), added ONLY through `python3 -I "$WS_TOOLS/log_entry.py" add "$WS_LOGS/DONELIST.MD" --file <entry.md>`, never by hand
8. **Commit and push**
9. **Think ahead** — append findings below the marker of the workspace `TODOLIST.MD`, under `### [SubStudio]`

### Committing

`origin/main` is production. Before every push: it builds (`tsc` **and** `next build`), the work
is finished, the workspace `DONELIST.MD` has the entry, the diff has no secrets (`AQ.` and `AIza` (Gemini),
`sk-`, `sk_`, `rk_`, `whsec_`, `ghp_`, `github_pat_`, `AKIA`, `postgres://`, `password`), and
every path is staged explicitly. A key must never be named `NEXT_PUBLIC_*` — that is baked into
the public bundle.

- **Read every hunk before staging** (`git diff <path>`) — the file may hold another session's
  work (§15). Commit a file only when its whole diff is yours.
- Read `git diff --cached --stat` right before committing; refuse if any staged file contains a
  conflict marker (`^(<<<<<<<|=======|>>>>>>>)`).
- Write the message to a scratchpad file and `git commit -F <file>` — a PowerShell here-string
  typed into the Bash tool commits a literal `@` as the subject.
- **One feature per commit**, so a revert is surgical.
- After the push, confirm `git ls-remote origin refs/heads/main` equals `git rev-parse HEAD`, then
  report the subject line(s) and SHA. Pushed is not running: that needs `docker compose up --build`.
- Never `pull --autostash` / `stash` with someone else's work in the tree; stash only your own paths.
- **Still ask first** before committing a product-direction fork, anything that deletes user data
  (anything touching `cleanUp`), or something you flagged as uncertain.

Message format — `<type>: <feature> — <why it matters>`, where `type` is one of
`feat` · `fix` · `perf` · `refactor` · `security` · `docs` · `chore`. Name the feature, not the diff.

```
fix: SSE progress — encode file_id once so events reach the dashboard
fix: FFmpeg muxing — copy existing subtitle streams so bitmap tracks survive
perf: pipeline — run Whisper/Gemini/FFmpeg off the event loop with to_thread
```

A push is not finished until you know the outcome. If you can't watch it, say so plainly.

---

## 11. Secrets — never in the transcript

**`.env` is the only home of SubStudio's local credentials** (compose reads it). They are not copied into the
workspace `SECRETS.MD`: a second copy would drift. A SubStudio value given in chat anyway is moved to `.env` by
script at once, never to `SECRETS.MD`. Everything else about secrets is workspace `CLAUDE.md` §5.

- **Read a value only into a shell variable, in the same command that uses it:**
  `V=$(sed -n 's/^GEMINI_API_KEY=//p' .env)`. Pass it on via header or environment, never argv.
- **Verify by names, counts and fingerprints:** `grep -oE '^[A-Z_]+=' .env`;
  `printf %s "$V" | wc -c`; `printf %s "$V" | sha256sum | cut -c1-12`. Compare raw and after
  `tr -d '[:space:]'` — a pasted value carries its line break, and `.env` on Windows may be CRLF.

---

## 12. Shell traps on this machine

Only the traps not (yet) in the workspace `windows-host.md` / `code-and-data.md`; the rest moved there.

- **Stopping a server by matching its command line also matches the shell that holds the pattern.** On 2026-11-19 a
  `Get-CimInstance Win32_Process | Where CommandLine -like '*--port 8011*'` script, written by a heredoc in the same
  Bash call, killed that Bash call too (exit 255, the rest of the command never ran). Stop a server by its PID
  (record `$!` or the PID of the process listening on the port: `Get-NetTCPConnection -LocalPort N`), never by a
  command-line pattern; and lease the port first (`threads.py lease port-<n>`) so the PID can only be yours.
- **A guard command is never piped.** `threads.py lease heavy | tail -1` hides the refusal AND its exit code (the
  pipe returns `tail`'s 0), so on 2026-11-19 Whisper ran 83 s inside another thread's heavy lease. Run the lease on
  its own and chain the work with `&&` (`threads.py lease heavy --minutes 30 && python -m bench...`). Same for a
  PowerShell script deleting or replacing files: `-ErrorAction Stop` (or `$ErrorActionPreference = 'Stop'`), else a
  failed step still exits 0 and the `&&` after it runs.
- **A command that timed out into the background is still running.** Stop it; and a stopped task
  may not stop its script — long loops check a stop file, and you confirm nothing still writes.
- **An interpreter that is found is not one that runs** — the Windows Store `python3` stub prints an
  advert. Prove it with a one-line round trip. JSON is edited with a parser, never `sed`.
- **Encoding:** Git Bash typing is Latin-1, so an accent in `curl -d` sends invalid UTF-8 — write
  non-ASCII request bodies with Write and send `--data-binary @file`; check with `od -c` (`303 251`,
  not `351`). PowerShell 5.1 reads ANSI unless `-Encoding UTF8`, and `Set-Content -Encoding utf8`
  writes a BOM. After a scripted edit of a non-ASCII file, `grep -c $'\xef\xbf\xbd'` must be 0.
- **Git Bash rewrites leading-slash args** (`-w /app` → `C:/Program Files/Git/app`): prefix
  `MSYS_NO_PATHCONV=1` or write `//app`. Inside containers use `sh -c`, never `sh -lc`.
- A thread silent overnight is a Windows restart until proved otherwise
  (`Get-WinEvent -FilterHashtable @{LogName='System'; Id=1074}`) — and the in-memory batch died with it.

---

## 13. Telling the user WHERE

Only the SubStudio consoles and URLs; the general rules are workspace `CLAUDE.md` §7.

- **Every page is a full, clickable URL** (`http://localhost:3000`, `http://localhost:8000/health`,
  `https://aistudio.google.com/apikey`), never a path or a description. Never invent one.
- **Say how a change takes effect:** `.env` needs `docker compose down && docker compose up --build`;
  `NEXT_PUBLIC_*` needs a rebuild.

---

## 14. Security

- **The UI is never the security.** Every route receiving a path (`/api/process`,
  `/api/events/{file_id:path}`, `/api/scan?target_path=`) resolves it (`os.path.realpath`) and
  checks it is under `/data`, server-side, deny by default. `cleanUp` deletes the file it is given:
  a client path plus a delete is arbitrary file deletion.
- **Uploaded SRTs are hostile:** size-capped, decoded with an explicit encoding, never written
  outside `/data`.
- **Logs are UI** — the log bridge mirrors every `logger.*` call into the dashboard. No secrets, no
  full request headers, terse errors.
- **CORS** is an explicit origin allowlist, never `*` with credentials.
- **Dependencies are attack surface:** before adding one, check it is maintained; prefer the stdlib.
  Run `pip-audit` / `npm audit` (in a container) and resolve High/Critical.
- **Audit ritual** when touching paths, deletion or keys: secret sweep of repo, built bundle and
  history (`git log --all -p -- .env`); malformed-input pass on the four routes (`..`, absolute
  paths outside `/data`, huge bodies, unicode); dependency audit; findings appended to the workspace `TODOLIST.MD`.

---

## 15. Several sessions, one checkout

Other Claude sessions may be editing this same tree. **Someone else's work in `git status` is
information, not mess:** never `git checkout --`, stash, delete or commit a file you did not write.

- Read every hunk of `git diff <path>` before staging. If a file mixes your change with another
  session's, don't commit it — say so and wait.
- A compile failure may not be yours: check `git status --short` for an untracked file still being
  written before chasing a missing symbol.
- Re-read before editing; prefer Edit (fails loudly on a stale anchor) over Write.
- The shared index can change under you: re-check `git diff --cached --stat` immediately before
  committing, and `git show --stat HEAD` after.
- Guard every `cd`: `cd "$W" || exit 1` — a failed `cd` followed by `;` runs git in the wrong place.
- Uncommitted edits to `CLAUDE.md` reach every session opened in this checkout.
- **A thread that needs its own checkout makes a worktree inside this repo:**
  `git worktree add .claude/worktrees/<name> -b <branch>` (gitignored; workspace CLAUDE.md §0, enforced by
  `hook_worktree_guard.py`), never `../SubStudio-sessions/`. Once its branch is merged, `git worktree remove` it in
  the same pass; bench outputs other work needs go to main's ignored `bench/clips/` first. (2026-11-21: 15 stale
  worktrees removed, 14 of them merged.)
