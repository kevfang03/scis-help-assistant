"""The local web app (ARCHITECTURE.md D5): JSON chat and feedback endpoints, the widget files and
the demo and desk pages.

Caps run before the engine: per-IP rate limit, then the session's message and token caps.
Session ids, IPs and user agents are never logged or stored; the rate limiter and the session
store hold them in memory only. Payload 02's engine, conversation and feedback types are
imported lazily, so this module (and its tests) load without them.
"""

import ipaddress
import logging
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, StrictBool, StringConstraints, field_validator

from scis_help.common import paths
from scis_help.index import store
from scis_help.web.ratelimit import RateLimiter
from scis_help.web.render import render_answer, urls_in
from scis_help.web.sessions import SessionStore

if TYPE_CHECKING:
    from scis_help.answer.engine import Engine
    from scis_help.answer.models import Answer
    from scis_help.common.config import Settings

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
LIMIT_TEXT = (
    "This chat has reached its limit. Please start a new chat or talk to a person at {contact}."
)
LIMIT_NOTICE = "Chat limit reached. Use “New chat” to start again."
BUDGET_NOTICE = "The assistant has used up today's answer budget, so it can only show links."
PAGE_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'"
    " data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)

SessionId = Annotated[str, StringConstraints(min_length=32, max_length=36)]


class ChatRequest(BaseModel):
    session_id: SessionId
    message: str
    mode: Literal["public", "desk"] = "public"

    @field_validator("session_id")
    @classmethod
    def _uuid(cls, value: str) -> str:
        return str(uuid.UUID(value))


class FeedbackRequest(BaseModel):
    session_id: SessionId
    answer_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    helpful: StrictBool

    @field_validator("session_id")
    @classmethod
    def _uuid(cls, value: str) -> str:
        return str(uuid.UUID(value))


def _new_conversation() -> Any:
    from scis_help.answer.models import Conversation

    return Conversation()


def _record_feedback(answer: "Answer", helpful: bool) -> None:
    from scis_help.feedback import feedback

    feedback.record(answer, helpful)


def _llm_state(settings: "Settings") -> Literal["ready", "no_key", "disabled"]:
    if not settings.llm.enabled:
        return "disabled"
    from scis_help.llm.keys import get_api_key

    return "ready" if get_api_key() else "no_key"


def _index_pages() -> int:
    manifest = store.read_manifest(paths.index_dir()) or {}
    pages = manifest.get("pages", 0)
    return pages if isinstance(pages, int) else 0


def is_loopback(host: str | None) -> bool:
    try:
        address = ipaddress.ip_address(host or "")
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return (mapped or address).is_loopback


def _client(request: Request) -> str:
    return request.client.host if request.client else ""


def _error(status: int, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status, headers=headers)


def create_app(settings: "Settings", *, engine: "Engine | None" = None) -> FastAPI:
    """The app. ``engine`` is built here (loading the index once) unless a test passes one."""
    if engine is None:
        from scis_help.answer.engine import Engine

        engine = Engine(settings)
    web = settings.web
    max_chars = settings.answer.max_message_chars
    contact = settings.answer.help_contact
    trusted_urls = urls_in(contact)
    sessions = SessionStore(
        idle_minutes=web.session_idle_minutes,
        max_sessions=web.max_sessions,
        new_conversation=_new_conversation,
    )
    limiter = RateLimiter(web.rate_per_minute)
    index_pages = _index_pages()

    app = FastAPI(
        title="SCIS Help Assistant (unofficial)", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.engine = engine
    if web.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=web.allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, error: RequestValidationError) -> JSONResponse:
        # Never echo the input back: it may be a user's message.
        return _error(400, "Bad request.")

    def desk_refused(request: Request) -> bool:
        return web.desk_local_only and not is_loopback(_client(request))

    def answer_json(answer: "Answer") -> dict[str, Any]:
        text = answer.text
        question = (answer.clarifying_question or "").strip()
        if answer.status == "clarify" and question and question not in text:
            text = f"{text}\n\n{question}".strip()
        notice = BUDGET_NOTICE if answer.reason == "budget" else None
        return {
            "answer_id": answer.answer_id,
            "status": answer.status,
            "answer_html": render_answer(text, answer.sources, trusted_urls),
            "sources": [asdict(source) for source in answer.sources],
            "clarifying_question": answer.clarifying_question,
            "scrub_notice": answer.scrub_notice,
            "notice": notice,
        }

    def limit_json() -> dict[str, Any]:
        return {
            "answer_id": None,
            "status": "not_covered",
            "answer_html": render_answer(LIMIT_TEXT.format(contact=contact), [], trusted_urls),
            "sources": [],
            "clarifying_question": None,
            "scrub_notice": False,
            "notice": LIMIT_NOTICE,
        }

    @app.post("/api/chat")
    def chat(body: ChatRequest, request: Request) -> Response:
        message = body.message.strip()
        if not message or len(message) > max_chars:
            return _error(400, f"Please write a message of 1 to {max_chars} characters.")
        if body.mode == "desk" and desk_refused(request):
            return _error(403, "Desk mode is only available on this computer.")
        retry_after = limiter.check(_client(request))
        if retry_after is not None:
            log.info("web chat rate limited", extra={"retry_after": retry_after})
            return _error(
                429,
                f"You're sending messages too fast. Please wait {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)},
            )
        session = sessions.get(body.session_id)
        with session.lock:
            conversation = session.conversation
            if (
                conversation.messages >= web.session_max_messages
                or conversation.tokens >= web.session_max_tokens
            ):
                log.info("web chat session limit", extra={"mode": body.mode})
                return JSONResponse(limit_json())
            try:
                answer = engine.ask(message, mode=body.mode, conversation=conversation)
            except Exception as error:
                log.error("web chat failed", extra={"error": type(error).__name__})
                return _error(500, "Something went wrong. Please try again.")
            session.remember(answer)
        log.info("web chat", extra={"mode": body.mode, "status": answer.status})
        return JSONResponse(answer_json(answer))

    @app.post("/api/feedback", status_code=204)
    def give_feedback(body: FeedbackRequest) -> Response:
        session = sessions.find(body.session_id)
        answer = session.take_for_feedback(body.answer_id) if session is not None else None
        if answer is None:
            return _error(404, "No such answer in this chat, or it was already rated.")
        _record_feedback(answer, body.helpful)
        return Response(status_code=204)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "index_pages": index_pages, "llm": _llm_state(settings)}

    def page(name: str) -> FileResponse:
        headers = {"Content-Security-Policy": PAGE_CSP, "Cache-Control": "no-cache"}
        return FileResponse(STATIC_DIR / name, media_type="text/html", headers=headers)

    @app.get("/", include_in_schema=False)
    def demo_page() -> Response:
        return page("demo.html")

    @app.get("/desk", include_in_schema=False)
    def desk_page(request: Request) -> Response:
        if desk_refused(request):
            return _error(403, "Desk mode is only available on this computer.")
        return page("desk.html")

    @app.get("/widget.js", include_in_schema=False)
    def widget_js() -> Response:
        return FileResponse(
            STATIC_DIR / "widget.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/widget.css", include_in_schema=False)
    def widget_css() -> Response:
        return FileResponse(
            STATIC_DIR / "widget.css", media_type="text/css", headers={"Cache-Control": "no-cache"}
        )

    return app
