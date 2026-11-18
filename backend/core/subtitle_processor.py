# subtitle_processor.py — SubtitleProcessor: SRT timestamp offsetting and embedded-track extraction. Exports: SubtitleProcessor.

import json
import os
import subprocess
import logging
import re
from datetime import timedelta
from typing import Optional

from .jobs import JobHandle, JobAborted

logger = logging.getLogger("SubStudio.Processor")

# Codecs that carry TEXT. Anything else (hdmv_pgs_subtitle, dvd_subtitle, dvb_subtitle, xsub)
# is a bitmap and cannot be converted to SRT — ffmpeg refuses, so we must not even try.
TEXT_SUBTITLE_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text", "eia_608", "subviewer"}


class SubtitleProcessor:
    def __init__(self):
        self.timestamp_regex = re.compile(r'(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})')

    def apply_offset(self, srt_content: str, offset_seconds: float) -> str:
        """Shifts every subtitle block. Positive delays, negative advances; clamped at zero."""
        if offset_seconds == 0:
            return srt_content

        logger.info(f"⏱️ Adjusting timing by {offset_seconds}s")

        def shift_timestamp(match):
            start_val = self._add_offset(match.group(1), offset_seconds)
            end_val = self._add_offset(match.group(2), offset_seconds)
            return f"{start_val} --> {end_val}"

        return self.timestamp_regex.sub(shift_timestamp, srt_content)

    def _add_offset(self, timestamp_str: str, offset_sec: float) -> str:
        try:
            h, m, s_ms = timestamp_str.split(':')
            s, ms = s_ms.split(',')

            td = timedelta(hours=int(h), minutes=int(m), seconds=int(s), milliseconds=int(ms))
            new_td = td + timedelta(seconds=offset_sec)

            if new_td.total_seconds() < 0:
                new_td = timedelta(0)

            total_seconds = int(new_td.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            milliseconds = int(new_td.microseconds / 1000)

            return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"
        except Exception as e:
            logger.error(f"Timestamp shift error: {e}")
            return timestamp_str

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
                              job: Optional[JobHandle] = None) -> str:
        """Extracts one embedded subtitle stream to a string via ffmpeg."""
        temp_srt = f"{os.path.splitext(video_path)[0]}.tmp_extract.srt"

        cmd = ['ffmpeg', '-y', '-v', 'error', '-i', video_path,
               '-map', f'0:s:{track_index}', '-c:s', 'srt', '-f', 'srt', temp_srt]

        process = None
        try:
            logger.info(f"⚙️ Extracting embedded track {track_index}...")
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if job:
                job.track(process)
            _, stderr = process.communicate()

            if job and job.is_aborted:
                raise JobAborted(job.file_id)

            if process.returncode != 0:
                logger.error(f"Embedded extraction failed: {stderr[:300]}")
                return ""

            if os.path.exists(temp_srt):
                with open(temp_srt, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
        except JobAborted:
            raise
        except Exception as e:
            logger.error(f"Extraction failed: {e}")
        finally:
            if job:
                job.untrack()
            if os.path.exists(temp_srt):
                try:
                    os.remove(temp_srt)
                except OSError:
                    pass

        return ""
