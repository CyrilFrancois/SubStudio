#!/usr/bin/env bash
# run_context.sh — does a glossary help? TranslateGemma with names swapped for tokens vs Qwen3 with names in its prompt,
# on the name set (93 cues) and on the 150-cue set (chrF must not drop). Run from the worktree root.
# Every model run goes through `threads.py with heavy` (doc/04): it waits for the lease, always releases it, and
# exits 3 without running when the lease or 4 GB of free RAM never came (--min-free-mb: never swap the shared PC).
# Pass variant numbers (1-8) to run only those.
set -u
WITH="python3 -I ../../doc/_tools/threads.py with heavy --minutes 30 --wait 7200 --min-free-mb 4096 --"
PY=".venv/Scripts/python.exe -X utf8 -u"
B="$PY bench/translate/bench_translate.py --mode batch --ngl 99"
VARIANTS=(
  "--model tg-q4 --set names"
  "--model tg-q4 --set names --glossary swap --token x"
  "--model tg-q4 --set names --glossary swap --token nm"
  "--model qwen3-q4 --set names"
  "--model qwen3-q4 --set names --glossary prompt"
  "--model qwen3-q4 --set names --glossary swap --token x"
  "--model tg-q4 --glossary swap --token x"
  "--model qwen3-q4 --glossary prompt"
)
pick=("$@")
[ ${#pick[@]} -eq 0 ] && pick=(1 2 3 4 5 6 7 8)
for n in "${pick[@]}"; do
  args="${VARIANTS[$((n - 1))]}"
  echo "== $n: $args"
  $WITH $B $args
  code=$?
  [ $code -eq 3 ] && { echo "heavy lease or 4 GB free RAM never obtained: stopped before variant $n"; exit 3; }
done
echo ALL DONE
