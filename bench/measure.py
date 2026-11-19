# measure.py — Runs one bench command and records its wall time and peak RAM/CPU against the half-the-PC budget. Exports: measure, main.
"""Samples the command's whole process tree, plus any already-running process named with --watch (a llama.cpp
server, say, which does the work while the command only sends it requests).

CPU is reported in threads (100 % of one logical CPU = 1.0), so it reads directly against D-0001's budget of 4.

Usage:
  python -m bench.measure --out run.json [--watch llama-server.exe] [--interval 0.5] -- <command> [args...]
The command's own exit code is returned, and the JSON is written even when it fails.
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

BUDGET_THREADS = 4.0  # D-0001: at most half of the 8 logical CPUs
BUDGET_RAM_GB = 7.0   # D-0001: at most 7 GB for the whole stack
_GB = 1024 ** 3


def norm_name(name: str) -> str:
    # "llama-server" and "llama-server.exe" name the same process; a mismatch would silently measure nothing.
    name = name.lower()
    return name[:-4] if name.endswith(".exe") else name


def _tree(root: psutil.Process, watch: set[str]) -> dict[int, psutil.Process]:
    """The command's process tree plus watched processes, keyed by PID so a process found both ways counts once."""
    procs = {root.pid: root}
    try:
        procs.update({p.pid: p for p in root.children(recursive=True)})
    except psutil.NoSuchProcess:
        pass
    if watch:
        procs.update({p.pid: p for p in psutil.process_iter(["name"]) if norm_name(p.info["name"] or "") in watch})
    return procs


def measure(command: list[str], watch: list[str] | None = None, interval: float = 0.5) -> dict:
    watch_names = {norm_name(w) for w in watch or []}
    seen: dict[int, psutil.Process] = {}  # cpu_percent needs the same object across calls to measure an interval
    own_peaks: dict[int, int] = {}
    names_read: set[str] = set()
    unreadable: set[str] = set()
    peak_sampled = 0
    cpu_samples: list[float] = []
    # Windows CreateProcess does not resolve a relative "dir/prog" path or a missing .exe suffix: resolve it here.
    program = shutil.which(command[0])
    if program is None:
        raise FileNotFoundError(f"cannot find the program to measure: {command[0]}")
    start = time.monotonic()
    child = subprocess.Popen([program, *command[1:]])
    root = psutil.Process(child.pid)
    while child.poll() is None:
        rss, cpu = 0, 0.0
        for pid, p in _tree(root, watch_names).items():
            proc = seen.setdefault(pid, p)
            try:
                name = norm_name(proc.name())
                mem = proc.memory_info()
                cpu += proc.cpu_percent(None)
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                unreadable.add(getattr(p, "info", {}).get("name") or f"pid {pid}")
                continue
            rss += mem.rss
            # Windows keeps each process's own peak working set, which catches a spike between two samples.
            own_peaks[pid] = max(own_peaks.get(pid, 0), getattr(mem, "peak_wset", mem.rss))
            names_read.add(name)
        peak_sampled = max(peak_sampled, rss)
        cpu_samples.append(cpu / 100.0)
        time.sleep(interval)
    wall = time.monotonic() - start
    # The first cpu_percent call of each process returns 0 by design, so the first sample is dropped from the mean.
    steady = cpu_samples[1:] or cpu_samples
    peak_threads = round(max(steady, default=0.0), 2)
    # Sum of each process's own peak: exact for one process, an upper bound for several (their peaks may not
    # coincide). The budget is judged on it, so a spike between samples cannot pass as within budget.
    peak_gb = round(max(peak_sampled, sum(own_peaks.values())) / _GB, 3)
    watch_missing = sorted(watch_names - names_read)
    return {
        "command": command,
        "exit_code": child.returncode,
        "minutes": round(wall / 60, 3),
        "peak_rss_gb": peak_gb,
        "peak_sampled_rss_gb": round(peak_sampled / _GB, 3),
        "peak_cpu_threads": peak_threads,
        "mean_cpu_threads": round(sum(steady) / len(steady), 2) if steady else 0.0,
        "samples": len(cpu_samples),
        "processes": sorted(names_read),
        "watch_missing": watch_missing,
        "unreadable": sorted(unreadable),
        "budget": {"cpu_threads": BUDGET_THREADS, "ram_gb": BUDGET_RAM_GB},
        # A watched process never read means its cost is missing: the run cannot be called within budget.
        "within_budget": (peak_threads <= BUDGET_THREADS and peak_gb <= BUDGET_RAM_GB
                          and not watch_missing and not unreadable),
    }


def main(argv: list[str]) -> int:
    if "--" not in argv:
        sys.exit("usage: python -m bench.measure --out run.json [--watch NAME] -- <command> [args...]")
    split = argv.index("--")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--watch", action="append", default=[], help="also count running processes with this name")
    p.add_argument("--interval", type=float, default=0.5, help="seconds between samples")
    args = p.parse_args(argv[:split])
    command = argv[split + 1:]
    if not command:
        sys.exit("no command after --")
    result = measure(command, args.watch, args.interval)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "command"}))
    if result["watch_missing"] or result["unreadable"]:
        print(f"measure: not measured: missing {result['watch_missing']}, unreadable {result['unreadable']}",
              file=sys.stderr)
        return result["exit_code"] or 2
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
