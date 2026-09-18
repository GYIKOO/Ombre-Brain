"""Lossless bounded input splitting with durable per-segment model results."""

import bisect
import hashlib
import json
import time

from .ingest_archive import ReceiptArchive, archive_directory

CHUNK_SIZE = 4096


def split_content(content, limit=CHUNK_SIZE):
    if limit < 1:
        raise ValueError("Chunk limit must be positive")
    start = 0
    while start < len(content):
        end = min(start + limit, len(content))
        if end < len(content):
            # Keep serialized phone messages together where possible, then
            # paragraphs, then lines. Overlong individual lines are hard split.
            for separator in ("\n    },\n", "\n\n", "\n"):
                boundary = content.rfind(separator, start, end)
                if boundary >= start:
                    end = boundary + len(separator)
                    break
        yield start, content[start:end]
        start = end


def _line_starts(text):
    starts, offset = [], 0
    for line in text.splitlines(keepends=True):
        starts.append(offset)
        offset += len(line)
    return starts or [0]


def map_ranges(items, segment, offset, original):
    """Convert valid local source lines to original lines, including hard cuts."""
    local = _line_starts(segment)
    global_lines = _line_starts(original)
    result = []
    for item in items:
        item = dict(item)
        ranges = []
        for pair in item.get("source_ranges", []) or []:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            left, right = pair
            if type(left) is not int or type(right) is not int or not 1 <= left <= right <= len(local):
                continue
            ranges.append(
                [
                    bisect.bisect_right(global_lines, offset + local[left - 1]),
                    bisect.bisect_right(global_lines, offset + local[right - 1]),
                ]
            )
        item["source_ranges"] = ranges
        # Derived from the actual segment, including reused old checkpoints.
        item["_ingest_chunk_ranges"] = [[
            bisect.bisect_right(global_lines, offset),
            bisect.bisect_right(global_lines, offset + max(0, len(segment) - 1)),
        ]]
        result.append(item)
    return result


async def digest_all(content, settings, digest_one):
    """All segments must finish before grow can start creating any buckets.

    Cached results avoid repeated model calls, NOT repeated bucket writes.
    Existing grow retry guard remains responsible for immediate exact retries.
    """
    fingerprint = hashlib.sha256(
        json.dumps(
            {"version": 1, "content": content, "settings": settings, "limit": CHUNK_SIZE},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    store = ReceiptArchive(archive_directory())
    path = store.root / ("receipt-" + fingerprint[:32] + ".json")
    chunks = list(split_content(content))
    record = None
    if path.exists():
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
            if (
                candidate.get("fingerprint") == fingerprint
                and candidate.get("tool") == "digest_chunks"
                and len(candidate.get("chunks", [])) == len(chunks)
            ):
                record = candidate
        except (ValueError, TypeError):
            pass
    if record is None:
        record = {
            "archive_version": 1,
            "receipt_id": fingerprint[:32],
            "fingerprint": fingerprint,
            "received_at": time.time(),
            "tool": "digest_chunks",
            "status": "received",
            "arguments": {"content": content},
            "chunks": [{"offset": start, "length": len(text), "status": "pending"} for start, text in chunks],
        }
        store._write(path, record)
    results = []
    for index, (offset, text) in enumerate(chunks):
        state = record["chunks"][index]
        if state.get("status") != "done":
            state["status"] = "processing"
            state["attempts"] = state.get("attempts", 0) + 1
            record["status"] = "processing"
            store._write(path, record)
            try:
                if text.strip():
                    items, diagnostic = await digest_one(text)
                    if not items:
                        raise RuntimeError(diagnostic or "Empty digest result")
                else:
                    items = []
                state.update(status="done", items=items)
                state.pop("error_type", None)
                store._write(path, record)
            except Exception as error:
                state.update(status="failed", error_type=type(error).__name__)
                record["status"] = "failed"
                store._write(path, record)
                raise RuntimeError(
                    f"长文第 {index + 1}/{len(chunks)} 段整理失败；已完成段保留，下次重试复用。"
                ) from error
        results.extend(map_ranges(state["items"], text, offset, content))
    record["status"] = "returned"
    record["finished_at"] = time.time()
    store._write(path, record)
    return results
