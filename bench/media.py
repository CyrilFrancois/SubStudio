# media.py — ffmpeg/ffprobe calls for the bench: locate the binaries, run them, read streams. Exports: ffmpeg, probe.
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _user_path_now() -> str:
    # A process keeps the PATH it started with: a winget install made since then is only in the registry.
    if sys.platform != "win32":
        return ""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return os.path.expandvars(winreg.QueryValueEx(key, "Path")[0])
    except OSError:
        return ""


def _binary(name: str) -> str:
    folder = os.environ.get("FFMPEG_DIR")
    found = (shutil.which(name, path=folder) if folder else None) or shutil.which(name) \
        or shutil.which(name, path=_user_path_now())
    if not found:
        raise FileNotFoundError(f"{name} not found in FFMPEG_DIR, PATH or the user PATH; install ffmpeg first")
    return found


def ffmpeg(*args: str) -> None:
    cmd = [_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({result.returncode}): {result.stderr.strip()[:500]}")


def probe(path: Path) -> dict:
    cmd = [_binary("ffprobe"), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path.name}: {result.stderr.strip()[:300]}")
    return json.loads(result.stdout)
