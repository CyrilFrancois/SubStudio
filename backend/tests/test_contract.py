# test_contract.py — The UI contract snapshot: backend/api must match frontend/src/lib/openapi.json. Exports: tests only.
#
# The frontend's api.gen.ts is generated from that snapshot (npm run gen:types), so a backend change
# that is not regenerated fails HERE, in every pytest run, not only when someone runs check:types.

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fastapi import FastAPI  # noqa: E402

from api import router  # noqa: E402

SNAPSHOT = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src", "lib", "openapi.json")


def test_backend_contract_matches_the_frontend_snapshot():
    app = FastAPI(title="SubStudio UI API", version="1")
    app.include_router(router)
    with open(SNAPSHOT, encoding="utf-8") as f:
        committed = json.load(f)
    assert app.openapi() == committed, "backend/api changed: run `npm run gen:types` in frontend/ and commit both files"
