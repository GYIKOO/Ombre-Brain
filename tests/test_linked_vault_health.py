import sqlite3
import pytest
from ombrebrain.storage.vault_health import inspect_vault


def test_linked_memory_directory(tmp_path):
    root=tmp_path/"runtime"
    shared=tmp_path/"shared"
    root.mkdir();shared.mkdir()
    (shared/"one.md").write_text("---\nid: one\ntype: dynamic\n---\nhello",encoding="utf8")
    try:
        (root/"dynamic").symlink_to(shared,target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks unavailable")
    db=root/"embeddings.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE embeddings(bucket_id TEXT)")
        c.execute("INSERT INTO embeddings VALUES ('one')")
    report=inspect_vault(str(root),str(db))
    assert report["markdown"]["active_ids"]==1
    assert report["sqlite"]["orphan_count"]==0
    outside=tmp_path/"outside.md"
    outside.write_text("---\nid: other\n---\nno",encoding="utf8")
    (shared/"escape.md").symlink_to(outside)
    assert inspect_vault(str(root),str(db))["markdown"]["unsafe_path_count"]==1
