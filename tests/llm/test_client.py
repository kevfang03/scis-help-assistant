import json
import ssl
from datetime import datetime, timedelta

import anthropic
import pytest
import truststore
from pydantic import BaseModel

from scis_help.common import paths
from scis_help.common.config import LlmSettings, Settings
from scis_help.common.logsetup import configure_logging
from scis_help.llm import client as client_module
from scis_help.llm.client import LlmClient, LlmFailed, LlmUnavailable
from scis_help.llm.keys import get_api_key, set_api_key
from tests.llm_fakes import FakeSdk, connection_error, response, status_error, timeout_error

KEY = "sk-ant-test-key"


class Out(BaseModel):
    ok: bool


def local(year=2026, month=10, day=21, hour=12, minute=0):
    return datetime(year, month, day, hour, minute).astimezone()


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make(sdk=None, *, key=KEY, clock=None, **llm):
    settings = Settings(llm=LlmSettings(**llm))
    sdk = sdk if sdk is not None else FakeSdk()
    return LlmClient(settings, api_key=key, sdk_client=sdk, clock=clock or Clock(local())), sdk


def call(client, **kwargs):
    args = {"system": "rules", "user": "question", "output": Out, "purpose": "test"}
    args.update(kwargs)
    return client.complete(max_tokens=100, **args)


def test_ok_call_request_shape():
    client, sdk = make()
    result = call(client)
    assert result.status == "ok" and result.value == Out(ok=True)
    request = sdk.calls[0]
    assert request["model"] == "claude-haiku-5-5"
    assert request["max_tokens"] == 100
    assert request["system"] == [
        {"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}
    ]
    assert request["messages"] == [{"role": "user", "content": "question"}]
    assert request["output_config"]["effort"] == "low"
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert request["output_config"]["format"]["schema"]["properties"]["ok"]["type"] == "boolean"
    for sampling in ("temperature", "top_p", "top_k", "thinking"):
        assert sampling not in request


@pytest.mark.parametrize(
    ("settings", "key", "reason"),
    [({"enabled": False}, KEY, "disabled"), ({}, None, "no_key")],
)
def test_unavailable_makes_no_call(settings, key, reason):
    client, sdk = make(key=key, **settings)
    with pytest.raises(LlmUnavailable) as error:
        call(client)
    assert error.value.reason == reason
    assert sdk.calls == []


def test_force_enabled_skips_only_the_enabled_check():
    client, sdk = make(enabled=False)
    assert call(client, force_enabled=True).status == "ok"
    client, sdk = make(key=None, enabled=False)
    with pytest.raises(LlmUnavailable) as error:
        call(client, force_enabled=True)
    assert error.value.reason == "no_key"


def test_budget_stops_before_the_call():
    sdk = FakeSdk([response({"ok": True}, input_tokens=900, output_tokens=100)])
    client, _ = make(sdk, daily_token_budget=1_000)
    call(client)
    assert client.tokens_used_today() == 1_000
    assert client.budget_left() == 0
    with pytest.raises(LlmUnavailable) as error:
        call(client)
    assert error.value.reason == "budget"
    assert len(sdk.calls) == 1


def test_budget_arithmetic_counts_cache_reads_at_ten_percent_rounded_up():
    sdk = FakeSdk(
        [response({"ok": True}, input_tokens=100, output_tokens=50, cache_read=1001, cache_write=7)]
    )
    client, _ = make(sdk, daily_token_budget=10_000)
    call(client)
    assert client.tokens_used_today() == 100 + 50 + 7 + 101
    usage = client.usage_today()
    assert usage["calls"] == 1 and usage["cache_read_tokens"] == 1001


def test_cost_uses_the_configured_prices():
    sdk = FakeSdk([response({"ok": True}, input_tokens=1_000_000, output_tokens=0)])
    client, _ = make(sdk, daily_token_budget=10_000_000)
    assert call(client).usage.cost_usd == pytest.approx(0.10)
    sdk.queue.append(
        response(
            {"ok": True},
            input_tokens=0,
            output_tokens=1_000_000,
            cache_read=1_000_000,
            cache_write=1_000_000,
        )
    )
    assert call(client).usage.cost_usd == pytest.approx(0.50 + 0.01 + 0.125)


def test_day_rolls_over_at_local_midnight_and_31_days_are_kept():
    clock = Clock(local(hour=23, minute=59))
    client, sdk = make(clock=clock)
    call(client)
    assert client.usage_today()["calls"] == 1
    clock.now = clock.now + timedelta(minutes=2)
    assert client.usage_today()["calls"] == 0
    call(client)
    # A fresh local noon, not now + 31 days: adding days keeps the old UTC offset, so a
    # daylight-saving change in between would move the date in some time zones.
    clock.now = local(month=11, day=21)
    call(client)
    data = json.loads((paths.data_dir() / "llm-usage.json").read_text(encoding="utf-8"))
    days = sorted(data["days"])
    assert "2026-10-21" not in days  # 31 days before the last call: pruned
    assert len(days) == 2 and data["schema"] == 1


def test_corrupt_usage_file_starts_fresh():
    path = paths.data_dir() / "llm-usage.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    client, _ = make()
    assert client.tokens_used_today() == 0
    call(client)
    assert client.usage_today()["calls"] == 1


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (lambda: timeout_error(), "timeout"),
        (lambda: connection_error(), "connection"),
        (lambda: status_error(anthropic.RateLimitError, 429), "rate_limited"),
        (lambda: status_error(anthropic.AuthenticationError, 401), "auth"),
        (lambda: status_error(anthropic.InternalServerError, 500), "http_500"),
        (lambda: status_error(anthropic.BadRequestError, 400), "http_400"),
    ],
)
def test_failures_have_reason_codes(error, reason):
    client, _ = make(FakeSdk([error()]))
    with pytest.raises(LlmFailed) as raised:
        call(client)
    assert raised.value.reason == reason
    assert client.usage_today()["calls"] == 0


