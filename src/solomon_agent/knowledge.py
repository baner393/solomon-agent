"""Read-only Markdown sources with an independent incremental search index."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

import jieba


@dataclass(frozen=True)
class Evidence:
    source_id: str
    title: str
    text: str
    locator: str


class KnowledgeProvider(Protocol):
    def search(
        self, query: str, limit: int = 3, source_ids: Sequence[str] | None = None
    ) -> list[Evidence]: ...


def _tokens(text: str, tokenizer: jieba.Tokenizer) -> str:
    # Chinese bigrams supplement jieba for unseen proper names and partial queries.
    terms = re.findall(r"[a-z0-9_]+", text.lower())
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.extend(tokenizer.lcut(run))
        terms.extend(run[i : i + 2] for i in range(len(run) - 1))
    return " ".join(dict.fromkeys(t for t in terms if t.strip()))


class MarkdownKnowledgeProvider:
    def __init__(self, sources: Mapping[str, Path], data_dir: Path):
        self.sources = {
            key: Path(value).expanduser().resolve(strict=True) for key, value in sources.items()
        }
        if not self.sources or any(not key for key in self.sources):
            raise ValueError("At least one named knowledge source is required")
        for path in self.sources.values():
            if not path.is_dir():
                raise ValueError(f"Knowledge source is not a directory: {path}")
        self.data_dir = Path(data_dir).expanduser().resolve()
        for source in self.sources.values():
            if self.data_dir.is_relative_to(source) or source.is_relative_to(self.data_dir):
                raise ValueError("Knowledge sources and data directory must not overlap")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tokenizer = jieba.Tokenizer()
        self.tokenizer.tmp_dir = str(self.data_dir)
        self.tokenizer.cache_file = "jieba.cache"
        self.db_path = self.data_dir / "knowledge.sqlite3"
        if self.db_path.is_symlink():
            raise ValueError("Knowledge index must not be a symbolic link")
        try:
            with self._connect() as db:
                if db.execute("PRAGMA user_version").fetchone()[0] not in {0, 1}:
                    raise ValueError("Unsupported knowledge index version")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS documents (source_id TEXT, locator TEXT, "
                    "digest TEXT, title TEXT, text TEXT, PRIMARY KEY(source_id, locator))"
                )
                db.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(source_id "
                    "UNINDEXED, locator UNINDEXED, title UNINDEXED, text UNINDEXED, tokens)"
                )
                db.execute("PRAGMA user_version = 1")
        except sqlite3.Error as exc:
            raise RuntimeError(f"Cannot write knowledge index in {self.data_dir}") from exc

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def index(self) -> dict[str, int]:
        changed = 0
        seen: set[tuple[str, str]] = set()
        # Read everything before mutation: a failing source leaves the old index intact.
        entries = []

        def scan_error(error):
            raise error

        for source_id, root in self.sources.items():
            for directory, subdirs, filenames in os.walk(root, onerror=scan_error):
                folder = Path(directory)
                for name in subdirs + filenames:
                    candidate = folder / name
                    if candidate.is_symlink() and not candidate.resolve(strict=True).is_relative_to(
                        root
                    ):
                        raise ValueError(f"Symbolic link escapes configured source: {candidate}")
                for name in sorted(filenames):
                    if not name.endswith(".md"):
                        continue
                    path = folder / name
                    resolved = path.resolve(strict=True)
                    if not resolved.is_relative_to(root):
                        raise ValueError(f"Knowledge file escapes configured source: {path}")
                    if not resolved.is_file():
                        continue
                    locator = path.relative_to(root).as_posix()
                    raw = resolved.read_bytes()
                    body = raw.decode("utf-8")
                    title_match = re.search(r"^# +(.+)$", body, re.MULTILINE)
                    title = title_match.group(1).strip() if title_match else path.stem
                    digest = hashlib.sha256(raw).hexdigest()
                    entries.append((source_id, locator, digest, title, body))
                    seen.add((source_id, locator))
        with self._connect() as db:
            existing = {
                (s, p): digest
                for s, p, digest in db.execute("SELECT source_id, locator, digest FROM documents")
            }
            for source_id, locator, digest, title, body in entries:
                if existing.get((source_id, locator)) == digest:
                    continue
                db.execute(
                    "INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?)",
                    (source_id, locator, digest, title, body),
                )
                db.execute(
                    "DELETE FROM search_index WHERE source_id=? AND locator=?", (source_id, locator)
                )
                # Short independent chunks bound how much personal material reaches the model.
                for offset in range(0, len(body), 700):
                    chunk = body[offset : offset + 700]
                    db.execute(
                        "INSERT INTO search_index VALUES (?,?,?,?,?)",
                        (
                            source_id,
                            locator,
                            title,
                            chunk,
                            _tokens(title + " " + chunk, self.tokenizer),
                        ),
                    )
                changed += 1
            removed = set(existing) - seen
            for source_id, locator in removed:
                db.execute(
                    "DELETE FROM documents WHERE source_id=? AND locator=?", (source_id, locator)
                )
                db.execute(
                    "DELETE FROM search_index WHERE source_id=? AND locator=?", (source_id, locator)
                )
        return {"updated": changed, "removed": len(removed), "documents": len(seen)}

    def search(
        self, query: str, limit: int = 3, source_ids: Sequence[str] | None = None
    ) -> list[Evidence]:
        ids = list(self.sources if source_ids is None else source_ids)
        if any(key not in self.sources for key in ids):
            raise ValueError("Unknown knowledge source")
        if not ids or limit <= 0:
            return []
        tokens = _tokens(query, self.tokenizer).split()
        if not tokens:
            return []
        match = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT source_id,title,text,locator FROM search_index WHERE "
                f"search_index MATCH ? AND source_id IN ({placeholders}) ORDER BY "
                f"bm25(search_index), source_id, locator LIMIT ?",
                [match, *ids, min(limit, 3)],
            ).fetchall()
        return [Evidence(*row) for row in rows]
