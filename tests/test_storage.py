import sqlite3
import time

import pytest

from solomon_agent.storage import SessionStore


def test_persistent_history_isolated_and_clear(tmp_path):
    store = SessionStore(tmp_path)
    store.start_request("r1", "alice", "draft", "question")
    assert store.finish_request("r1", "answer")
    assert not store.finish_request("r1", "duplicate")
    store.start_request("r2", "bob", "draft", "private")
    store.finish_request("r2", "secret")
    restored = SessionStore(tmp_path)
    assert restored.recent("alice", "draft") == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]
    assert restored.recent("alice", "voice") == []
    restored.clear("alice")
    assert restored.recent("alice", "draft") == []
    assert restored.recent("bob", "draft")[-1]["content"] == "secret"


def test_cancel_error_and_restart_reject_late_output(tmp_path):
    store = SessionStore(tmp_path)
    for rid, status in [("cancel", "cancelled"), ("error", "error")]:
        store.start_request(rid, "s", "voice", "hello")
        assert store.finish_request(rid, None, status)
        assert not store.finish_request(rid, "late")
    store.start_request("restart", "s", "voice", "pending")
    restored = SessionStore(tmp_path)
    assert not restored.finish_request("restart", "late")
    assert restored.recent("s", "voice") == []
    restored.start_request("cleared", "s", "voice", "pending")
    restored.clear("s")
    assert not restored.finish_request("cleared", "late")


def test_retention_and_invalid_requests(tmp_path):
    store = SessionStore(tmp_path)
    for mode in ["voice", "draft"]:
        store.start_request(mode, "s", mode, "old")
        store.finish_request(mode, "answer")
    with sqlite3.connect(store.db_path) as db:
        db.execute("UPDATE requests SET created_at=?", (time.time() - 31 * 86400,))
    store.prune()
    assert store.recent("s", "voice") == []
    assert store.recent("s", "draft")
    with pytest.raises(ValueError):
        store.start_request("invalid", "s", "unknown", "text")
    with pytest.raises(ValueError):
        store.finish_request("draft", status="running")
    with pytest.raises(sqlite3.IntegrityError):
        store.start_request("draft", "s", "draft", "duplicate")
    SessionStore(tmp_path, draft_retention_days=1)
    assert not store.recent("s", "draft")


def test_archive_preserves_records_until_retention_and_blocks_reuse(tmp_path):
    store = SessionStore(tmp_path)
    store.start_request("record", "closed", "voice", "remembered")
    store.finish_request("record", "retained")
    store.clear("closed")
    assert store.recent("closed", "voice") == []
    assert store.is_session_archived("closed")
    assert store.has_request("record")
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT user_text,assistant_text FROM requests").fetchone() == (
            "remembered",
            "retained",
        )
    with pytest.raises(ValueError, match="archived"):
        store.start_request("reuse", "closed", "voice", "new text")
    restored = SessionStore(tmp_path)
    assert restored.is_session_archived("closed")
    assert restored.has_request("record")
    restored.start_request("new", "new-session", "voice", "fresh")
    restored.finish_request("new", "answer")
    assert restored.recent("new-session", "voice")
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            "UPDATE requests SET created_at=? WHERE request_id='record'",
            (time.time() - 31 * 86400,),
        )
    restored.prune()
    assert not restored.has_request("record")
    assert restored.is_session_archived("closed")


def test_recent_never_starts_with_orphan_assistant(tmp_path):
    store = SessionStore(tmp_path)
    for number in range(3):
        store.start_request(str(number), "s", "draft", f"question {number}")
        store.finish_request(str(number), f"answer {number}")
    assert store.recent("s", "draft", limit=3) == [
        {"role": "user", "content": "question 2"},
        {"role": "assistant", "content": "answer 2"},
    ]
    assert store.recent("s", "draft", limit=1) == []
