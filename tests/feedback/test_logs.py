import json
from datetime import datetime, timedelta

from scis_help.answer.models import Answer, SourceLink
from scis_help.common import paths
from scis_help.feedback import feedback, gaps


def make_answer(question="vpn fails", status="answered", answer_id="a1") -> Answer:
    return Answer(
        answer_id=answer_id,
        status=status,
        text="Do it [1].",
        sources=[
            SourceLink(1, "https://x.test/vpn/", "VPN", "Steps", True),
            SourceLink(0, "https://x.test/wifi/", "Wi-Fi", "", False),
        ],
        clarifying_question=None,
        scrub_notice=False,
        question_scrubbed=question,
        mode="public",
        reason=None,
        usage=None,
    )


def lines(name):
    return (paths.data_dir() / name).read_text(encoding="utf-8").splitlines()


def test_unanswered_record_and_report_groups_by_normalised_text():
    for question in ("Parking permit?", "parking   permit", "Tuition due", "parking permit!"):
        gaps.record_unanswered(
            make_answer(question, "not_covered"), top_url="https://x.test/a/", top_score=0.41
        )
    record = json.loads(lines(gaps.FILE_NAME)[0])
    assert set(record) == {"date", "mode", "reason", "question", "top_url", "top_score"}
    assert gaps.report() == [("parking permit", 3), ("tuition due", 1)]


def test_report_skips_corrupt_and_old_lines():
    path = paths.data_dir() / gaps.FILE_NAME
    path.parent.mkdir(parents=True)
    old = (datetime.now().astimezone() - timedelta(days=40)).isoformat()
    path.write_text(
        "not json\n"
        + json.dumps({"date": old, "question": "old one"})
        + "\n"
        + json.dumps({"no": "date"})
        + "\n",
        encoding="utf-8",
    )
    gaps.record_unanswered(make_answer("new one", "not_covered"), top_url=None, top_score=0.0)
    assert gaps.report(days=30) == [("new one", 1)]
    assert gaps.report(days=60) == [("new one", 1), ("old one", 1)]


def test_suspended_writes_nothing():
    with gaps.suspended():
        gaps.record_unanswered(make_answer(), top_url=None, top_score=0.0)
    assert not (paths.data_dir() / gaps.FILE_NAME).exists()


def test_feedback_stores_question_only_when_not_helpful():
    feedback.record(make_answer("good one", answer_id="up1"), helpful=True)
    feedback.record(make_answer("bad one", answer_id="down1"), helpful=False)
    up, down = (json.loads(line) for line in lines(feedback.FILE_NAME))
    assert "question" not in up and up["urls"] == ["https://x.test/vpn/"]
    assert set(up) == {"date", "answer_id", "helpful", "mode", "status", "urls"}
    assert down["question"] == "bad one" and down["helpful"] is False
    summary = feedback.summary()
    assert summary["up"] == 1 and summary["down"] == 1
    assert summary["down_items"][0]["answer_id"] == "down1"
    assert summary["down_items"][0]["question"] == "bad one"


def test_summary_with_no_file():
    assert feedback.summary() == {"up": 0, "down": 0, "down_items": []}
    assert gaps.report() == []
