import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ombrebrain.storage.ingest_archive import ReceiptArchive, archive_ingest, active_receipts
from ombrebrain.storage import ingest_retry as retry

class RetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"OMBRE_INGEST_ARCHIVE_DIR": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.archive = ReceiptArchive(self.temp.name)

    def receipt(self, tool="grow", status="failed", **extra):
        receipt = self.archive.begin(tool, {"content": "synthetic test"})
        self.archive.finish(receipt, status, **extra)
        return receipt[1]["receipt_id"]

    async def test_retry_durable_and_double_click_joins(self):
        rid = self.receipt()
        event = asyncio.Event()
        calls = []
        @archive_ingest("grow")
        async def fake(content):
            calls.append(content)
            await event.wait()
            return "1条|新1合0 batch:test"
        await retry.start_retry(rid, fake)
        task = retry._tasks[rid]
        await retry.start_retry(rid, fake)
        await asyncio.sleep(0)
        self.assertEqual(calls, ["synthetic test"])
        self.assertEqual(retry.list_receipts()[0]["status"], "running")
        event.set()
        await task
        record = retry.read_receipt(rid)[1]
        self.assertEqual(record["status"], "failed")  # original outcome preserved
        self.assertEqual(record["manual_retry"]["status"], "succeeded")
        self.assertEqual(len(retry.list_receipts()), 1)  # child not offered again
        children = [json.loads(p.read_text(encoding="utf-8")) for p in Path(self.temp.name).glob("receipt-*.json")]
        self.assertEqual(sum(r.get("retry_of") == rid for r in children), 1)
        with self.assertRaises(ValueError):
            await retry.start_retry(rid, fake)

    async def test_failure_and_restart_remain_retryable(self):
        rid = self.receipt("hold")
        async def fail(**kwargs):
            raise RuntimeError("synthetic error")
        await retry.start_retry(rid, fail)
        await retry._tasks[rid]
        self.assertEqual(retry.list_receipts()[0]["status"], "failed")
        path, record = retry.read_receipt(rid)
        record["manual_retry"]["status"] = "running"
        self.archive._write(path, record)
        self.assertEqual(retry.list_receipts()[0]["status"], "interrupted")
        async def succeed(**kwargs):
            return "新建→test"
        await retry.start_retry(rid, succeed)
        await retry._tasks[rid]
        self.assertEqual(retry.read_receipt(rid)[1]["manual_retry"]["attempts"], 2)

    async def test_active_original_not_replayed(self):
        rid = self.receipt(status="received")
        active_receipts.add(rid)
        try:
            self.assertEqual(await retry.start_retry(rid), {"status": "running"})
            self.assertNotIn(rid, retry._tasks)
        finally:
            active_receipts.discard(rid)

    async def test_no_replay_of_checkpoint_or_traversal(self):
        rid = self.receipt(tool="digest_chunks")
        for invalid in (rid, "../../config", None):
            with self.assertRaises(ValueError):
                await retry.start_retry(invalid)

    async def test_partial_unknown_and_complete_classification(self):
        self.assertEqual(retry.outcome("grow", "2条|新1合0 batch:x"), "partial")
        self.assertEqual(retry.outcome("grow", "2条(预拆分·逐字)|新2合0 batch:x"), "succeeded")
        self.assertEqual(retry.outcome("grow", "后台处理中"), "uncertain")
        self.assertEqual(retry.outcome("grow", "0条|新0合0 batch:x"), "partial")
        self.receipt(status="returned", result="2条|新1合0 batch:x")
        self.receipt(status="returned", result="1条|新1合0 batch:x")
        self.assertEqual(len(retry.list_receipts()), 1)
        self.assertEqual(retry.list_receipts()[0]["status"], "partial")

class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_and_explicit_confirmation(self):
        from web import ingest_retry as routes
        from starlette.responses import JSONResponse
        from unittest.mock import AsyncMock
        handlers = {}
        class MCP:
            def custom_route(self, path, methods):
                def decorate(function):
                    handlers[methods[0]] = function
                    return function
                return decorate
        routes.register(MCP())
        request = AsyncMock()
        with patch.object(routes.sh, "_require_auth", return_value=JSONResponse({}, status_code=401)):
            for method in ("GET", "POST"):
                self.assertEqual((await handlers[method](request)).status_code, 401)
        with patch.object(routes.sh, "_require_auth", return_value=None):
            request.json.return_value = {"id": "a" * 32}
            with patch.object(routes, "start_retry", new_callable=AsyncMock) as start:
                self.assertEqual((await handlers["POST"](request)).status_code, 400)
                start.assert_not_called()
                request.json.return_value["confirm"] = True
                start.return_value = {"status": "running"}
                self.assertEqual((await handlers["POST"](request)).status_code, 202)
                start.assert_awaited_once_with("a" * 32)

if __name__ == "__main__":
    unittest.main()
