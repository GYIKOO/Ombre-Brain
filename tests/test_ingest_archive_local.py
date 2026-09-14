import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ombrebrain.storage.ingest_archive import ReceiptArchive, archive_ingest, RETENTION_SECONDS


class ArchiveTests(unittest.TestCase):
    def test_preserves_full_input_on_model_failure_and_after_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"OMBRE_INGEST_ARCHIVE_DIR": directory}):
                text = "原文🌙\n" * 2000

                @archive_ingest("grow")
                async def fail(content):
                    records = list(Path(directory).glob("receipt-*.json"))
                    self.assertEqual(json.loads(records[0].read_text(encoding="utf-8"))["arguments"]["content"], text)
                    raise RuntimeError("model unavailable")

                with self.assertRaises(RuntimeError):
                    asyncio.run(fail(text))
            record = json.loads(next(Path(directory).glob("receipt-*.json")).read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "failed")
            self.assertEqual(record["arguments"]["content"], text)

    def test_disk_failure_prevents_processing(self):
        with patch.object(ReceiptArchive, "begin", side_effect=OSError("disk full")):
            called = []

            @archive_ingest("hold")
            async def hold(content):
                called.append(content)

            with self.assertRaises(OSError):
                asyncio.run(hold("test"))
            self.assertEqual(called, [])

    def test_retention_only_removes_expired_owned_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReceiptArchive(directory)
            with patch("ombrebrain.storage.ingest_archive.time.time", return_value=100):
                old, _ = store.begin("hold", {"content": "old"})
            with patch("ombrebrain.storage.ingest_archive.time.time", return_value=200):
                fresh, _ = store.begin("hold", {"content": "fresh"})
            unrelated = Path(directory) / "notes.txt"
            unrelated.write_text("keep")
            store.prune(RETENTION_SECONDS + 150)
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(unrelated.exists())

    def test_return_value_and_distinct_receipts_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"OMBRE_INGEST_ARCHIVE_DIR": directory}):

                @archive_ingest("hold")
                async def hold(content, test_data=False):
                    return "partial result"

                for _ in range(2):
                    self.assertEqual(asyncio.run(hold("same", test_data=True)), "partial result")
            records = list(Path(directory).glob("receipt-*.json"))
            self.assertEqual(len(records), 2)
            self.assertTrue(all(json.loads(p.read_text())["status"] == "returned" for p in records))


if __name__ == "__main__":
    unittest.main()
