"""Local 30-day receipt journal, written before memory processing.

This is a recovery archive, not an automatic retry queue. Only JSON arguments
to hold/grow are recorded; transport credentials are never included.
"""

import asyncio
import functools
import inspect
import json
import logging
import os
import re
import tempfile
import time
import uuid
from contextlib import suppress
from pathlib import Path

RETENTION_SECONDS = 30 * 86400
_NAME = re.compile(r"receipt-[0-9a-f]{32}\.json\Z")
_log = logging.getLogger(__name__)


def archive_directory():
    override = os.environ.get("OMBRE_INGEST_ARCHIVE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    config = os.environ.get("OMBRE_CONFIG_PATH")
    if config:
        return Path(config).expanduser().resolve().parent / "ingest-archive"
    return Path(os.environ.get("OMBRE_VAULT_DIR", "buckets")).expanduser().resolve() / "ingest-archive"


class ReceiptArchive:
    def __init__(self, root):
        self.root = Path(root)

    def _write(self, path, record):
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".receipt-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
            if os.name != "nt":
                directory_fd = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def begin(self, tool, arguments):
        record = {
            "archive_version": 1,
            "receipt_id": uuid.uuid4().hex,
            "received_at": time.time(),
            "tool": tool,
            "status": "received",
            "arguments": arguments,
        }
        path = self.root / ("receipt-" + record["receipt_id"] + ".json")
        self._write(path, record)
        try:
            self.prune(record["received_at"])
        except OSError:
            _log.warning("Receipt retention cleanup failed; received input is saved")
        return path, record

    def finish(self, receipt, status, **details):
        path, record = receipt
        record = {**record, "status": status, "finished_at": time.time(), **details}
        self._write(path, record)

    def prune(self, now=None):
        cutoff = (time.time() if now is None else now) - RETENTION_SECONDS
        if not self.root.exists():
            return
        for path in self.root.iterdir():
            if not _NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                received = record.get("received_at")
                valid = (
                    record.get("archive_version") == 1
                    and path.name == f"receipt-{record.get('receipt_id')}.json"
                    and isinstance(received, (int, float))
                )
                if valid and received < cutoff:
                    path.unlink()
            except (ValueError, TypeError):
                continue  # Never delete an unrecognized or damaged file.


def archive_ingest(tool):
    def decorate(function):
        signature = inspect.signature(function)

        @functools.wraps(function)
        async def wrapped(*args, **kwargs):
            arguments = signature.bind(*args, **kwargs)
            arguments.apply_defaults()
            archive = ReceiptArchive(archive_directory())
            # Synchronous durable write deliberately completes before any await
            # or API call. If this fails, no downstream memory work is attempted.
            receipt = archive.begin(tool, dict(arguments.arguments))
            try:
                result = await function(*args, **kwargs)
            except BaseException as error:
                status = "interrupted" if isinstance(error, asyncio.CancelledError) else "failed"
                try:
                    archive.finish(receipt, status, error_type=type(error).__name__)
                except OSError:
                    _log.exception("Receipt status update failed; original receipt remains")
                raise
            try:
                # A returned string can contain partial failures or in-progress
                # notices. Do not falsely label it as successfully stored.
                archive.finish(receipt, "returned", result=result)
            except (OSError, TypeError, ValueError):
                _log.exception("Receipt result update failed; original receipt remains")
            return result

        return wrapped

    return decorate


class ArchiveRetention:
    """Hourly cleanup while the HTTP service runs; startup catches downtime."""

    def __init__(self):
        self.task = None

    async def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._run(), name="ingest-archive-retention")

    async def _run(self):
        while True:
            try:
                ReceiptArchive(archive_directory()).prune()
            except OSError:
                _log.exception("Receipt archive cleanup failed")
            await asyncio.sleep(3600)

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
