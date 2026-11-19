# align.py — re-time a subtitle to a video's speech with ffsubsync, in a killable child process. Exports: align, AlignResult, AlignError.
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("SubStudio.Acquire.Align")

# The child calls ffsubsync's own `run()` and prints its result dict as JSON: structured numbers instead of
# scraping log lines, and a separate process so a stop request can kill it (a thread cannot be killed).
_CHILD = (
    "import json, sys\n"
    "from ffsubsync.ffsubsync import make_parser, run\n"
    "r = run(make_parser().parse_args(sys.argv[1:]))\n"
    "print('SUBSTUDIO_RESULT ' + json.dumps({k: r.get(k) for k in "
    "('retval', 'offset_seconds', 'framerate_scale_factor', 'sync_was_successful')}))\n"
)
_MARK = "SUBSTUDIO_RESULT "


class AlignError(RuntimeError):
    """ffsubsync could not run (missing ffmpeg, unreadable file, crash)."""


@dataclass(frozen=True)
class AlignResult:
    output_path: str
    synced: bool                    # False: alignment looked untrustworthy, output is the input unchanged
    offset_seconds: Optional[float]  # seconds added to every cue (s, not ms)


def align(reference: str, subtitle_path: str, output_path: str, timeout_s: int = 900) -> AlignResult:
    """Shift `subtitle_path` onto `reference` (a video, or another subtitle) and write `output_path`.

    A low-quality alignment leaves the cues untouched rather than applying a probably-wrong shift
    (`--skip-sync-on-low-quality`); the caller sees `synced=False` and decides.
    """
    for p in (reference, subtitle_path):
        if not os.path.isfile(p) or os.path.getsize(p) == 0:
            raise AlignError(f"missing or empty input: {os.path.basename(p)}")
    # A video reference is decoded by ffmpeg in the child; without it on PATH, ffsubsync dies on a Windows
    # « file not found » that names no file. A Git Bash shim in ~/bin is invisible to a Windows process.
    if not reference.lower().endswith((".srt", ".ass", ".ssa", ".vtt")) and not shutil.which("ffmpeg"):
        raise AlignError("ffmpeg is not on PATH: cannot read the video's audio")
    cmd = [sys.executable, "-c", _CHILD, reference, "-i", subtitle_path, "-o", output_path,
           "--skip-sync-on-low-quality", "--output-encoding", "utf-8"]
    if not reference.lower().endswith((".srt", ".ass", ".ssa", ".vtt")):
        # ffsubsync's default detector (`subs_then_webrtc`) takes the video's own subtitle track first, when it has
        # one: a download was then re-timed to that track (maybe a bad one), never to the speech (workspace-29,
        # Ghosts S05E18, 2026-11-19). The audio is the truth: always detect speech in it.
        cmd += ["--vad", "webrtc"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        raise AlignError(f"ffsubsync took longer than {timeout_s} s") from e
    except OSError as e:
        raise AlignError(f"could not start ffsubsync: {e}") from e

    line = next((l for l in proc.stdout.splitlines() if l.startswith(_MARK)), None)
    if proc.returncode != 0 or line is None:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        logger.error(f"ffsubsync failed ({proc.returncode}): {' | '.join(tail)}")
        raise AlignError(f"ffsubsync failed: {tail[-1] if tail else 'no output'}")
    try:
        r = json.loads(line[len(_MARK):])
    except ValueError as e:
        raise AlignError("ffsubsync result unreadable") from e
    if r.get("retval") != 0 or not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise AlignError(f"ffsubsync wrote no subtitle (retval {r.get('retval')})")
    result = AlignResult(output_path, bool(r.get("sync_was_successful")), r.get("offset_seconds"))
    if not result.synced:
        logger.warning(f"Alignment untrustworthy for {os.path.basename(subtitle_path)}; timings left as downloaded")
    return result
