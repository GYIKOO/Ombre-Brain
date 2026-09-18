"""Explicit, single-process receipt recovery. No scheduled API calls."""
import asyncio
import importlib
import json
import re
import time
from .ingest_archive import ReceiptArchive, archive_directory, retry_parent, active_receipts

_tasks = {}

def outcome(tool, result):
    text = str(result)
    if text.startswith("✅ 已识别为刚才 grow 的重试；未重复写入。\n"):
        text = text.split("\n", 1)[1]
    counts = re.match(r"^(\d+)条(?:\([^)]*\))?\|新(\d+)合(\d+) batch:", text)
    if counts:
        return "succeeded" if int(counts[1]) > 0 and int(counts[1]) == int(counts[2]) + int(counts[3]) else "partial"
    if text.startswith(("已处理过的 ", "短内容已按 hold 路径保存", "新建→", "合并→", "📌钉选→", "🫧feel→")):
        return "succeeded"
    return "uncertain"

def read_receipt(receipt_id):
    if not isinstance(receipt_id, str) or not re.fullmatch(r"[0-9a-f]{32}", receipt_id):
        raise ValueError("无效缓存编号")
    root = archive_directory()
    path = root / ("receipt-" + receipt_id + ".json")
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise ValueError("无效缓存路径")
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("receipt_id") != receipt_id or record.get("tool") not in ("hold", "grow") or record.get("retry_of") or not isinstance(record.get("arguments"), dict):
        raise ValueError("不是可重试的原始 hold/grow 缓存")
    return path, record

def state(record):
    rid = record["receipt_id"]
    if rid in _tasks or rid in active_receipts:
        return "running"
    retry = record.get("manual_retry")
    if retry:
        return "interrupted" if retry["status"] == "running" else retry["status"]
    status = record.get("status")
    if status == "returned":
        return outcome(record["tool"], record.get("result", ""))
    return "interrupted" if status == "received" else status

def list_receipts():
    rows = []
    for path in archive_directory().glob("receipt-*.json"):
        try:
            _, record = read_receipt(path.stem[8:])
            status = state(record)
            if status == "succeeded" and not record.get("manual_retry"):
                continue
            retry = record.get("manual_retry", {})
            content = str(record["arguments"].get("content", ""))
            rows.append({"id": record["receipt_id"], "tool": record["tool"], "received_at": record["received_at"],
                         "status": status, "characters": len(content), "preview": content[:400],
                         "attempts": retry.get("attempts", 0),
                         "detail": str(retry.get("result") or retry.get("error_type") or record.get("result") or record.get("error_type") or "")[:2000]})
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return sorted(rows, key=lambda row: row["received_at"], reverse=True)

async def start_retry(receipt_id, dispatch=None):
    # No await before durable state and task registration: double clicks serialize.
    path, record = read_receipt(receipt_id)
    status = state(record)
    if status == "running":
        return {"status": "running"}
    if status == "succeeded":
        raise ValueError("此记录已经成功处理，无需重试")
    archive = ReceiptArchive(path.parent)
    attempt = {"status": "running", "started_at": time.time(),
               "attempts": record.get("manual_retry", {}).get("attempts", 0) + 1}
    record["manual_retry"] = attempt
    archive._write(path, record)

    async def execute():
        active_receipts.add(receipt_id)
        token = retry_parent.set(receipt_id)
        try:
            function = dispatch or importlib.import_module("tools." + record["tool"]).dispatch
            result = await function(**record["arguments"])
            attempt.update(status=outcome(record["tool"], result), result=str(result))
        except BaseException as error:
            attempt.update(status="interrupted" if isinstance(error, asyncio.CancelledError) else "failed", error_type=type(error).__name__)
            if not isinstance(error, (Exception, asyncio.CancelledError)):
                raise
        finally:
            retry_parent.reset(token)
            attempt["finished_at"] = time.time()
            try:
                archive._write(path, record)
            finally:
                active_receipts.discard(receipt_id)
                _tasks.pop(receipt_id, None)
    task = asyncio.create_task(execute(), name="manual-ingest-retry:" + receipt_id)
    task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    _tasks[receipt_id] = task
    return {"status": "running"}
