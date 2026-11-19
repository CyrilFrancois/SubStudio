#!/usr/bin/env bash
# run_all.sh — Phase A: every candidate on the 150-cue EN->FR set, one after another (one heavy model at a time).
# Run from the worktree root. Holds the `heavy` lease for the whole run and releases it on any exit.
set -u
LEASE="python3 -I ../../doc/_tools/threads.py"
$LEASE lease heavy --minutes 150 || exit 1
trap '$LEASE release heavy' EXIT
PY=".venv/Scripts/python.exe -X utf8 -u bench/translate/bench_translate.py"
$PY --model hymt-q8 --mode batch --ngl 99
$PY --model tg-q4 --mode batch --ngl 99
$PY --model tg-q4 --mode cue --ngl 99
$PY --model qwen3-q4 --mode batch --ngl 99
$PY --model tg-q8 --mode batch --ngl 99
$PY --model nllb --batch 16
echo ALL DONE
