# run.py — From one video's cells to the verified in-place swap: which tracks to write, which languages the marker lists, each track's origin. Exports: write_run, current_path, verdict_record.
"""The one seam between the pipeline (core/cells.VideoRun) and core/output.update_in_place (D-0002 §4):

- tracks written  = every PASSED cell that is not « kept » (a kept subtitle is already in the file, unchanged);
- marker          = every PASSED language, kept ones included, in the order of the cells;
- origin per track = Cell.origin.kind() (downloaded | translated | transcribed), written as the ORIGIN_TAG;
- reviewed tag     = the cells a person saved in the Review screen (Cell.reviewed), so a rebuild keeps the mark.

A video whose passed cells are all « kept » is left exactly as it was: no write, no rename. After a swap the video
has a new name; it is recorded on the run (VideoRun.output_path) so a retry rewrites the renamed file, never
looks for the old one.
"""

from pathlib import Path
from typing import Dict, Optional

from ..cells import CellState, VideoRun
from ..jobs import JobHandle
from ..provenance import REJECTED, track_key
from .inplace import OutputResult, update_in_place


def current_path(run: VideoRun) -> Path:
    """Where this run's video is now: its new name after a swap, else the path it was queued with."""
    return Path(run.output_path or run.path)


def write_run(run: VideoRun, job: Optional[JobHandle] = None) -> Optional[OutputResult]:
    """Writes the run's passed cells into its video. None when no cell passed (nothing to claim in a marker)."""
    passed = [c for c in run.cells.values() if c.state == CellState.PASSED]
    if not passed:
        return None
    new = [c for c in passed if c.origin is None or c.origin.kind() != "kept"]
    if any(c.origin is None for c in new):
        # A passed cell always carries its origin (cells.produce_cell); without one the tag would be a guess.
        raise ValueError(f"passed cells without an origin: {[c.language for c in new if c.origin is None]}")
    result = update_in_place(
        current_path(run),
        {c.language: c.cues for c in new},
        [c.language for c in passed],
        origins={c.language: c.origin.kind() for c in new},
        reviewed={c.language for c in new if c.reviewed},
        job=job,
    )
    if result.status == "updated":
        run.output_path = str(result.path)
    return result


def verdict_record(run: VideoRun, subs: Dict[int, int]) -> Dict[str, str]:
    """The quality gate's verdicts on the pre-existing subtitles, keyed for the video as it is after the output
    step (api/job_store.record_verdicts): {track_key: "kept"} when accepted, {track_key: REJECTED} when refused.
    `subs` is OutputResult.subs (an untouched video: output.subtitle_streams of it). An embedded track the update
    replaced has no entry there and is left out: it is no longer in the file."""
    record: Dict[str, str] = {}
    for (kind, ref), passed in run.judged.items():
        if kind == "embedded":
            index = subs.get(int(ref))
            if index is None:
                continue
            key = track_key(kind="embedded", stream_index=index)
        else:
            key = track_key(kind="sidecar", path=ref)
        record[key] = "kept" if passed else REJECTED
    return record
