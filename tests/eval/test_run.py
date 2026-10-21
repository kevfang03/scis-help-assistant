from pathlib import Path

import pytest

from scis_help.common.config import LlmSettings
from scis_help.eval.run import (
    AnswerRow,
    EvalQuestion,
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


def q(id, split):
    return EvalQuestion(id, "x", ("https://x.test/",), "answered", split)


def test_select_keeps_the_owner_split_out_of_all():
    questions = [q("d", "dev"), q("h", "holdout"), q("o", "owner")]
    assert [x.id for x in select(questions, "owner")] == ["o"]
    assert [x.id for x in select(questions, "dev")] == ["d"]
    assert [x.id for x in select(questions, "all")] == ["d", "h"]
    assert [x.id for x in select(questions, "all", include_owner=True)] == ["d", "h", "o"]


def test_owner_split_loads_and_unknown_split_fails(tmp_path):
    path = tmp_path / "q.yaml"
    path.write_text(
        "- id: o1\n  question: x\n  expect: not_covered\n  split: owner\n  author: owner\n",
        encoding="utf-8",
    )
    [loaded] = load_questions(path)
    assert loaded.split == "owner" and loaded.author == "owner" and not loaded.in_scope
    path.write_text(
        "- id: x\n  question: x\n  expect: not_covered\n  split: test\n", encoding="utf-8"
    )
    with pytest.raises(QuestionsError, match="dev, holdout or owner"):
        load_questions(path)


def test_committed_questions_file_shape():
    questions = load_questions(QUESTIONS)
    assert len(questions) == 40
    assert sum(q.in_scope for q in questions) == 32
    assert len(select(questions, "dev")) == 20 and len(select(questions, "holdout")) == 10
    owner = select(questions, "owner")
    assert [q.id for q in owner] == [f"o{n:02d}" for n in range(1, 11)]
    assert all(q.author == "owner" for q in owner) and sum(q.in_scope for q in owner) == 8
    assert len(select(questions, "all")) == 30
    for q in questions:
        assert all(url.startswith("https://support.cci.drexel.edu/") for url in q.expected_urls)