def test_bad_output_is_recorded_then_raised():
    client, _ = make(FakeSdk([response('{"ok": "maybe"}')]))
    with pytest.raises(LlmFailed) as raised:
        call(client)
    assert raised.value.reason == "bad_output"
    assert client.usage_today()["calls"] == 1


def test_refusal_and_truncation_are_results_without_value():
    sdk = FakeSdk(
        [
            response("I can't help with that", stop_reason="refusal"),
            response('{"ok": tr', stop_reason="max_tokens"),
        ]
    )
    client, _ = make(sdk)
    refused = call(client)
    assert refused.status == "refused" and refused.value is None
    truncated = call(client)
    assert truncated.status == "truncated" and truncated.value is None
    assert client.usage_today()["calls"] == 2


def test_log_line_has_counts_and_no_text(isolated_home):
    configure_logging(paths.log_dir())
    client, _ = make(FakeSdk([response({"ok": True}, cache_read=640)]))
    call(client, system="SECRET-SYSTEM", user="SECRET-QUESTION")
    log_text = (paths.log_dir() / "scis-help.log").read_text(encoding="utf-8")
    line = json.loads(next(line for line in log_text.splitlines() if '"llm call"' in line))
    assert line["cache_read_tokens"] == 640 and line["purpose"] == "test"
    assert line["status"] == "ok" and line["model"] == "claude-haiku-5-5"
    assert "SECRET" not in log_text and KEY not in log_text


def test_sdk_client_is_built_with_a_truststore_context(monkeypatch):
    built = {}

    def fake_http_client(**kwargs):
        built["verify"] = kwargs["verify"]
        return "http-client"

    def fake_anthropic(**kwargs):
        built.update(kwargs)
        return FakeSdk()

    monkeypatch.setattr(client_module.anthropic, "DefaultHttpxClient", fake_http_client)
    monkeypatch.setattr(client_module.anthropic, "Anthropic", fake_anthropic)
    client = LlmClient(Settings(), api_key=KEY)
    call(client)
    assert isinstance(built["verify"], truststore.SSLContext)
    assert built["verify"].verify_mode == ssl.CERT_REQUIRED
    assert built["http_client"] == "http-client"
    assert built["api_key"] == KEY and built["max_retries"] == 2 and built["timeout"] == 30.0


def test_key_round_trip_and_prefix_check(memory_keyring):
    assert get_api_key() is None
    with pytest.raises(ValueError) as error:
        set_api_key("not-a-key-123")
    assert "not-a-key-123" not in str(error.value)
    set_api_key("  sk-ant-abc  ")
    assert get_api_key() == "sk-ant-abc"
    assert memory_keyring.passwords == {("scis-help", "anthropic-api-key"): "sk-ant-abc"}
    assert LlmClient(Settings()).key_saved
    assert "sk-ant-abc" not in repr(LlmClient(Settings()))
