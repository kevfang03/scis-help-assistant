"""Append-only JSON-lines files in the data folder, shared by the gaps and feedback logs.

One JSON object per line, written with a lock so web requests in threads don't interleave.
Unreadable lines are skipped when reading.
"""

import json
import logging
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def append(path: Path, record: dict[str, object]) -> None:
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(line)


def read_recent(path: Path, days: int) -> Iterator[dict]:
    """Records from the last ``days`` days whose ``date`` field parses; others are skipped."""
    if not path.exists():
        return
    since = datetime.now().astimezone() - timedelta(days=days)
    skipped = 0
    with path.open(encoding="utf-8", errors="replace") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                when = datetime.fromisoformat(record["date"])
                if when.tzinfo is None:
                    when = when.astimezone()
            except (ValueError, KeyError, TypeError):
                skipped += 1
                continue
            if when >= since:
                yield record
    if skipped:
        log.warning("jsonl lines skipped", extra={"file": path.name, "skipped": skipped})
