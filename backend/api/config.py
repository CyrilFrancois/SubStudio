# config.py — Environment read once for the UI API: media root, state dir, stub flag. Exports: MEDIA_ROOT, STATE_DIR, STUB.

import os
from pathlib import Path

# The library the UI shows. In Docker the volume is mounted at /data; natively on Windows it is
# set to a folder such as C:\Videos. Resolved once so every containment check compares real paths.
MEDIA_ROOT = os.path.realpath(os.environ.get("SUBSTUDIO_MEDIA_ROOT", "/data"))

# Where the API keeps its own small files (settings.json). Never inside the media library.
STATE_DIR = Path(os.environ.get("SUBSTUDIO_STATE_DIR", Path(__file__).resolve().parent.parent / "state"))

# "1" serves stub jobs/events/subtitles so the UI can be built before the job store exists.
# Anything else, including absent, means real data only.
STUB = os.environ.get("SUBSTUDIO_API_STUB", "") == "1"
