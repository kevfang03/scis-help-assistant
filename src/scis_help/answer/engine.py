"""The answer engine (ARCHITECTURE.md D4): scrub, retrieve, gate, ask Claude, validate.

Grounding rules enforced here, in code: the model only sees retrieved chunks; source ids it
invents are dropped; an "answered" with no valid source becomes "not covered"; every link shown
comes from the index metadata of a chunk the model was given, never from the model's text. A
URL or host name may stay in the answer text only as code (plain text, never a link), and only
if it appears verbatim in a chunk the model was given.
"""

import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from scis_help.answer import prompts
from scis_help.answer.models import Answer, Conversation, Mode, ModelAnswer, SourceLink
from scis_help.common.config import Settings
from scis_help.common.scrub import scrub
from scis_help.feedback.gaps import record_unanswered
from scis_help.index.retrieve import Hit, RetrievalResult, Retriever
from scis_help.llm.client import LlmClient, LlmFailed, LlmUnavailable, LlmUsage

log = logging.getLogger(__name__)

# [S1], [S1, S3], [S1][S2]: one bracket group of source ids.
_CITATION_GROUP = re.compile(r"\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]", re.IGNORECASE)
_SOURCE_ID = re.compile(r"S?(\d+)", re.IGNORECASE)
# The model sometimes writes [2] or [1, 3] for [S2] / [S1, S3]; read those as source ids too.
_BARE_GROUP = re.compile(r"\[\s*(\d{1,2}(?:\s*[,;]\s*\d{1,2})*)\s*\](?!\()")
# Markdown links lose their target ([label](url) -> label); links come from the index only.
_MARKDOWN_LINK = re.compile(r"\[([^\]\n]+)\]\((?:https?://|www\.)[^)\s]*\)")
# URLs with a scheme or www., and bare host names on common top-level domains (neverssl.com,
# cs.drexel.edu/~user). Not after @ (emails) or inside a longer dotted word; markdown emphasis
# (**url**, *url*) is not part of the URL.
_URL_OR_HOST = re.compile(
    r"(?<![\w@.-])(?:(?:https?://|www\.)[^\s)\]>\"'`*]+"
    r"|(?:[a-z0-9-]+\.)+(?:com|edu|org|net|gov|io|us|info|me|co|ai|dev|app)\b"
    r"(?:/[^\s)\]>\"'`*]*)?)",
    re.IGNORECASE,
)
_TRAILING = ".,;:!?"
# Inline code spans and fenced blocks: `x`, ``x``, ```...```.
_CODE_SPAN = re.compile(r"(`+)(.+?)\1", re.DOTALL)


@dataclass(frozen=True, slots=True)
class _Validated:
    status: str
    text: str
    used_ids: list[int]  # 1-based source numbers in display order
    clarifying_question: str | None
    reason: str | None


