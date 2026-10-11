import pytest

from solomon_agent.knowledge import MarkdownKnowledgeProvider


def test_search_is_source_scoped_and_incremental(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    note = a / "intro.md"
    note.write_text(
        "# 智能体入门\n智能体工具调用与知识检索。Retrieval enables assistants.", encoding="utf-8"
    )
    (b / "private.md").write_text("# 私密\n智能体 Secret retrieval", encoding="utf-8")
    provider = MarkdownKnowledgeProvider({"public": a, "private": b}, tmp_path / "data")
    assert provider.index()["updated"] == 2
    assert provider.index()["updated"] == 0
    for query in ["工具调用", "retrieval", "智能体"]:
        hits = provider.search(query, source_ids=["public"])
        assert hits and all(hit.source_id == "public" for hit in hits)
        assert hits[0].title == "智能体入门"
        assert hits[0].locator == "intro.md"
    assert provider.search("retrieval", source_ids=[]) == []
    with pytest.raises(ValueError):
        provider.search("secret", source_ids=["unknown"])
    note.write_text("# 新标题\n完全替换为banana", encoding="utf-8")
    assert provider.index()["updated"] == 1
    assert not provider.search("retrieval", source_ids=["public"])
    note.unlink()
    assert provider.index()["removed"] == 1
    assert not provider.search("banana", source_ids=["public"])


def test_restart_and_source_path_migration(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Deployment\nPortable installation", encoding="utf-8")
    data = tmp_path / "data"
    MarkdownKnowledgeProvider({"kb": root}, data).index()
    moved = tmp_path / "moved"
    root.rename(moved)
    restarted = MarkdownKnowledgeProvider({"kb": moved}, data)
    assert restarted.index()["updated"] == 0
    assert restarted.search("portable")[0].source_id == "kb"
    assert list(moved.iterdir()) == [moved / "note.md"]


def test_escape_and_overlap_fail_without_index_mutation(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "secret.md"
    outside.write_text("secret")
    (root / "good.md").write_text("# Good\nusable", encoding="utf-8")
    provider = MarkdownKnowledgeProvider({"kb": root}, tmp_path / "data")
    provider.index()
    (root / "bad.md").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes"):
        provider.index()
    assert provider.search("usable")
    with pytest.raises(ValueError, match="overlap"):
        MarkdownKnowledgeProvider({"kb": root}, root / ".kb")
    with pytest.raises(ValueError, match="overlap"):
        MarkdownKnowledgeProvider({"kb": root}, tmp_path)


def test_index_failure_never_falls_back_to_vault(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    (data / "knowledge.sqlite3").mkdir()
    with pytest.raises(RuntimeError, match="Cannot write"):
        MarkdownKnowledgeProvider({"kb": root}, data)
    assert not (root / ".kb").exists()


def test_long_notes_have_bounded_evidence(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "long.md").write_text("# Search\n" + "知识检索 " * 1000, encoding="utf-8")
    provider = MarkdownKnowledgeProvider({"kb": root}, tmp_path / "data")
    provider.index()
    hits = provider.search("知识检索", limit=20)
    assert len(hits) <= 3
    assert all(len(hit.text) <= 700 for hit in hits)


def test_unreadable_source_keeps_previous_index(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    note = root / "note.md"
    note.write_text("# Stable\nexisting evidence", encoding="utf-8")
    provider = MarkdownKnowledgeProvider({"kb": root}, tmp_path / "data")
    provider.index()
    note.chmod(0)
    try:
        with pytest.raises(PermissionError):
            provider.index()
        assert provider.search("existing")
    finally:
        note.chmod(0o600)


def test_unwritable_data_directory_and_escaping_directory_link(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    data.chmod(0o500)
    try:
        with pytest.raises(RuntimeError, match="Cannot write"):
            MarkdownKnowledgeProvider({"kb": root}, data)
        assert not list(root.iterdir())
    finally:
        data.chmod(0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    provider = MarkdownKnowledgeProvider({"kb": root}, data)
    with pytest.raises(ValueError, match="escapes"):
        provider.index()


def test_unreadable_subdirectory_never_deletes_old_index(tmp_path):
    root = tmp_path / "vault"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "note.md").write_text("# Stable\nretained evidence", encoding="utf-8")
    provider = MarkdownKnowledgeProvider({"kb": root}, tmp_path / "data")
    provider.index()
    nested.chmod(0)
    try:
        with pytest.raises(PermissionError):
            provider.index()
        assert provider.search("retained")
    finally:
        nested.chmod(0o700)
    assert provider.index()["removed"] == 0


def test_hidden_metadata_trash_and_hidden_symlink_targets_are_excluded(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    for name in [".git", ".kb", ".trash", ".obsidian", "visible/.private"]:
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "note.md").write_text("# Private\nhiddenmarker", encoding="utf-8")
    (root / ".hidden.md").write_text("hiddenmarker", encoding="utf-8")
    (root / "kept.md").write_text("# Public\npublicmarker", encoding="utf-8")
    (root / "alias.md").symlink_to(root / ".trash" / "note.md")
    provider = MarkdownKnowledgeProvider({"kb": root}, tmp_path / "data")
    assert provider.index()["documents"] == 1
    assert not provider.search("hiddenmarker")
    assert provider.search("publicmarker")


def test_existing_index_entries_moved_to_trash_are_removed(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    note = root / "note.md"
    note.write_text("# Visible\narchivedmarker", encoding="utf-8")
    provider = MarkdownKnowledgeProvider({"kb": root}, tmp_path / "data")
    provider.index()
    trash = root / ".trash"
    trash.mkdir()
    note.rename(trash / "note.md")
    assert provider.index() == {"updated": 0, "removed": 1, "documents": 0}
    assert not provider.search("archivedmarker")


def test_source_inside_hidden_parent_still_indexes_visible_notes(tmp_path):
    root = tmp_path / ".private-home" / "vault"
    root.mkdir(parents=True)
    (root / "visible.md").write_text("# Visible\nnormalmarker", encoding="utf-8")
    provider = MarkdownKnowledgeProvider({"kb": root}, tmp_path / "data")
    assert provider.index()["documents"] == 1
    assert provider.search("normalmarker")
