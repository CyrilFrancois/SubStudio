# subtitle_processor.py — SubtitleProcessor: find and extract an embedded text subtitle track as cues. Exports: SubtitleProcessor.

import json
import os
import subprocess
import logging
from typing import List, Optional

from .jobs import JobHandle, JobAborted
from .subtitles import Cue, load_file

logger = logging.getLogger("SubStudio.Processor")

# Codecs that carry TEXT. Anything else (hdmv_pgs_subtitle, dvd_subtitle, dvb_subtitle, xsub)
# is a bitmap and cannot be converted to SRT — ffmpeg refuses, so we must not even try.
TEXT_SUBTITLE_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text", "eia_608", "subviewer"}


class SubtitleProcessor:
    def find_text_subtitle_stream(self, video_path: str) -> Optional[int]:
        """
        Returns the subtitle-relative index (for -map 0:s:N) of the first TEXT subtitle
        stream, or None. Bitmap tracks are skipped because they cannot become SRT.
        """
        try:
            cmd = [
                'ffprobe', '-v', 'error', '-print_format', 'json',
                '-show_streams', '-select_streams', 's', video_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            streams = json.loads(result.stdout or '{}').get('streams', [])
        except Exception as e:
            logger.debug(f"Subtitle probe failed for {os.path.basename(video_path)}: {e}")
            return None

        for relative_index, stream in enumerate(streams):
            codec = (stream.get('codec_name') or '').lower()
            if codec in TEXT_SUBTITLE_CODECS:
                lang = stream.get('tags', {}).get('language', 'und')
                logger.info(f"📑 Found embedded text subtitle track {relative_index} ({codec}, {lang})")
                return relative_index

        if streams:
            codecs = ", ".join((s.get('codec_name') or '?') for s in streams)
            logger.info(f"Embedded subtitles are bitmap-only ({codecs}); cannot reuse as text.")
        return None

    def extract_embedded_subs(self, video_path: str, track_index: int = 0,
                              job: Optional[JobHandle] = None) -> List[Cue]:
        """Extracts one embedded text subtitle stream as cues. Raises on any failure: a failed
        extraction must never read as a video without subtitles."""
        temp_srt = f"{os.path.splitext(video_path)[0]}.tmp_extract.srt"
        cmd = ['ffmpeg', '-y', '-v', 'error', '-i', video_path,
               '-map', f'0:s:{track_index}', '-c:s', 'srt', '-f', 'srt', temp_srt]
        try:
            logger.info(f"⚙️ Extracting embedded track {track_index}...")
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if job:
                job.track(process)
            try:
                _, stderr = process.communicate()
            finally:
                if job:
                    job.untrack()
            if job and job.is_aborted:
                raise JobAborted(job.file_id)
            if process.returncode != 0:
                raise RuntimeError(f"Embedded subtitle extraction failed: {stderr[:300]}")
            return load_file(temp_srt)
        finally:
            if os.path.exists(temp_srt):
                try:
                    os.remove(temp_srt)
                except OSError:
                    pass
