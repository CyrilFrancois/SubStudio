# conftest.py — Shared pytest fixtures: the seeded edge-case media, built once per session in a temp folder; tests run offline. Exports: media.
import os
import tempfile

import pytest

from bench.make_fixtures import build_all

# Tests never ask subtitle sites (quota, flakiness) and never write in the real work folder.
os.environ["SUBSTUDIO_DOWNLOADS"] = "0"
os.environ["SUBSTUDIO_WORK_DIR"] = tempfile.mkdtemp(prefix="substudio-work-")
# …nor in the real state folder: the job store (jobs.sqlite3) and settings.json of a test live in a temp dir.
os.environ.setdefault("SUBSTUDIO_STATE_DIR", tempfile.mkdtemp(prefix="substudio-state-"))


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    # No skip when ffmpeg is missing: a skipped edge case reads as green, and these are the ones that bit us.
    return build_all(tmp_path_factory.mktemp("media"))
