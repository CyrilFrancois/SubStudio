import os
import logging
import ffmpeg
import gc
import re
from faster_whisper import WhisperModel
from typing import Callable, Optional
from pathlib import Path

logger = logging.getLogger("SubStudio.Transcriber")

class VideoTranscriber:
    def __init__(self, model_size: str = "base"):
        self.model = None
        self.current_model_size = None
        self.default_model_size = model_size

    def _get_model(self, model_size: str):
        if self.model is None or self.current_model_size != model_size:
            if self.model is not None:
                logger.warning(f"Model mismatch. Clearing [{self.current_model_size}] to load [{model_size}]...")
                self.model = None
                gc.collect() 
            
            logger.info(f"LOADING WHISPER MODEL: [{model_size}] (Device: CPU, Compute: int8)")
            self.model = WhisperModel(model_size, device="cpu", compute_type="int8")
            self.current_model_size = model_size
        return self.model

    def extract_audio(self, video_path: str) -> str:
        audio_path = str(Path(video_path).with_suffix(".tmp.wav"))
        try:
            (
                ffmpeg
                .input(video_path)
                .output(audio_path, acodec='pcm_s16le', ac=1, ar='16k', vn=None, sn=None)
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True)
            )
            return audio_path
        except ffmpeg.Error as e:
            err = e.stderr.decode() if e.stderr else str(e)
            logger.error(f"❌ Audio extraction failed: {err}")
            return ""

    def format_timestamp(self, seconds: float) -> str:
        seconds = max(0, seconds)
        milliseconds = int(round(seconds * 1000))
        hours = milliseconds // 3600000
        milliseconds %= 3600000
        minutes = milliseconds // 60000
        milliseconds %= 60000
        secs = milliseconds // 1000
        ms = milliseconds % 1000
        return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"

    def transcribe(
        self, 
        video_path: str, 
        file_id: str, 
        on_progress: Callable,
        current_file: int,
        total_files: int,
        model_size: Optional[str] = None,
        context_prompt: Optional[str] = None
    ) -> str:
        audio_file = ""
        file_prefix = f"[{current_file}/{total_files} Files]"
        target_size = model_size or self.default_model_size

        try:
            on_progress(file_id, "transcribing", 5, f"{file_prefix} Step 2/5: Extracting audio...")
            audio_file = self.extract_audio(video_path)
            
            if not audio_file or not os.path.exists(audio_file):
                raise Exception("Could not prepare audio for transcription.")

            whisper = self._get_model(target_size)

            segments, info = whisper.transcribe(
                audio_file, 
                beam_size=5, 
                word_timestamps=True,
                initial_prompt=context_prompt,
                condition_on_previous_text=False
            )
            
            total_duration = info.duration
            srt_blocks = []
            all_words = []
            last_logged_pct = -1

            # 1. Collect words and handle terminal logging
            for segment in segments:
                if segment.words:
                    all_words.extend(segment.words)
                
                # Progress calculation
                current_pct = int((segment.end / total_duration) * 100)
                progress_val = 10 + int((segment.end / total_duration) * 80)
                
                on_progress(file_id, "transcribing", progress_val, 
                           f"{file_prefix} Step 2/5: AI Transcribing ({current_pct}%)")

                # Terminal log every 5%
                if current_pct >= last_logged_pct + 5:
                    logger.info(f"Transcription Progress: {current_pct}%")
                    last_logged_pct = (current_pct // 5) * 5

            # 2. Advanced Chunking Logic
            MAX_CHARS_TOTAL = 70  # Total characters before forcing a break
            MAX_CHARS_LINE = 35   # Ideal characters per line
            
            current_chunk = []
            current_chars = 0
            
            for word in all_words:
                word_text = word.word.strip()
                if not word_text: continue
                
                current_chunk.append(word)
                current_chars += len(word_text) + 1
                
                # Logic to break:
                # A) Sentence ends (. ! ?)
                # B) Chunk is getting too long (MAX_CHARS_TOTAL)
                is_sentence_end = any(word_text.endswith(p) for p in ['.', '!', '?'])
                
                if is_sentence_end or current_chars > MAX_CHARS_TOTAL:
                    srt_blocks.append(self._build_srt_block(len(srt_blocks) + 1, current_chunk, MAX_CHARS_LINE))
                    current_chunk = []
                    current_chars = 0

            # Final chunk
            if current_chunk:
                srt_blocks.append(self._build_srt_block(len(srt_blocks) + 1, current_chunk, MAX_CHARS_LINE))

            on_progress(file_id, "transcribing", 95, f"{file_prefix} Step 2/5: Finalizing subtitles...")
            return "\n".join(srt_blocks)

        except Exception as e:
            logger.error(f"❌ {file_prefix} Transcription error: {str(e)}")
            raise e
        finally:
            if audio_file and os.path.exists(audio_file):
                try: os.remove(audio_file)
                except: pass

    def _build_srt_block(self, index, word_objects, max_line_len):
        """Builds a 2-line maximum SRT block with natural breaking."""
        start = self.format_timestamp(word_objects[0].start)
        end = self.format_timestamp(word_objects[-1].end)
        
        words = [w.word.strip() for w in word_objects]
        
        # Merge words into a single string first
        full_text = " ".join(words)
        
        # If text is long, find a good spot to insert a newline
        if len(full_text) > max_line_len:
            midpoint = len(full_text) // 2
            # Look for the space closest to the midpoint
            space_indices = [i for i, char in enumerate(full_text) if char == ' ']
            if space_indices:
                best_space = min(space_indices, key=lambda x: abs(x - midpoint))
                full_text = full_text[:best_space] + "\n" + full_text[best_space+1:]

        return f"{index}\n{start} --> {end}\n{full_text}\n"