def _cut(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    space = cut.rfind(" ")
    if space > 0 and not text[max_chars].isspace():
        cut = cut[:space]
    return cut.rstrip()


def _ids_in(group: str) -> list[int]:
    return [int(number) for number in _SOURCE_ID.findall(group)]


class Engine:
    def __init__(
        self,
        settings: Settings,
        *,
        retriever: Retriever | None = None,
        llm: LlmClient | None = None,
    ) -> None:
        """``retriever`` None loads the index (``IndexMissing`` if there is none); ``llm`` None
        builds an ``LlmClient`` that reads the key from keyring."""
        self._settings = settings
        self._retriever = retriever if retriever is not None else Retriever.load(settings)
        self._llm = llm if llm is not None else LlmClient(settings)

    def ask(
        self,
        message: str,
        *,
        mode: Mode = "public",
        conversation: Conversation | None = None,
    ) -> Answer:
        """Answer one message. ValueError if it is empty. Never raises for LLM problems: those
        become a ``links_only`` answer."""
        conversation = conversation if conversation is not None else Conversation()
        message = _cut(message.strip(), self._settings.answer.max_message_chars)
        if not message:
            raise ValueError("empty message")
        conversation.messages += 1

        scrubbed = scrub(message)
        question = scrubbed.text
        if conversation.pending_question:
            question = f"{conversation.pending_question} {question}"
            conversation.pending_question = None

        result = self._retriever.search(question, audience=mode)
        answer_id = uuid.uuid4().hex
        base = {
            "answer_id": answer_id,
            "scrub_notice": scrubbed.any,
            "question_scrubbed": question,
            "mode": mode,
        }

        if not result.confident or not result.hits:
            answer = Answer(
                status="not_covered",
                text=self._not_covered_text(),
                sources=self._also_retrieved(result.hits, set(), mode),
                clarifying_question=None,
                reason="low_confidence",
                usage=None,
                **base,
            )
            self._unanswered(answer, result)
            return self._done(answer, result, scrubbed.replaced, conversation)

        hits = result.hits
        user = prompts.user_turn(
            mode=mode,
            sources=[(hit.chunk.title, hit.chunk.heading, hit.chunk.text) for hit in hits],
            question=question,
            clarify_asked=conversation.clarify_asked,
        )
        llm = self._settings.llm
        max_tokens = llm.max_output_tokens_desk if mode == "desk" else llm.max_output_tokens
        try:
            outcome = self._llm.complete(
                system=prompts.SYSTEM_PROMPT,
                user=user,
                output=ModelAnswer,
                purpose="answer",
                max_tokens=max_tokens,
            )
        except LlmUnavailable as error:
            return self._done(
                self._links_only(base, hits, error.reason, None),
                result,
                scrubbed.replaced,
                conversation,
            )
        except LlmFailed as error:
            return self._done(
                self._links_only(base, hits, f"llm_failed:{error.reason}", None),
                result,
                scrubbed.replaced,
                conversation,
            )
        if outcome.status == "refused" or outcome.value is None:
            reason = "refused" if outcome.status == "refused" else f"llm_failed:{outcome.status}"
            return self._done(
                self._links_only(base, hits, reason, outcome.usage),
                result,
                scrubbed.replaced,
                conversation,
            )

        checked = self._validate(
            outcome.value, [hit.chunk.text for hit in hits], conversation, question
        )
        if checked.status == "not_covered":
            answer = Answer(
                status="not_covered",
                text=self._not_covered_text(),
                sources=self._also_retrieved(hits, set(), mode),
                clarifying_question=None,
                reason=checked.reason,
                usage=outcome.usage,
                **base,
            )
            self._unanswered(answer, result)
        elif checked.status == "clarify":
            answer = Answer(
                status="clarify",
                text=checked.text,
                sources=[],
                clarifying_question=checked.clarifying_question,
                reason=None,
                usage=outcome.usage,
                **base,
            )
        else:
            text, sources = self._number(checked, hits, mode)
            answer = Answer(
                status="answered",
                text=text,
                sources=sources,
                clarifying_question=None,
                reason=None,
                usage=outcome.usage,
                **base,
            )
        return self._done(answer, result, scrubbed.replaced, conversation)

    # -- steps --

    def _validate(
        self,
        model: ModelAnswer,
        sources: Sequence[str],
        conversation: Conversation,
        question: str,
    ) -> _Validated:
        """Drop unknown source ids and their citations; turn an unsupported answer or a second
        clarification into not covered. ``sources`` are the chunk texts the model was given."""
        valid = set(range(1, len(sources) + 1))
        if model.status == "not_covered":
            return _Validated("not_covered", "", [], None, "model")
        if model.status == "clarify":
            clarifying = (model.clarifying_question or "").strip()
            if conversation.clarify_asked or not clarifying:
                return _Validated("not_covered", "", [], None, "model")
            conversation.clarify_asked = True
            conversation.pending_question = question
            # The question alone: the model's lead-in often asks the same thing again.
            clarifying = _strip_links(_CITATION_GROUP.sub("", clarifying), sources).strip()
            return _Validated("clarify", clarifying, [], clarifying, None)

        listed = [n for source_id in model.used_sources for n in _ids_in(source_id)]

        def keep_valid(match: re.Match[str]) -> str:
            ids = [n for n in _ids_in(match[1]) if n in valid]
            return "[" + ", ".join(f"S{n}" for n in ids) + "]" if ids else ""

        text = _BARE_GROUP.sub(lambda m: "[" + re.sub(r"(\d+)", r"S\1", m[1]) + "]", model.answer)
        text = _CITATION_GROUP.sub(keep_valid, text)
        text = _strip_links(text, sources).strip()
        cited = [n for match in _CITATION_GROUP.finditer(text) for n in _ids_in(match[1])]
        used: list[int] = []
        for n in cited + listed:  # first mention in the text, then the rest of used_sources
            if n in valid and n not in used:
                used.append(n)
        if not used or not text:
            return _Validated("not_covered", "", [], None, "model")
        return _Validated("answered", text, used, None, None)

    def _number(self, checked: _Validated, hits: list[Hit], mode: Mode):
        """Number the used pages 1.. in first-mention order (two chunks of one page share a
        number), rewrite [S#] to [n] and build the links from the chunks."""
        page_number: dict[str, int] = {}
        sources: list[SourceLink] = []
        number_of: dict[int, int] = {}
        for source in checked.used_ids:
            chunk = hits[source - 1].chunk
            if chunk.url not in page_number:
                page_number[chunk.url] = len(page_number) + 1
                sources.append(
                    SourceLink(
                        n=page_number[chunk.url],
                        url=chunk.url,
                        title=chunk.title,
                        heading=chunk.heading,
                        used=True,
                    )
                )
            number_of[source] = page_number[chunk.url]

        def rewrite(match: re.Match[str]) -> str:
            numbers: list[int] = []
            for source in _ids_in(match[1]):
                if number_of[source] not in numbers:
                    numbers.append(number_of[source])
            return "".join(f"[{n}]" for n in numbers)

        text = _CITATION_GROUP.sub(rewrite, checked.text)
        return text, sources + self._also_retrieved(hits, set(page_number), mode)

    @staticmethod
    def _also_retrieved(hits: list[Hit], shown: set[str], mode: Mode) -> list[SourceLink]:
        """Desk mode only: the other retrieved pages, n = 0, used = False."""
        if mode != "desk":
            return []
        links: list[SourceLink] = []
        seen = set(shown)
        for hit in hits:
            if hit.chunk.url in seen:
                continue
            seen.add(hit.chunk.url)
            links.append(
                SourceLink(
                    n=0,
                    url=hit.chunk.url,
                    title=hit.chunk.title,
                    heading=hit.chunk.heading,
                    used=False,
                )
            )
        return links

    def _links_only(
        self, base: dict, hits: list[Hit], reason: str, usage: LlmUsage | None
    ) -> Answer:
        """The top distinct pages, numbered 1.., with no generated text."""
        links: list[SourceLink] = []
        seen: set[str] = set()
        for hit in hits:
            if hit.chunk.url in seen:
                continue
            seen.add(hit.chunk.url)
            links.append(
                SourceLink(
                    n=len(links) + 1,
                    url=hit.chunk.url,
                    title=hit.chunk.title,
                    heading=hit.chunk.heading,
                    used=False,
                )
            )
            if len(links) == self._settings.answer.links_when_unavailable:
                break
        return Answer(
            status="links_only",
            text=prompts.LINKS_ONLY_TEXT,
            sources=links,
            clarifying_question=None,
            reason=reason,
            usage=usage,
            **base,
        )

    def _not_covered_text(self) -> str:
        return prompts.NOT_COVERED_TEXT.format(contact=self._settings.answer.help_contact)

    @staticmethod
    def _unanswered(answer: Answer, result: RetrievalResult) -> None:
        top = result.hits[0] if result.hits else None
        try:
            record_unanswered(
                answer,
                top_url=top.chunk.url if top else None,
                top_score=top.dense if top else result.best_dense,
            )
        except OSError as error:
            log.warning("unanswered not saved", extra={"error": type(error).__name__})

    @staticmethod
    def _done(
        answer: Answer,
        result: RetrievalResult,
        replaced,
        conversation: Conversation,
    ) -> Answer:
        usage = answer.usage
        if usage is not None:
            conversation.tokens += (
                usage.input_tokens
                + usage.output_tokens
                + usage.cache_read_tokens
                + usage.cache_write_tokens
            )
        log.info(
            "answer",
            extra={
                "answer_id": answer.answer_id,
                "mode": answer.mode,
                "status": answer.status,
                "reason": answer.reason,
                "hits": len(result.hits),
                "confident": result.confident,
                "best_dense": round(result.best_dense, 4),
                "best_bm25": round(result.best_bm25, 3),
                "sources_used": sum(1 for source in answer.sources if source.used),
                "scrubbed": dict(replaced),
                "input_tokens": usage.input_tokens if usage else 0,
                "output_tokens": usage.output_tokens if usage else 0,
                "cache_read_tokens": usage.cache_read_tokens if usage else 0,
                "cache_write_tokens": usage.cache_write_tokens if usage else 0,
            },
        )
        return answer


def _strip_links(text: str, sources: Sequence[str]) -> str:
    """Markdown links become their label. A URL or host name that appears verbatim in one of
    ``sources`` stays as code (in backticks, so it renders as text, not a link); any other is
    removed."""
    text = _MARKDOWN_LINK.sub(r"\1", text)

    def plain(match: re.Match[str], *, in_code: bool) -> str:
        url = match[0].rstrip(_TRAILING)
        tail = match[0][len(url) :]
        if not any(url in source for source in sources):
            return tail
        return url + tail if in_code else f"`{url}`{tail}"

    def outside(part: str) -> str:
        return _URL_OR_HOST.sub(lambda m: plain(m, in_code=False), part)

    parts: list[str] = []
    end = 0
    for span in _CODE_SPAN.finditer(text):
        parts.append(outside(text[end : span.start()]))
        inner = _URL_OR_HOST.sub(lambda m: plain(m, in_code=True), span[2])
        if inner.strip():
            parts.append(span[1] + inner + span[1])
        end = span.end()
    parts.append(outside(text[end:]))
    return "".join(parts)
