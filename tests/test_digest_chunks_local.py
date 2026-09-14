import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from ombrebrain.storage.digest_chunks import digest_all, split_content, map_ranges


class ChunkTests(unittest.TestCase):
    def test_lossless_and_bounded(self):
        for text in [
            "字" * 13000,
            "一行\r\n" * 1500 + "最后的暗号",
            '{\n  "messages": [\n' + ('    {"content": "你好"}\n    },\n' * 400) + "]}",
            "a\n" + "b" * 6000,
        ]:
            parts = list(split_content(text))
            self.assertEqual("".join(p for _, p in parts), text)
            self.assertTrue(all(0 < len(p) <= 4096 for _, p in parts))

    def test_ranges_after_hard_split_and_newlines(self):
        text = "a" * 5000 + "\nb\nc"
        parts = list(split_content(text))
        for start, part in parts:
            mapped = map_ranges([{"source_ranges": [[1, 1], [999, 1000]]}], part, start, text)
            self.assertEqual(mapped[0]["source_ranges"], [[text[:start].count("\n") + 1] * 2])

    def test_retry_reuses_completed_segment_and_reaches_tail(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"OMBRE_INGEST_ARCHIVE_DIR": ""}):
            os.environ["OMBRE_INGEST_ARCHIVE_DIR"] = root
            calls = []
            failed = False

            async def model(text):
                nonlocal failed
                calls.append(text)
                if len(calls) == 2 and not failed:
                    failed = True
                    raise RuntimeError("temporary error")
                return [{"content": text[-12:], "source_ranges": [[1, 1]]}], ""

            text = "a" * 4096 + "b" * 4096 + "TAIL-SENTINEL"
            with self.assertRaises(RuntimeError):
                asyncio.run(digest_all(text, {"model": "test"}, model))
            items = asyncio.run(digest_all(text, {"model": "test"}, model))
            self.assertEqual(len(calls), 4)
            self.assertIn("AIL-SENTINEL", items[-1]["content"])
            asyncio.run(digest_all(text, {"model": "test"}, model))
            self.assertEqual(len(calls), 4)
            asyncio.run(digest_all(text, {"model": "changed"}, model))
            self.assertEqual(len(calls), 7)


if __name__ == "__main__":
    unittest.main()
