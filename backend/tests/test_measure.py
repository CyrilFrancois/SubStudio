# test_measure.py — Checks the bench meter on a real child process: wall time, peak RAM, CPU in threads, exit code, budget flag. Exports: tests only.
import sys

import pytest

from bench.measure import measure

# Holds ~300 MB and keeps one core busy for about 1.5 s.
_WORKLOAD = ("import time\n"
             "block = bytearray(300 * 1024 * 1024)\n"
             "for i in range(0, len(block), 4096): block[i] = 1\n"
             "end = time.time() + 1.5\n"
             "while time.time() < end: pass\n")


def test_measures_a_known_workload():
    r = measure([sys.executable, "-c", _WORKLOAD], interval=0.2)
    assert r["exit_code"] == 0
    assert 0.02 <= r["minutes"] < 0.5
    assert 0.28 <= r["peak_rss_gb"] < 1.0
    assert 0.5 <= r["peak_cpu_threads"] <= 1.5  # one busy thread, sampling noise aside
    assert r["within_budget"] is True


def test_relative_program_path_is_resolved():
    # The first real use failed on Windows: CreateProcess could not run ".venv/Scripts/python.exe".
    import os
    from pathlib import Path
    rel = os.path.relpath(sys.executable, Path.cwd()).replace("\\", "/")
    assert measure([rel, "-c", "pass"], interval=0.1)["exit_code"] == 0


def test_missing_program_is_a_clear_error():
    with pytest.raises(FileNotFoundError, match="cannot find the program"):
        measure(["no-such-program-xyz", "--help"])


def test_a_watched_process_never_found_fails_the_budget():
    r = measure([sys.executable, "-c", "pass"], watch=["no-such-server.exe"], interval=0.1)
    assert r["watch_missing"] == ["no-such-server"]
    assert r["within_budget"] is False


def test_a_process_in_the_tree_and_watched_counts_once(monkeypatch):
    # A server the command starts itself, also passed with --watch, must not double its memory.
    import psutil
    from bench import measure as m
    me = psutil.Process()
    again = psutil.Process(me.pid)  # a different object for the same process, as process_iter returns
    again.info = {"name": me.name()}
    monkeypatch.setattr(m.psutil, "process_iter", lambda attrs: [again])
    procs = m._tree(me, {m.norm_name(me.name())})
    assert [p.pid for p in procs.values()].count(me.pid) == 1


def test_watch_name_matches_with_or_without_exe():
    from bench.measure import norm_name
    assert norm_name("llama-server") == norm_name("llama-server.exe") == norm_name("LLAMA-SERVER.EXE")


def test_processes_that_never_coexist_are_not_summed():
    # Three children one after the other, each holding ~200 MB: the peak is one of them, not 600 MB.
    child = ("import subprocess, sys\n"
             "for _ in range(3):\n"
             "    subprocess.run([sys.executable, '-c', "
             "'import time; b = bytearray(200 * 1024 * 1024); b[::4096] = b\"x\" * len(b[::4096]); time.sleep(0.6)'])\n")
    r = measure([sys.executable, "-c", child], interval=0.1)
    assert r["exit_code"] == 0
    assert 0.19 <= r["peak_rss_gb"] < 0.45


def test_failed_command_keeps_its_exit_code():
    r = measure([sys.executable, "-c", "import sys; sys.exit(3)"], interval=0.1)
    assert r["exit_code"] == 3
