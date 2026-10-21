"""The LLM client: every Claude API call goes through ``LlmClient.complete`` (ARCHITECTURE.md D6).

Around the official ``anthropic`` SDK it adds:

- checks before any network, in this order: ``llm.enabled``, the daily token budget, the API
  key. Each failure is an ``LlmUnavailable`` with a reason code;
- structured output from a pydantic model, effort from config, no sampling parameters, and the
  system prompt as one block marked ``cache_control: ephemeral``;
- TLS verified against the operating system's trust store (``common/tls.py``); never disabled;
- one ``llm call`` log line per call and per-day totals in ``llm-usage.json`` (31 days kept):
  counts, model and cost only, never the prompt, the sources, the question or the output.

The output is parsed here rather than by ``messages.parse()``: ``parse()`` validates the JSON
inside the call, so a refusal or a ``max_tokens`` cut-off would raise before ``stop_reason`` and
the usage could be read.
"""

import json
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta
from typing import Literal

import anthropic
import keyring.errors
from pydantic import BaseModel, ValidationError

from scis_help.common import paths, tls
from scis_help.common.atomic import write_json_atomic
from scis_help.common.config import Settings
from scis_help.llm.keys import get_api_key

log = logging.getLogger(__name__)

USAGE_FILE = "llm-usage.json"
USAGE_KEEP_DAYS = 31
# Cache reads count towards the daily budget at 10% (rounded up), like their price.
CACHE_READ_BUDGET_WEIGHT = 0.1
MAX_RETRIES = 2
_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class LlmUsage:
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float  # estimate from the LlmSettings prices


@dataclass(frozen=True, slots=True)
class LlmResult[T: BaseModel]:
    value: T | None  # None unless status is "ok"
    status: Literal["ok", "refused", "truncated"]
    usage: LlmUsage


class LlmUnavailable(Exception):
    """No call was made. ``reason``: "disabled", "budget" or "no_key"."""

    def __init__(self, reason: Literal["disabled", "budget", "no_key"]) -> None:
        super().__init__(f"LLM call not made ({reason})")
        self.reason = reason


class LlmFailed(Exception):
    """The call was made and failed. ``reason``: "timeout", "connection", "rate_limited",
    "auth", "http_<status>" or "bad_output"."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"LLM call failed ({reason})")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _Day:
    """One local day's totals in ``llm-usage.json``."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def budget_tokens(self) -> int:
        cache_reads = math.ceil(self.cache_read_tokens * CACHE_READ_BUDGET_WEIGHT)
        return self.input_tokens + self.output_tokens + self.cache_write_tokens + cache_reads

    def plus(self, usage: LlmUsage) -> "_Day":
        return _Day(
            calls=self.calls + 1,
            input_tokens=self.input_tokens + usage.input_tokens,
            output_tokens=self.output_tokens + usage.output_tokens,
            cache_read_tokens=self.cache_read_tokens + usage.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + usage.cache_write_tokens,
            cost_usd=self.cost_usd + usage.cost_usd,
        )


_DAY_FIELDS = tuple(item.name for item in fields(_Day))


def _load_days() -> dict[str, _Day]:
    """``{"schema": 1, "days": {"<ISO date>": {...}}}``; a corrupt file starts fresh."""
    path = paths.data_dir() / USAGE_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["schema"] != _SCHEMA:
            raise ValueError("unknown schema")
        days: dict[str, _Day] = {}
        for day, record in data["days"].items():
            date.fromisoformat(day)
            days[day] = _Day(
                calls=int(record["calls"]),
                input_tokens=int(record["input_tokens"]),
                output_tokens=int(record["output_tokens"]),
                cache_read_tokens=int(record["cache_read_tokens"]),
                cache_write_tokens=int(record["cache_write_tokens"]),
                cost_usd=float(record["cost_usd"]),
            )
        return days
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        log.warning("llm usage reset")
        return {}


def _save_days(days: dict[str, _Day], today: date) -> None:
    oldest = today - timedelta(days=USAGE_KEEP_DAYS - 1)
    data = {
        "schema": _SCHEMA,
        "days": {
            day: {name: getattr(totals, name) for name in _DAY_FIELDS}
            for day, totals in sorted(days.items())
            if date.fromisoformat(day) >= oldest
        },
    }
    write_json_atomic(paths.data_dir() / USAGE_FILE, data)


