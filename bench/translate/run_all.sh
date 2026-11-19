#!/usr/bin/env bash
# run_all.sh — Phase A: every candidate on the 150-cue EN->FR set, one after another (one heavy model at a time).
# Run from the worktree root. Each run goes through `threads.py with heavy` (doc/04): it waits for the lease, always
# releases it, and exits 3 without running when the lease never came; then this script stops.
set -u
WITH="python3 -I ../../doc/_tools/threads.py with heavy --minutes 60 --wait 7200 --min-free-mb 4096 --"
PY=".venv/Scripts/python.exe -X utf8 -u bench/translate/bench_translate.py"
for args in "--model hymt-q8 --mode batch --ngl 99" "--model tg-q4 --mode batch --ngl 99" \
            "--model tg-q4 --mode cue --ngl 99" "--model qwen3-q4 --mode batch --ngl 99" \
            "--model tg-q8 --mode batch --ngl 99" "--model nllb --batch 16"; do
  $WITH $PY $args
  [ $? -eq 3 ] && { echo "heavy lease never obtained: stopped before $args"; exit 3; }
done
echo ALL DONE
