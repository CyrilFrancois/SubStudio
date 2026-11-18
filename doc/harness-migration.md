# SubStudio: how the pre-harness CLAUDE.md was split (2026-11-18)

**What this answers:** where each section of SubStudio's old single `CLAUDE.md` (648 lines, archived locally in
`_archive/2026-11-18_CLAUDE-before-harness.md`) went when SubStudio moved under the workspace harness, and why.
**For:** Cyril, and any Claude thread that wonders where an old rule went. Every removed or reworded line is listed
verbatim, with its reason, in `doc/harness-migration-dropped.md`; `scripts/coverage.py` of the harness proves the
files together lose no line (506 old non-empty lines, 0 missing).

**Why split it:** once Claude is launched from the workspace root, the workspace `CLAUDE.md` already carries the
general working rules. Keeping them twice made two copies that drift (§4 here and workspace §4 already said the same
thing in different words). Only what names SubStudio stays in the project.

## Old section → destination → why

| Old § | Destination | Bucket and why |
|---|---|---|
| title, intro (l. 1-5) | `CLAUDE.md` « Context and goal » (intro verbatim, new title) | project |
| §1 Functional objective | `CLAUDE.md` (verbatim) | project: the three questions name SubStudio's roles |
| §2 State of evolution | dropped | already in the harness: workspace §2 |
| §3 Verification | `.claude/rules/engineering.md` (verbatim, always loaded) | project: its commands, `tsc` vs `next build`, Windows bind-mount casing, Gemini `finishReason`, the boundaries that have bitten |
| §4 Working method | mostly dropped; `CLAUDE.md` « 4. Working method » keeps « Do it yourself » (its Docker/.env/AI Studio detail), the deep-fix examples and « A comment is not a mechanism » | already in the harness (workspace §4); the SubStudio examples are project-specific |
| §5 Architecture, §6 Key patterns, §7 Code style and UI conventions | `.claude/rules/engineering.md` (verbatim; 3 « see `TODOLIST.md` » pointers reworded to the workspace `TODOLIST.MD`) | project |
| §8 What NOT to do | `.claude/rules/git-and-secrets.md` (verbatim) | project |
| §9 Session workflow and Committing | `.claude/rules/git-and-secrets.md` (verbatim; 3 lines reworded to the workspace logs) | project: commit and push are Claude's here, `origin/main` is production, the secret-prefix sweep, `NEXT_PUBLIC_*`. Overrides the workspace « Version control », which allows a project its own flow |
| §9 reply shape | dropped, reworded as workspace §8; the systems line (pushed subject + SHA) kept in `CLAUDE.md` « Checks » | already in the harness |
| §10 Project log files | dropped | Cyril's workspace decision (2026-11-18): logs move to the workspace root (workspace §6, `log-files.md`) |
| §11 Secrets | `.claude/rules/git-and-secrets.md` keeps `.env` as the home and the read-into-a-variable / fingerprint commands; the rest dropped as workspace §5 | `.env` stays: docker compose reads it, and a copy in `SECRETS.MD` would drift. So a SubStudio value pasted in chat goes to `.env`, not `SECRETS.MD`: this overrides workspace §5 for this project |
| §12 Shell traps | `.claude/rules/git-and-secrets.md` keeps the lines not yet in the workspace layer (timed-out command still running, JSON with a parser, PowerShell ANSI, `sh -c` never `sh -lc`, « the in-memory batch died with » a Windows restart); the rest dropped as `windows-host.md` / `code-and-data.md` | general rules kept verbatim here too; Cyril approved promoting them, and they were added to the workspace `code-and-data.md` / `windows-host.md` and the harness repo on 2026-11-18 (harness commit 6887c2c, CI green) |
| §13 Telling the user WHERE | `.claude/rules/git-and-secrets.md` keeps SubStudio's URLs and how config takes effect; the rest dropped as workspace §7 | project |
| §14 Security | `.claude/rules/git-and-secrets.md` (verbatim) | project |
| §15 Several sessions, one checkout | `.claude/rules/git-and-secrets.md` (verbatim) | mostly project-repo practice; « read every hunk », « guard every `cd` », the stash and `ls-remote` rules were also promoted to the workspace layer and the harness repo (2026-11-18, 6887c2c) |

## What else moved

- **`DONELIST.md`** (5 entries) → end of the workspace `DONELIST.MD`, titled `## [YYYY-MM-DD] [SubStudio] …`. Dates
  from git (4 entries match the 2026-08-21 commits) and, for the uncommitted Gemini entry, from its files' timestamps
  (2026-11-08). Bodies verbatim; « **Verified locally:** » mapped to « **Verified:** locally: », the wording the
  workspace Stop hook accepts.
- **`TODOLIST.md`**: Cyril's one note (the « DL app » idea) → ABOVE the marker of the workspace `TODOLIST.MD`, in
  « [SubStudio] backlog », verbatim. The old file's header (« Claude appends findings at the very end… ») was not
  moved: above the marker it would read as Cyril's instruction and contradict workspace §6. It stays in `_archive/`. Claude's 16 dated findings → below the marker under `### [SubStudio]`, each
  with a plain first sentence and the original text verbatim after « Detail: ».
- **`TODOCYRIL.md`** (2 items, no `>>>` answer): « Rotate the OpenAI API key » rewritten as « Delete the old OpenAI
  API key SubStudio no longer uses » in the workspace `TODOCYRIL.MD` (SubStudio moved to Gemini; `.env` holds no
  OpenAI key and no code calls OpenAI, checked by key name and grep). « Decide: should `.env` stay out of git? »
  dropped: it asked nothing unless Cyril objected, and `.env` has been untracked with `.env.example` committed since
  2026-08-21.
- **Credentials:** none moved. `.env` stays (compose reads it).
- **The three old log files** were deleted from the project after a byte-exact copy to `_archive/` (now gitignored).
- **No `.claude/settings*.json`, skills or `AGENTS.md`** existed in the project.
