"""Data shapes of the answer engine (ARCHITECTURE.md D4). Payload 03 codes against these."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from scis_help.llm.client import LlmUsage

Mode = Literal["public", "desk"]


class ModelAnswer(BaseModel):
    """The structured output schema sent to Claude."""

    status: Literal["answered", "clarify", "not_covered"]
    answer: str  # short markdown; cites sources as [S1], [S2]
    used_sources: list[str]  # e.g. ["S1", "S3"]
    clarifying_question: str | None = None


@dataclass(frozen=True, slots=True)
class SourceLink:
    n: int  # display number [1], [2]...; 0 for "also retrieved" (desk only)
    url: str  # always from the index metadata, never from model output
    title: str
    heading: str
    used: bool


@dataclass(frozen=True, slots=True)
class Answer:
    answer_id: str  # uuid4 hex
    status: Literal["answered", "clarify", "not_covered", "links_only"]
    text: str  # markdown; [S#] rewritten to [n]; contact appended for not_covered
    sources: list[SourceLink]  # used first (n = 1..), then desk-only "also retrieved"
    clarifying_question: str | None
    scrub_notice: bool  # True -> the UI shows "please don't share passwords or IDs"
    question_scrubbed: str
    mode: Mode
    # links_only / not_covered cause: "low_confidence", "model", "budget", "no_key",
    # "disabled", "llm_failed:<code>", "refused"
    reason: str | None
    usage: LlmUsage | None


@dataclass
class Conversation:
    """One per CLI run or web session; ``Engine.ask`` mutates it."""

    pending_question: str | None = None  # the original question while a clarification is open
    clarify_asked: bool = False
    messages: int = 0
    tokens: int = 0
