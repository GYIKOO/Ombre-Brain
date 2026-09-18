"""Historical time for receipt recovery, independent of processing time.

Only structured original timestamps are trusted, never dates invented by a model.
ContextVars isolate concurrent receipts and grow items.
"""
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import json
import math
import re

recovery_time = ContextVar("recovery_time", default=None)

def parse_time(value, ceiling=None):
    try:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?", value)):
            seconds = float(value)
            if not math.isfinite(seconds):
                return None
            if seconds > 100_000_000_000:
                seconds /= 1000
            date = datetime.fromtimestamp(seconds, timezone.utc)
        elif isinstance(value, str):
            date = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        else:
            return None
        if date.year < 2000 or (ceiling and date.timestamp() > ceiling + 300):
            return None
        return date
    except (ValueError, OverflowError, OSError):
        return None

def message_spans(content):
    """Decode actual JSON objects to keep braces/dates inside chat text inert."""
    try:
        obj = json.loads(content)
        if not isinstance(obj, dict) or not isinstance(obj.get("messages"), list):
            return []
        # Locate the top-level messages value using the decoder, not text search.
        decoder = json.JSONDecoder()
        pos = content.index("{") + 1
        while True:
            while content[pos].isspace() or content[pos] == ",": pos += 1
            key, pos = decoder.raw_decode(content, pos)
            while content[pos].isspace() or content[pos] == ":": pos += 1
            if key == "messages": break
            _, pos = decoder.raw_decode(content, pos)
        pos += 1  # opening array
        rows = []
        for message in obj["messages"]:
            while content[pos].isspace() or content[pos] == ",": pos += 1
            start = pos
            _, pos = decoder.raw_decode(content, pos)
            rows.append((content[:start].count("\n") + 1, content[:pos].count("\n") + 1, message))
        return rows
    except (ValueError, TypeError, IndexError):
        return []

def select_time(content, ranges=None):
    context = recovery_time.get()
    if context is None:
        return None
    received = parse_time(context["received_at"])
    if received is None:
        raise ValueError("原始接收时间无效，不能用重试时间替代")
    ranges = [pair for pair in (ranges or []) if isinstance(pair, (list, tuple)) and len(pair) == 2 and all(type(n) is int for n in pair) and 1 <= pair[0] <= pair[1]]
    dates = []
    for start, end, message in message_spans(content):
        if ranges and not any(left <= end and right >= start for left, right in ranges):
            continue
        if not isinstance(message, dict): continue
        for key in ("timestamp", "createdAt", "created_at", "time", "date"):
            date = parse_time(message.get(key), received.timestamp())
            if date:
                dates.append(date)
                break
    return {**context, "event_start": (min(dates) if dates else received).isoformat(),
            "event_end": (max(dates) if dates else received).isoformat(),
            "time_basis": "original_messages" if dates else "received_at"}

@contextmanager
def replay_time(record):
    received = parse_time(record.get("received_at"))
    if received is None:
        raise ValueError("原始接收时间无效，无法安全重试")
    token = recovery_time.set({"receipt_id": record["receipt_id"], "received_at": received.timestamp()})
    try:
        arguments = record["arguments"]
        recovery_time.set(select_time(arguments.get("source_content") or arguments.get("content", ""), arguments.get("source_ranges")))
        yield
    finally:
        recovery_time.reset(token)

def timed_item(source):
    def decorate(function):
        @wraps(function)
        async def wrapped(item):
            token = recovery_time.set(select_time(source(), item.get("_source_ranges") or item.get("_ingest_chunk_ranges")))
            try:
                return await function(item)
            finally:
                recovery_time.reset(token)
        return wrapped
    return decorate

def bucket_time(source_tool):
    context = recovery_time.get()
    if source_tool not in ("hold", "grow") or not context:
        return {}
    return {"event_start": context["event_start"], "event_end": context["event_end"],
            "time_basis": context["time_basis"],
            "ingest_received_at": datetime.fromtimestamp(context["received_at"], timezone.utc).isoformat(),
            "recovery_receipt_id": context["receipt_id"]}
