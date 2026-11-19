# pgs.py — Minimal PGS (.sup) bitmap-subtitle writer, so the bench can seed a PGS-only video with ffmpeg. Exports: write_sup.
"""ffmpeg cannot turn text into bitmap subtitles, so a PGS track has to be written by hand.

Format: each segment is b"PG", PTS and DTS (90 kHz, 4 bytes each), a type byte and a 2-byte size.
A display set is PCS, WDS, PDS, ODS, END; an empty PCS (no objects) clears the screen.
"""
import struct
from pathlib import Path

_PCS, _WDS, _PDS, _ODS, _END = 0x16, 0x17, 0x14, 0x15, 0x80


def _segment(seg_type: int, pts_ms: int, payload: bytes) -> bytes:
    pts = pts_ms * 90
    return b"PG" + struct.pack(">IIBH", pts, 0, seg_type, len(payload)) + payload


def _rle_solid(width: int, height: int, color: int) -> bytes:
    # One run of `color` per line, then end-of-line. Long form (00 11LLLLLL LLLLLLLL CC) works for 64..16383.
    if not 64 <= width <= 16383 or color == 0:
        raise ValueError("width must be 64..16383 and color non-zero")
    line = bytes([0x00, 0xC0 | (width >> 8), width & 0xFF, color, 0x00, 0x00])
    return line * height


def _display_set(start_ms: int, video_w: int, video_h: int, comp_no: int, box: tuple[int, int, int, int]) -> bytes:
    x, y, w, h = box
    pcs = struct.pack(">HHBHBBBB", video_w, video_h, 0x10, comp_no, 0x80, 0, 0, 1)
    pcs += struct.pack(">HBBHH", 0, 0, 0, x, y)
    wds = struct.pack(">BBHHHH", 1, 0, x, y, w, h)
    pds = struct.pack(">BB", 0, 0) + bytes([1, 235, 128, 128, 255])  # entry 1: opaque white (Y, Cr, Cb, A)
    rle = _rle_solid(w, h, 1)
    ods = struct.pack(">HBB", 0, 0, 0xC0) + (len(rle) + 4).to_bytes(3, "big") + struct.pack(">HH", w, h) + rle
    return b"".join(_segment(t, start_ms, p) for t, p in
                    [(_PCS, pcs), (_WDS, wds), (_PDS, pds), (_ODS, ods), (_END, b"")])


def _clear_set(end_ms: int, video_w: int, video_h: int, comp_no: int, box: tuple[int, int, int, int]) -> bytes:
    x, y, w, h = box
    pcs = struct.pack(">HHBHBBBB", video_w, video_h, 0x10, comp_no, 0x00, 0, 0, 0)
    wds = struct.pack(">BBHHHH", 1, 0, x, y, w, h)
    return b"".join(_segment(t, end_ms, p) for t, p in [(_PCS, pcs), (_WDS, wds), (_END, b"")])


def write_sup(path: Path, video_w: int, video_h: int, cues_ms: list[tuple[int, int]]) -> None:
    """Write one white box per (start_ms, end_ms) cue, near the bottom of the frame."""
    box = (video_w // 4, video_h - 60, video_w // 2, 40)
    out = b""
    for i, (start, end) in enumerate(cues_ms):
        out += _display_set(start, video_w, video_h, 2 * i, box)
        out += _clear_set(end, video_w, video_h, 2 * i + 1, box)
    path.write_bytes(out)
