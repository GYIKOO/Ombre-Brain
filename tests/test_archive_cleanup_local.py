from pathlib import Path
import frontmatter
import pytest


@pytest.mark.asyncio
async def test_archive_cleanup_deletes_file_and_vector(bucket_mgr, fake_embedding_engine):
    bid = "cleanup-test-123"
    path = Path(bucket_mgr.archive_dir) / (bid + ".md")
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter.dump(frontmatter.Post("synthetic archive", id=bid, type="archived"), str(path))
    fake_embedding_engine._store[bid] = [1.0]
    result = await bucket_mgr.hard_delete_archived_bucket(bid)
    assert result["ok"]
    assert not path.exists()
    assert await fake_embedding_engine.get_embedding(bid) is None
    assert (await bucket_mgr.hard_delete_archived_bucket(bid))["error"] == "not_found"


@pytest.mark.asyncio
async def test_archive_cleanup_refuses_active_and_protected(bucket_mgr):
    for bid, kind, protected in [("active-test", "dynamic", False), ("protected-test", "archived", True)]:
        path = Path(bucket_mgr.archive_dir) / (bid + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        frontmatter.dump(frontmatter.Post("keep me", id=bid, type=kind, protected=protected), str(path))
        assert not (await bucket_mgr.hard_delete_archived_bucket(bid))["ok"]
        assert path.exists()
