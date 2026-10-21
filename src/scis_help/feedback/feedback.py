"""Feedback on answers ("Did this help?", ARCHITECTURE.md D8).

``feedback.jsonl`` in the data folder: date, answer id, helpful, mode, status and the used
URLs. The scrubbed question is stored only for a 👎, to find bad answers. No session ids, IPs or
user agents.
"""

from scis_help.answer.models import Answer
from scis_help.common import paths
from scis_help.feedback import store

FILE_NAME = "feedback.jsonl"


def _path():
    return paths.data_dir() / FILE_NAME


def record(answer: Answer, helpful: bool) -> None:
    entry: dict[str, object] = {
        "date": store.now_iso(),
        "answer_id": answer.answer_id,
        "helpful": bool(helpful),
        "mode": answer.mode,
        "status": answer.status,
        "urls": [source.url for source in answer.sources if source.used],
    }
    if not helpful:
        entry["question"] = answer.question_scrubbed
    store.append(_path(), entry)


def summary(days: int = 30) -> dict:
    """``{"up": n, "down": n, "down_items": [{date, answer_id, mode, status, question,
    urls}, ...]}``, newest 👎 first."""
    up = down = 0
    down_items: list[dict] = []
    for entry in store.read_recent(_path(), days):
        if entry.get("helpful") is True:
            up += 1
        elif entry.get("helpful") is False:
            down += 1
            down_items.append(
                {
                    key: entry.get(key)
                    for key in ("date", "answer_id", "mode", "status", "question", "urls")
                }
            )
    down_items.reverse()
    return {"up": up, "down": down, "down_items": down_items}
