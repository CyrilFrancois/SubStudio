# make_names_set.py — builds opensubs_en_fr_names.jsonl: the EN->FR cues each holding a name the human translator kept. Exports: main.
"""  .venv/Scripts/python.exe -X utf8 bench/translate/make_names_set.py

The 150-cue set holds only 13 names, too few to measure a glossary. This reads the same dataset
(loicmagne/open-subtitles-bitext-mining, en-fr, via the Hugging Face datasets-server: 1,000 rows in all) and keeps
every row where names_score.kept_names finds a name (93 on 2026-11-19), the 13 of the first set included.
"""
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from names_score import kept_names  # noqa: E402

URL = ("https://datasets-server.huggingface.co/rows?dataset=loicmagne/open-subtitles-bitext-mining"
       "&config=en-fr&split=train&offset={offset}&length=100")
OUT = Path(__file__).resolve().parent / "opensubs_en_fr_names.jsonl"


def main(want: int = 1000):
    rows, offset = [], 0
    with httpx.Client(timeout=60) as http:
        while len(rows) < want and offset < 20000:
            for attempt in range(5):
                r = http.get(URL.format(offset=offset))
                if r.status_code == 200:
                    break
                time.sleep(3 * (attempt + 1))
            r.raise_for_status()
            batch = r.json()["rows"]
            if not batch:
                break
            for item in batch:
                row = item["row"]
                en, fr = row["sentence1"], row["sentence2"]
                if kept_names(en, fr):
                    rows.append({"id": len(rows) + 1, "row": item["row_idx"], "en": en, "fr": fr})
                    if len(rows) == want:
                        break
            offset += len(batch)
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"{len(rows)} cues with a kept name, from rows 0..{offset}")


if __name__ == "__main__":
    main()
