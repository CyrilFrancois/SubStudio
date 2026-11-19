# export_openapi.py — Prints the UI contract's OpenAPI JSON (backend/api only) for frontend type generation. Exports: none (script).
#
# Builds an app holding only the api router, so generating types needs neither Whisper nor a GPU
# nor the legacy routes. Run from anywhere: python backend/api/export_openapi.py > openapi.json

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from fastapi import FastAPI  # noqa: E402

from api import router  # noqa: E402

app = FastAPI(title="SubStudio UI API", version="1")
app.include_router(router)
sys.stdout.write(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
