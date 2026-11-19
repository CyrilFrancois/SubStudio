# conftest.py — Shared pytest fixtures: the seeded edge-case media, built once per session in a temp folder. Exports: media.
import pytest

from bench.make_fixtures import build_all


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    # No skip when ffmpeg is missing: a skipped edge case reads as green, and these are the ones that bit us.
    return build_all(tmp_path_factory.mktemp("media"))
