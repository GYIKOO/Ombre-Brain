"""Verify attribution policy reaches every generated-memory path without API calls."""
from unittest.mock import AsyncMock
import pytest
from dehydrator import Dehydrator, _PROMPT_VERSION, _perspective_rule


def test_named_attribution_policy():
    rule = _perspective_rule("小明")
    assert _PROMPT_VERSION >= 5
    for required in ("群聊", "speaker/name", "不能全局替换", "第三人称", "必须标明说话人", "归属不明", "why_remembered", "小明"):
        assert required in rule
    assert "AI 自身永远用「我」" not in rule
    assert "原文里的「你/她/他」都指" not in rule


@pytest.mark.asyncio
async def test_all_generation_paths_and_chunk_cache_use_policy(tmp_path, monkeypatch):
    from ombrebrain.storage import digest_chunks
    d = Dehydrator({"buckets_dir": str(tmp_path), "human": "小明", "dehydration": {"api_key": "test-key"}})
    chat = AsyncMock(return_value="[]")
    monkeypatch.setattr(d, "_chat", chat)
    text = '甲：我下班了。乙：我给你留了饭。丙：我还在开会。'
    try:
        await d._api_dehydrate(text)
        await d._api_merge(text, "乙：饭在冰箱。")
        await d._api_digest_detailed(text)
        chat.return_value = '{}'
        await d._api_analyze(text, include_why=True)
        assert chat.call_count == 4
        for call in chat.call_args_list:
            assert _perspective_rule("小明") in call.args[0]
            assert "一句第一人称" not in call.args[0]
            assert "严格保留第一人称" not in call.args[0]
        assert text in chat.call_args_list[2].args[1]
        digest = AsyncMock(return_value=[])
        monkeypatch.setattr(digest_chunks, "digest_all", digest)
        await d.digest(text * 300)
        settings = digest.call_args.args[1]
        assert settings["prompt_version"] == _PROMPT_VERSION
        assert _perspective_rule("小明") in settings["prompt"]
    finally:
        await d.client.close()
        d.close()
