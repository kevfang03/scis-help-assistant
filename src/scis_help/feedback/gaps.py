"""The unanswered-questions log (ARCHITECTURE.md D8): a docs-gap report.

``unanswered.jsonl`` in the data folder holds one line per ``not_covered`` answer (gated or
said by the model): date, mode, reason, the scrubbed question, the top retrieved URL and its
score. No session ids, IPs or user agents.
"""

import re
import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager

from scis_help.answer.models import Answer
from scis_help.common import paths
from scis_help.feedback import store

FILE_NAME = "unanswered.jsonl"

_NON_WORD = re.compile(r"[^\w\[\]]+")
_paused = threading.local()


def _path():
    return paths.data_dir() / FILE_NAME


@contextmanager
def suspended() -> Iterator[None]:
    """No unanswered lines are written in this thread meanwhile (eval runs use it so test
    questions don't show up in the docs-gap report)."""
    before = getattr(_paused, "on", False)
    _paused.on = True
    try:
        yield
    finally:
        _paused.on = before


def record_unanswered(answer: Answer, *, top_url: str | None, top_score: float) -> None:
    if getattr(_paused, "on", False):
        return
    store.append(
        _path(),
        {
            "date": store.now_iso(),
            "mode": answer.mode,
            "reason": answer.reason,
            "question": answer.question_scrubbed,
            "top_url": top_url,
            "top_score": round(float(top_score), 4),
        },
    )


def normalise(question: str) -> str:
    """Lower case, punctuation dropped, spaces collapsed: "VPN fails?" == "vpn  fails"."""
    return " ".join(_NON_WORD.sub(" ", question.lower()).split())


def report(days: int = 30) -> list[tuple[str, int]]:
    """(normalised scrubbed question, count), most frequent first, then alphabetical."""
    counts = Counter(
        normalise(str(record.get("question", ""))) for record in store.read_recent(_path(), days)
    )
    counts.pop("", None)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))
