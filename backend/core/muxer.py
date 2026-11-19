# muxer.py — VideoMuxer: injects translated cue lists into MKV as SRT tracks with ISO 639-2 tags. Exports: VideoMuxer.

import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from .jobs import JobHandle, JobAborted
from .subtitles import Cue, drop_after, to_srt

logger = logging.getLogger("SubStudio.Muxer")

class VideoMuxer:
    def __init__(self):
        # ISO 639-2 map for MKV metadata compatibility
        self.lang_map = {
            "fr": "fra", "en": "eng", "es": "spa", "de": "deu",
            "it": "ita", "pt": "por", "nl": "nld", "ja": "jpn",
            "ko": "kor", "zh": "zho", "ru": "rus"
        }

    def get_video_duration(self, video_path: Path) -> float:
        """Returns the duration of the video in seconds using ffprobe."""
        try:
            cmd = [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return float(result.stdout.strip())
        except Exception as e:
            logger.warning(f"Could not determine video duration: {e}. Defaulting to large value.")
            return 999999.0

    def get_existing_sub_count(self, video_path: Path) -> int:
        """Counts existing subtitle streams in the source file for correct metadata indexing."""
        try:
            cmd = [
                "ffprobe", "-v", "error", "-select_streams", "s",
                "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            output = result.stdout.strip()
            return len(output.split('\n')) if output else 0
        except:
            return 0

    def mux(
        self,
        video_path: str,
        srts: Dict[str, List[Cue]],
        current_file: int,
        total_files: int,
        strip_existing: bool = False,
        cleanup_original: bool = False,
        job: Optional[JobHandle] = None
    ) -> str:
        video_input_path = Path(video_path)
        output_path = video_input_path.parent / f"{video_input_path.stem}_SubStudio.mkv"
        prefix = f"[{current_file}/{total_files} Files]"
        
        # Determine duration to clip trailing subs and count existing tracks for metadata
        video_duration = self.get_video_duration(video_input_path)
        existing_sub_count = 0 if strip_existing else self.get_existing_sub_count(video_input_path)

        tmp_srt_paths: List[tuple] = []

        # ------------------------------------------------
        # 1. Process and Write temporary SRT files
        # ------------------------------------------------
        for lang_code, cues in srts.items():
            # Cues starting after the video ends would hold a black frame in the player.
            formatted_content = to_srt(drop_after(cues, video_duration))
            
            tmp_path = video_input_path.parent / f"{video_input_path.stem}.{lang_code}.tmp.srt"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(formatted_content)
            tmp_srt_paths.append((lang_code, tmp_path))

        try:
            logger.info(f"{prefix} Muxing: {video_input_path.name}")

            # ------------------------------------------------
            # 2. Build FFmpeg command
            # ------------------------------------------------
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
            cmd += ["-i", str(video_input_path)]
            for _, srt_path in tmp_srt_paths:
                cmd += ["-i", str(srt_path)]

            # Mapping Logic. The '?' makes each map optional: a video with no audio track
            # (or no subtitles) would otherwise abort with "matches no streams".
            if strip_existing:
                cmd += ["-map", "0:v?", "-map", "0:a?"]
            else:
                cmd += ["-map", "0:v?", "-map", "0:a?", "-map", "0:s?", "-map", "0:t?"]

            for i in range(len(tmp_srt_paths)):
                cmd += ["-map", f"{i + 1}:0"]

            # Existing subtitle streams are COPIED, never re-encoded: image-based tracks
            # (PGS/VobSub, i.e. most BluRay remuxes) cannot be converted to text and ffmpeg
            # aborts the whole mux. Only the SRTs we just generated are encoded as srt.
            cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", "copy"]
            for i in range(len(tmp_srt_paths)):
                out_stream_idx = existing_sub_count + i
                cmd += [f"-c:s:{out_stream_idx}", "srt"]

            # Metadata Assignment
            for index, (lang_code, _) in enumerate(tmp_srt_paths):
                # Calculate the correct output stream index for subtitles
                out_stream_idx = existing_sub_count + index
                iso_lang = self.lang_map.get(lang_code.lower(), lang_code.lower())
                cmd += [f"-metadata:s:s:{out_stream_idx}", f"language={iso_lang}"]
                cmd += [f"-metadata:s:s:{out_stream_idx}", f"title=AI {lang_code.upper()}"]

            cmd += [str(output_path)]

            # Popen (not run) so an abort can kill FFmpeg mid-encode instead of waiting it out.
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
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
                raise RuntimeError(stderr)

            # Cleanup Temporary SRTs
            for _, p in tmp_srt_paths:
                if p.exists(): p.unlink()

            # Cleanup Original if requested
            if cleanup_original:
                if output_path.exists() and video_input_path.resolve() != output_path.resolve():
                    logger.info(f"{prefix} Cleanup: Removing source file.")
                    video_input_path.unlink()

            return str(output_path)

        except JobAborted:
            for _, p in tmp_srt_paths:
                if p.exists(): p.unlink()
            if output_path.exists():
                output_path.unlink()
            logger.warning(f"🛑 {prefix} Muxing aborted by user; partial output removed.")
            raise
        except Exception as e:
            # Emergency Cleanup
            for _, p in tmp_srt_paths:
                if p.exists(): p.unlink()
            if output_path.exists():
                output_path.unlink()
            logger.error(f"❌ {prefix} Muxing Error: {str(e)}")
            raise Exception(f"Muxing failed: {str(e)}")