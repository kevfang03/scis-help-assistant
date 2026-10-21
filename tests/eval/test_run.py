from pathlib import Path

import pytest

from scis_help.common.config import LlmSettings
from scis_help.eval.run import (
    AnswerRow,
    QuestionsError,
    RetrievalRow,
    answer_metrics,
    estimate_cost,
    load_questions,
    page_order,
    retrieval_metrics,
    select,
)

QUESTIONS = Path(__file__).parents[2] / "evals" / "questions.yaml"


def rrow(id, expect, rank, confident):
    return RetrievalRow(id, expect, rank, confident, 0.6, 3.0, [])


def arow(id, expect, status, hit, verdict=None):
    return AnswerRow(id, expect, status, None, [], hit, verdict, 100, 10, 0, 0, 0.001)


def test_retrieval_metrics():
    rows = [
        rrow("a", "answered", 1, True),
        rrow("b", "answered", 3, True),
        rrow("c", "answered", 5, False),
        rrow("d", "answered", None, True),
        rrow("e", "not_covered", None, False),
        rrow("f", "not_covered", None, True),
    ]
    m = retrieval_metrics(rows)
    assert m["hit_at_3"] == 0.5 and m["hit_at_5"] == 0.75
    assert m["mrr"] == pytest.approx((1 + 1 / 3 + 1 / 5) / 4, abs=1e-4)
    assert m["in_scope_confident"] == 0.75 and m["out_of_scope_rejected"] == 0.5


def test_answer_metrics():
    rows = [
        arow("a", "answered", "answered", True, "supported"),
        arow("b", "answered", "answered", False, "partial"),
        arow("c", "answered", "clarify", False),
        arow("d", "answered", "links_only", False),
        arow("e", "not_covered", "not_covered", None),
        arow("f", "not_covered", "answered", None, "supported"),
    ]
    m = answer_metrics(rows)
    assert m["answered_rate"] == 0.5
    assert m["not_covered_accuracy"] == 0.5
    assert m["citation_hit"] == 0.25 and m["citation_hit_of_answered"] == 0.5
    assert m["groundedness"] == pytest.approx(2 / 3, abs=1e-4)
    assert m["links_only"] == 1 and m["input_tokens"] == 600


def test_page_order_and_cost_estimate():
    assert page_order(["a", "b", "a", "c"]) == ["a", "b", "c"]
    assert 0 < estimate_cost([], judge=True, prices=LlmSettings()) + 1e-9


def test_bad_questions_files(tmp_path):
    bad = tmp_path / "q.yaml"
    bad.write_text("- id: q1\n  question: x\n  expect: maybe\n  split: dev\n", encoding="utf-8")
    with pytest.raises(QuestionsError, match="expect must be"):
        load_questions(bad)
    with pytest.raises(QuestionsError, match="cannot read"):
        load_questions(tmp_path / "missing.yaml")


def test_committed_questions_file_shape():
    questions = load_questions(QUESTIONS)
    assert len(questions) == 30
    assert sum(q.in_scope for q in questions) == 24
    assert len(select(questions, "dev")) == 20 and len(select(questions, "holdout")) == 10
    for q in questions:
        assert all(url.startswith("https://support.cci.drexel.edu/") for url in q.expected_urls)
