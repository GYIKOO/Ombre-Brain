"""Synthetic inputs only: actor must reach every chunk without rewriting evidence."""
import json
from unittest.mock import AsyncMock
import pytest
from dehydrator import Dehydrator

@pytest.mark.asyncio
async def test_actor_on_every_chunk_and_no_cross_request_leak(tmp_path, monkeypatch):
    from ombrebrain.storage import digest_chunks
    d = Dehydrator({"buckets_dir": str(tmp_path), "dehydration": {"api_key": "test", "model": "deepseek-flash"}})
    result = json.dumps([{"content":"乙回应甲", "source_ranges":[[1,1]], "name":"回应"}], ensure_ascii=False)
    chat = AsyncMock(return_value=result)
    monkeypatch.setattr(d, "_chat", chat)
    settings_seen=[]
    async def fake_all(raw, settings, callback):
        settings_seen.append(settings)
        result=[]
        for _, part in digest_chunks.split_content(raw):
            items, diagnostic = await callback(part)
            assert items, diagnostic
            result.extend(items)
        return result
    monkeypatch.setattr(digest_chunks, "digest_all", fake_all)
    actor={"name":"乙", "characterId":"synthetic-A", "members":["甲","乙","丙"]}
    raw=json.dumps({"source":"group", "_actor":actor, "messages":[{"role":"assistant", "name":"丙", "content":"合成测试内容"*1400}]}, ensure_ascii=False, indent=2)
    try:
        assert d.digest_max_tokens == 16384
        await d.digest(raw)
        assert chat.call_count > 1
        reconstructed=[]
        parts=list(digest_chunks.split_content(raw))
        for call, (_,part) in zip(chat.call_args_list,parts):
            assert 'synthetic-A' in call.args[0]
            assert '"丙"' in call.args[0]
            assert call.args[1] == "\n".join(f"{i}| {line}" for i,line in enumerate(part.splitlines(),1))
            assert call.kwargs['max_tokens'] == 16384
        assert json.loads(settings_seen[0]['actor_context'])['_actor'] == actor
        chat.reset_mock()
        await d.digest(json.dumps({"source":"private", "_actor":{"name":"丁","characterId":"synthetic-B"}, "messages":[{"role":"user","content":"你好"}]},ensure_ascii=False))
        assert 'synthetic-B' in chat.call_args.args[0]
        assert 'synthetic-A' not in chat.call_args.args[0]
        await d.digest('没有身份元数据的普通文本')
        assert 'synthetic-B' not in chat.call_args.args[0]
    finally:
        await d.client.close()
        d.close()
