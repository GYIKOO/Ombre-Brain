"""Single-instance dialogue dedup: commit identities only after full success."""

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import weakref

from .ingest_archive import archive_directory

_locks = weakref.WeakKeyDictionary()
log = logging.getLogger(__name__)


def identity(message, source, test_data):
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        return None
    if not message.get("role"):
        return None
    anchor = message.get("id") or message.get("messageId") or message.get("timestamp")
    if anchor is None or anchor == "":
        return None  # No reliable identity: never guess that repeated text is old.
    payload = [source, bool(test_data), message["role"], anchor, message["content"]]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def fully_stored(result):
    match = re.match(r"^(\d+)条\|新(\d+)合(\d+) batch:", str(result))
    return bool(match and int(match[1]) == int(match[2]) + int(match[3]) and int(match[1]) > 0)


async def process_dialogue(content, test_data, operation):
    try:
        envelope = json.loads(content)
    except (ValueError, TypeError):
        return await operation(content, test_data=test_data)
    if (
        not isinstance(envelope, dict)
        or not isinstance(envelope.get("messages"), list)
        or not envelope["messages"]
        or "source" not in envelope
    ):
        return await operation(content, test_data=test_data)
    loop = asyncio.get_running_loop()
    lock = _locks.setdefault(loop, asyncio.Lock())
    async with lock:
        path = archive_directory().parent / "message-ingest.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS processed (fingerprint TEXT PRIMARY KEY, processed_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
            source = envelope["source"]
            selected, keys, in_batch = [], [], set()
            for message in envelope["messages"]:
                key = identity(message, source, test_data)
                if key and (
                    key in in_batch
                    or connection.execute("SELECT 1 FROM processed WHERE fingerprint=?", (key,)).fetchone()
                ):
                    continue
                selected.append(message)
                if key:
                    keys.append(key)
                    in_batch.add(key)
            skipped = len(envelope["messages"]) - len(selected)
            if not selected:
                log.info("dialogue dedup: received=%s skipped=%s new=0", len(envelope["messages"]), skipped)
                return f"已处理过的 {skipped} 条消息，跳过重复整理；未调用模型。"
            filtered = {**envelope, "messages": selected, "roundsCount": len(selected) / 2}
            result = await operation(json.dumps(filtered, ensure_ascii=False, indent=2), test_data=test_data)
            complete = fully_stored(result)
            if complete:
                connection.executemany(
                    "INSERT OR IGNORE INTO processed(fingerprint) VALUES (?)", [(key,) for key in keys]
                )
                connection.commit()
            log.info(
                "dialogue dedup: received=%s skipped=%s new=%s committed=%s",
                len(envelope["messages"]),
                skipped,
                len(selected),
                complete,
            )
            return result
        finally:
            connection.close()
