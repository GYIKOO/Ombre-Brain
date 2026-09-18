import asyncio
import json
from datetime import datetime
import pytest
from ombrebrain.storage.ingest_time import replay_time, recovery_time, bucket_time, parse_time, select_time, message_spans, timed_item
from ombrebrain.storage.digest_chunks import map_ranges
from ombrebrain.storage import ingest_retry
from ombrebrain.storage.ingest_archive import ReceiptArchive, archive_ingest

RECEIVED = 1757894400  # 2025-09-15 UTC
def record(content="synthetic", rid="a" * 32):
    return {"received_at": RECEIVED, "receipt_id": rid, "arguments": {"content": content}}

def dialogue():
    return json.dumps({"source": "test", "messages": [
        {"role": "user", "timestamp": "2025-09-12T10:00:00-07:00", "content": "day one"},
        {"role": "assistant", "timestamp": 1757786400000, "content": "day two"},
    ]}, indent=2)

def test_receipt_fallback_bad_dates_and_context_reset():
    for value in (0, True, "1970-01-01", "garbage", float("nan"), "2099-01-01"):
        assert parse_time(value, RECEIVED) is None
    with replay_time(record()):
        metadata = bucket_time("hold")
        assert metadata["time_basis"] == "received_at"
        assert datetime.fromisoformat(metadata["event_start"]).timestamp() == RECEIVED
        assert bucket_time("import") == {}
    assert recovery_time.get() is None
    with pytest.raises(ValueError):
        with replay_time({**record(), "received_at": None}): pass

@pytest.mark.asyncio
async def test_parallel_items_use_own_message_ranges():
    content = dialogue()
    rows = message_spans(content)
    with replay_time(record(content)):
        @timed_item(lambda: content)
        async def item_time(item):
            await asyncio.sleep(0)
            return bucket_time("grow")
        results = await asyncio.gather(*[item_time({"_source_ranges": [[start, end]]}) for start,end,_ in rows])
        assert results[0]["event_start"] == "2025-09-12T17:00:00+00:00"
        assert results[0]["event_start"] != results[1]["event_start"]
        assert bucket_time("grow")["event_end"] == results[1]["event_end"]
    assert recovery_time.get() is None

def test_cached_chunk_without_model_ranges_uses_segment_span():
    content = dialogue()
    start, end, _ = message_spans(content)[1]
    lines = content.splitlines(keepends=True)
    offset = len("".join(lines[:start-1]))
    segment = "".join(lines[start-1:end])
    mapped = map_ranges([{"content": "old cached summary"}], segment, offset, content)[0]
    with replay_time(record(content)):
        selected = select_time(content, mapped["_ingest_chunk_ranges"])
        assert selected["event_start"] == selected["event_end"]
        assert selected["event_start"] != "2025-09-12T17:00:00+00:00"

@pytest.mark.asyncio
async def test_real_bucket_create_and_merge_preserve_history(bucket_mgr):
    with replay_time(record()):
        bid = await bucket_mgr.create(content="historical synthetic", source_tool="hold", title="history", defer_derived_index=True)
    metadata = (await bucket_mgr.get(bid))["metadata"]
    assert datetime.fromisoformat(metadata["created"]).timestamp() == RECEIVED
    assert datetime.fromisoformat(metadata["processed_at"]).timestamp() > RECEIVED
    assert metadata["name"].startswith(datetime.fromtimestamp(RECEIVED).strftime("%Y-%m-%d %H-%M-%S"))
    before = {k: metadata[k] for k in ("created", "last_active", "activation_count", "name")}
    with replay_time(record(dialogue(), "b" * 32)):
        assert await bucket_mgr.update(bid, content="merged synthetic", last_merged_by="grow", bump_active=True)
    after = (await bucket_mgr.get(bid))["metadata"]
    assert {k: after[k] for k in before} == before
    assert after["recovered_sources"][0]["event_start"] == "2025-09-12T17:00:00+00:00"
    fresh = await bucket_mgr.create(content="ordinary new content", source_tool="hold", defer_derived_index=True)
    fresh_meta = (await bucket_mgr.get(fresh))["metadata"]
    assert "recovery_receipt_id" not in fresh_meta
    assert datetime.fromisoformat(fresh_meta["created"]).timestamp() > RECEIVED

@pytest.mark.asyncio
async def test_manual_retry_preserves_first_receipt_across_child_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("OMBRE_INGEST_ARCHIVE_DIR", str(tmp_path))
    archive = ReceiptArchive(tmp_path)
    receipt = archive.begin("hold", {"content": "synthetic"})
    receipt[1]["received_at"] = RECEIVED
    archive.finish(receipt, "failed")
    observed = []
    @archive_ingest("hold")
    async def fake(content):
        observed.append(bucket_time("hold"))
        return "新建→test"
    await ingest_retry.start_retry(receipt[1]["receipt_id"], fake)
    await ingest_retry._tasks[receipt[1]["receipt_id"]]
    assert datetime.fromisoformat(observed[0]["event_start"]).timestamp() == RECEIVED
    assert recovery_time.get() is None


@pytest.mark.asyncio
async def test_internal_chunk_bounds_do_not_cross_public_validation(monkeypatch):
    from tools.grow import core
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    items = [{"content": "synthetic", "_ingest_chunk_ranges": [[1, 3]]}]
    monkeypatch.setattr(core.rt, "dehydrator", SimpleNamespace(digest=AsyncMock(return_value=items)))
    monkeypatch.setattr(core.rt, "logger", Mock())
    captured = []
    def validate(payload):
        captured.extend(payload)
        return "stop before writes"
    monkeypatch.setattr(core, "check_grow_items_payload", validate)
    assert await core.grow_core("synthetic") == "stop before writes"
    assert captured == [{"content": "synthetic"}]
    assert items[0]["_ingest_chunk_ranges"] == [[1, 3]]
