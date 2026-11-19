# names.py — proper nouns counted from the source cues: the names a translator must not translate. Exports: proper_nouns.
"""
Computed, not asked of a model: a word counts as a name when it is capitalised in the MIDDLE of a sentence (a
capital at a sentence start says nothing), recurs, and never appears in lower case anywhere in the cues (so « Hey »
or « Look » never qualify). ALL-CAPS words are skipped: in SDH subtitles they are speaker labels and shouting.
"""

import re
from collections import Counter
from typing import Iterable, List

from ..subtitles import Cue

_WORD = re.compile(r"[^\W\d_][\w'’-]*")
_SENTENCE_END = ".!?…:\"«»—-♪[("
_NEVER = {"I", "I'm", "I'll", "I've", "I'd", "OK", "Mr", "Mrs", "Ms", "Dr"}


def proper_nouns(cues: Iterable[Cue], min_count: int = 2, limit: int = 60) -> List[str]:
    """Names in the cues, most frequent first."""
    texts = [c.text for c in cues]
    lower_seen = {w.casefold() for t in texts for w in _WORD.findall(t) if w[0].islower()}
    counts: Counter = Counter()
    previous = ""  # last character of the cue before: a sentence often runs across lines and cues
    for text in texts:
        flat = " ".join(text.split())
        for m in _WORD.finditer(flat):
            word = m.group().rstrip("'’-")
            before = flat[:m.start()].rstrip() or previous
            sentence_start = not before or before[-1] in _SENTENCE_END
            if sentence_start or not word[0].isupper() or word.isupper() or word in _NEVER:
                continue
            base = re.sub(r"['’]s$", "", word)  # « Sam's » is « Sam »
            if base.casefold() in lower_seen:
                continue
            counts[base] += 1
        previous = flat[-1:] or previous
    return [w for w, n in counts.most_common() if n >= min_count][:limit]
