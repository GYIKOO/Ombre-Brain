
import pytest
import utils


def linked_vault(tmp_path):
    vault = tmp_path / "runtime"
    actual = tmp_path / "Obsidian"
    vault.mkdir()
    actual.mkdir()
    for name in ("dynamic", "archive"):
        (actual / name).mkdir()
    for name in ("dynamic", "archive", "permanent", "feel", "plans", "letters", "_media"):
        (vault / name).symlink_to(actual / name, target_is_directory=True)
    return vault, actual


def test_startup_restores_missing_linked_subdirectories(tmp_path, monkeypatch):
    vault, actual = linked_vault(tmp_path)
    memory = actual / "dynamic" / "memory.md"
    memory.write_text("unchanged", encoding="utf-8")
    monkeypatch.delenv("OMBRE_BUCKETS_DIR", raising=False)
    monkeypatch.setenv("OMBRE_VAULT_DIR", str(vault))
    monkeypatch.delenv("OMBRE_MEDIA_DIR", raising=False)
    for _ in range(2):
        utils.load_config(str(tmp_path / "absent.yaml"))
        for name in ("permanent", "feel", "plans", "letters", "_media"):
            assert (actual / name).is_dir()
            assert (vault / name).is_symlink()
        assert memory.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.parametrize("missing", ["dynamic", "archive", "root"])
def test_inaccessible_primary_storage_is_not_recreated(tmp_path, missing):
    vault, actual = linked_vault(tmp_path)
    if missing == "root":
        (actual / "dynamic").rmdir()
        (actual / "archive").rmdir()
        actual.rmdir()
    else:
        (actual / missing).rmdir()
    with pytest.raises(RuntimeError, match="记忆库目录不可访问"):
        utils._repair_linked_vault_directories(str(vault), str(vault / "_media"))
    assert not (actual / "permanent").exists()
    assert not (actual if missing == "root" else actual / missing).exists()


def test_file_collision_is_preserved(tmp_path):
    vault, actual = linked_vault(tmp_path)
    (actual / "permanent").write_text("keep", encoding="utf-8")
    with pytest.raises(RuntimeError, match="不是文件夹"):
        utils._repair_linked_vault_directories(str(vault), str(vault / "_media"))
    assert (actual / "permanent").read_text(encoding="utf-8") == "keep"


def test_normal_new_vault_still_initializes(tmp_path, monkeypatch):
    vault = tmp_path / "new-vault"
    monkeypatch.delenv("OMBRE_BUCKETS_DIR", raising=False)
    monkeypatch.setenv("OMBRE_VAULT_DIR", str(vault))
    monkeypatch.delenv("OMBRE_MEDIA_DIR", raising=False)
    utils.load_config(str(tmp_path / "absent.yaml"))
    assert (vault / "dynamic").is_dir()
    assert (vault / "_media").is_dir()