class LlmClient:
    def __init__(
        self,
        settings: Settings,
        *,
        api_key: str | None = None,
        sdk_client: "anthropic.Anthropic | None" = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """``api_key`` None reads keyring; ``sdk_client`` lets tests inject a fake. The real
        SDK client is built on the first call that passes every check."""
        self._llm = settings.llm
        if api_key is None:
            try:
                api_key = get_api_key()
            except keyring.errors.KeyringError as error:
                log.warning("llm key unreadable", extra={"error": type(error).__name__})
        self._api_key = api_key or None
        self._sdk_client = sdk_client
        self._clock = clock or (lambda: datetime.now().astimezone())

    def __repr__(self) -> str:
        return f"LlmClient(model={self._llm.model!r}, key_saved={self._api_key is not None})"

    @property
    def key_saved(self) -> bool:
        return self._api_key is not None

    # -- usage --

    def _today(self) -> date:
        """Today in the computer's local time zone."""
        return self._clock().astimezone().date()

    def _day(self) -> _Day:
        return _load_days().get(self._today().isoformat(), _Day())

    def tokens_used_today(self) -> int:
        """Tokens counted against the budget today: input + output + cache writes + cache reads
        at 10% (rounded up)."""
        return self._day().budget_tokens

    def budget_left(self) -> int:
        return max(0, self._llm.daily_token_budget - self.tokens_used_today())

    def usage_today(self) -> dict[str, float | int]:
        day = self._day()
        return {
            "calls": day.calls,
            "input_tokens": day.input_tokens,
            "output_tokens": day.output_tokens,
            "cache_read_tokens": day.cache_read_tokens,
            "cache_write_tokens": day.cache_write_tokens,
            "budget_tokens": day.budget_tokens,
            "cost_usd": day.cost_usd,
        }

    def _record(self, usage: LlmUsage) -> None:
        today = self._today()
        days = _load_days()
        key = today.isoformat()
        days[key] = days.get(key, _Day()).plus(usage)
        try:
            _save_days(days, today)
        except OSError as error:
            log.warning("llm usage not saved", extra={"error": type(error).__name__})

    def _cost(self, input_tokens: int, output_tokens: int, read: int, write: int) -> float:
        llm = self._llm
        return (
            input_tokens * llm.input_usd_per_mtok
            + output_tokens * llm.output_usd_per_mtok
            + read * llm.cache_read_usd_per_mtok
            + write * llm.cache_write_usd_per_mtok
        ) / 1_000_000

    # -- the call --

    def _sdk(self) -> "anthropic.Anthropic":
        if self._sdk_client is None:
            self._sdk_client = anthropic.Anthropic(
                api_key=self._api_key,
                timeout=float(self._llm.timeout_seconds),
                max_retries=MAX_RETRIES,
                http_client=anthropic.DefaultHttpxClient(verify=tls.ssl_context()),
            )
        return self._sdk_client

    def _check(self, force_enabled: bool) -> None:
        if not (self._llm.enabled or force_enabled):
            raise LlmUnavailable("disabled")
        if self.tokens_used_today() >= self._llm.daily_token_budget:
            raise LlmUnavailable("budget")
        if self._api_key is None:
            raise LlmUnavailable("no_key")

    def complete[T: BaseModel](
        self,
        *,
        system: str,
        user: str,
        output: type[T],
        purpose: str,
        max_tokens: int,
        force_enabled: bool = False,
    ) -> LlmResult[T]:
        """One structured-output call. ``purpose`` is a short code for the log ("answer",
        "judge", "llm_test"); ``force_enabled`` skips only the ``llm.enabled`` check.

        LlmUnavailable when no call was made; LlmFailed when the call failed or its output did
        not validate. A refusal or a ``max_tokens`` cut-off is a result with no value.
        """
        self._check(force_enabled)
        model = self._llm.model
        context = {"purpose": purpose, "model": model}
        try:
            response = self._sdk().messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={
                    "effort": self._llm.effort,
                    "format": {"type": "json_schema", "schema": anthropic.transform_schema(output)},
                },
            )
        except anthropic.AuthenticationError as error:
            raise self._failed("auth", context) from error
        except anthropic.RateLimitError as error:
            raise self._failed("rate_limited", context) from error
        except anthropic.APITimeoutError as error:
            raise self._failed("timeout", context) from error
        except anthropic.APIConnectionError as error:
            raise self._failed("connection", context) from error
        except anthropic.APIStatusError as error:
            raise self._failed(f"http_{error.status_code}", context) from error

        usage = self._usage_of(response, model)
        value: T | None = None
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            status = "refused"
        elif stop_reason == "max_tokens":
            status = "truncated"
        else:
            value = _parse(response, output)
            status = "ok" if value is not None else "bad_output"

        self._record(usage)
        log.info(
            "llm call",
            extra={
                **context,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_tokens": usage.cache_read_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
                "cost_usd": round(usage.cost_usd, 6),
                "status": status,
                "stop_reason": stop_reason,
            },
        )
        if status == "bad_output":
            raise LlmFailed("bad_output")
        return LlmResult(value, status, usage)  # type: ignore[arg-type]

    def _usage_of(self, response: object, model: str) -> LlmUsage:
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) or 0
        output_tokens = getattr(usage, "output_tokens", None) or 0
        read = getattr(usage, "cache_read_input_tokens", None) or 0
        write = getattr(usage, "cache_creation_input_tokens", None) or 0
        return LlmUsage(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=read,
            cache_write_tokens=write,
            cost_usd=self._cost(input_tokens, output_tokens, read, write),
        )

    @staticmethod
    def _failed(reason: str, context: dict[str, str]) -> LlmFailed:
        log.info("llm call failed", extra={**context, "reason": reason})
        return LlmFailed(reason)


def _parse[T: BaseModel](response: object, output: type[T]) -> T | None:
    """The first text block as ``output``, or None if there is none or it does not validate.
    Thinking blocks (adaptive thinking is on by default) are skipped."""
    for block in getattr(response, "content", None) or ():
        if getattr(block, "type", None) == "text":
            try:
                return output.model_validate_json(block.text)
            except ValidationError:
                return None
    return None
