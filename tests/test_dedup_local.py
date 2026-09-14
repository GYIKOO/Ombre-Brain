import asyncio
import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from ombrebrain.storage.message_dedup import process_dialogue
from ombrebrain.storage.duplicate_review import candidates, review, undo, content_hash


class DedupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"OMBRE_INGEST_ARCHIVE_DIR": self.tmp.name + "/archive"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_overlaps_edits_and_reopen(self):
        calls = []

        async def operation(text, test_data=False):
            calls.append(json.loads(text)["messages"])
            return "1条|新1合0 batch:test"

        def send(messages):
            return asyncio.run(
                process_dialogue(json.dumps({"source": "private", "messages": messages}), False, operation)
            )

        a = {"role": "user", "timestamp": 1, "content": "hello"}
        b = {"role": "assistant", "timestamp": 2, "content": "hi"}
        c = {"role": "user", "timestamp": 3, "content": "new"}
        send([a, b])
        send([a, b])
        send([a, b, c])
        send([{**a, "content": "edited"}])
        send([{**a, "timestamp": 4}])
        self.assertEqual([len(batch) for batch in calls], [2, 1, 1, 1])

    def test_failure_and_partial_result_not_marked_done(self):
        message = {"role": "user", "timestamp": 1, "content": "x"}
        text = json.dumps({"source": "private", "messages": [message]})

        async def fail(text, test_data=False):
            raise RuntimeError("down")

        with self.assertRaises(RuntimeError):
            asyncio.run(process_dialogue(text, False, fail))
        calls = []

        async def partial(text, test_data=False):
            calls.append(1)
            return "2条|新1合0 batch:x"

        asyncio.run(process_dialogue(text, False, partial))
        asyncio.run(process_dialogue(text, False, partial))
        self.assertEqual(len(calls), 2)

    def test_partial_result_not_cached_by_grow_guard(self):
        from tools.grow.retry_guard import run_once
        calls = []
        async def operation():
            calls.append(1)
            return "2条|新1合0 batch:partial"
        async def go():
            await run_once("partial-test", operation)
            await run_once("partial-test", operation)
        asyncio.run(go())
        self.assertEqual(len(calls), 2)

    def test_no_timestamp_never_deduplicates(self):
        calls = []

        async def operation(text, test_data=False):
            calls.append(1)
            return "1条|新1合0 batch:x"

        text = json.dumps({"source": "private", "messages": [{"role": "user", "content": "yes"}]})
        asyncio.run(process_dialogue(text, False, operation))
        asyncio.run(process_dialogue(text, False, operation))
        self.assertEqual(len(calls), 2)

    def test_concurrent_overlap_serialized(self):
        calls = []

        async def operation(text, test_data=False):
            calls.append(1)
            await asyncio.sleep(0.01)
            return "1条|新1合0 batch:x"

        text = json.dumps({"source": "private", "messages": [{"role": "user", "content": "yes", "timestamp": 1}]})

        async def go():
            await asyncio.gather(process_dialogue(text, False, operation), process_dialogue(text, False, operation))

        asyncio.run(go())
        self.assertEqual(len(calls), 1)

    def test_candidates_and_reversible_review(self):
        class Manager:
            def __init__(self):
                self.b = {
                    str(i): {"id": str(i), "content": text, "metadata": {"type": "dynamic", "name": text}}
                    for i, text in enumerate(["first", "second"])
                }

            async def get(self, key):
                return copy.deepcopy(self.b.get(key))

            get_including_archive = get

            async def archive(self, key):
                self.b[key]["metadata"]["type"] = "archived"
                return True

            async def restore_archived(self, key):
                self.b[key]["metadata"]["type"] = "dynamic"
                return {"ok": True}

        class Engine:
            enabled = True

            async def get_embedding(self, key):
                return [1.0, 0.0]

            async def search_similar(self, *a, **k):
                raise AssertionError("No model/vector API query allowed")

        async def go():
            m = Manager()
            pairs = await candidates(list(m.b.values()), Engine())
            self.assertEqual(len(pairs), 1)
            a, b = m.b["0"], m.b["1"]
            rid = await review(m, "archive", "0", "1", content_hash(a), content_hash(b))
            self.assertEqual(m.b["1"]["metadata"]["type"], "archived")
            self.assertEqual(m.b["1"]["content"], "second")
            await undo(m, rid)
            self.assertEqual(m.b["1"]["metadata"]["type"], "dynamic")
            await review(m, "dismiss", "0", "1", content_hash(a), content_hash(b))
            self.assertEqual(await candidates(list(m.b.values()), Engine()), [])
            m.b["1"]["content"] = "edited"
            self.assertEqual(len(await candidates(list(m.b.values()), Engine())), 1)
            with self.assertRaises(ValueError):
                await review(m, "archive", "0", "1", content_hash(a), "stale")

        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
