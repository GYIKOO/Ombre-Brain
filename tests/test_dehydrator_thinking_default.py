"""No real provider calls: verify request payload and checkpoint settings."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from dehydrator import Dehydrator, dehydration_extra_body

@pytest.mark.parametrize("model", ["deepseek-flash", "deepseek-pro", "deepseek-chat", "deepseek-v4-flash", "deepseek/deepseek-v4-pro"])
def test_switchable_models_default_off(model):
    configured = {"other": "keep"}
    assert dehydration_extra_body(model, "openai_compat", configured) == {"other": "keep", "thinking": {"type": "disabled"}}
    assert configured == {"other": "keep"}

@pytest.mark.parametrize("configured", [
    {"thinking": {"type": "enabled"}}, {"thinking": {"type": "disabled"}},
    {"reasoning_effort": "high"}, {"reasoning": {"effort": "high"}},
])
def test_explicit_choice_wins(configured):
    assert dehydration_extra_body("deepseek-flash", "openai_compat", configured) == configured

@pytest.mark.parametrize("model,fmt", [("gpt-5", "openai_compat"), ("deepseek-reasoner", "openai_compat"), ("custom-model", "openai_compat"), ("deepseek-flash", "anthropic"), ("gemini-2.5-flash", "gemini")])
def test_does_not_send_foreign_switch(model, fmt):
    assert dehydration_extra_body(model, fmt, {}) == {}

@pytest.mark.asyncio
async def test_real_request_defaults_and_hot_reload(tmp_path):
    d = Dehydrator({"buckets_dir": str(tmp_path), "dehydration": {"api_key": "test-key", "model": "deepseek-flash"}})
    create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]))
    original = d.client
    d.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    try:
        await d._chat_once("system", "synthetic")
        assert create.call_args.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
        d.extra_body = {"thinking": {"type": "enabled"}}
        await d._chat_once("system", "synthetic")
        assert create.call_args.kwargs["extra_body"] == d.extra_body
        d.extra_body = {}  # dashboard save must not drop the default
        await d._chat_once("system", "synthetic")
        assert create.call_args.kwargs["extra_body"]["thinking"]["type"] == "disabled"
        d.model = "gpt-5"  # no sticky vendor extension after changing model
        await d._chat_once("system", "synthetic")
        assert create.call_args.kwargs["extra_body"] is None
    finally:
        d.close()
        await original.close()

@pytest.mark.asyncio
async def test_chunk_cache_uses_effective_thinking_settings(tmp_path, monkeypatch):
    from ombrebrain.storage import digest_chunks
    digest = AsyncMock(return_value=[{"content": "synthetic"}])
    monkeypatch.setattr(digest_chunks, "digest_all", digest)
    d = Dehydrator({"buckets_dir": str(tmp_path), "dehydration": {"api_key": "test-key", "model": "deepseek-flash"}})
    try:
        await d.digest("x" * 5000)
        disabled = digest.call_args.args[1]["extra_body"]
        d.extra_body = {"thinking": {"type": "enabled"}}
        await d.digest("x" * 5000)
        enabled = digest.call_args.args[1]["extra_body"]
        assert disabled == {"thinking": {"type": "disabled"}}
        assert enabled != disabled
    finally:
        await d.client.close()
        d.close()
