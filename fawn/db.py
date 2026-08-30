import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .model import Item


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    user_comment TEXT NOT NULL,
    summary TEXT NOT NULL,
    transcript TEXT NOT NULL,
    tags TEXT NOT NULL,
    categories TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    id UNINDEXED,
    title,
    author,
    user_comment,
    summary,
    transcript,
    tags,
    categories,
    tokenize='unicode61 remove_diacritics 2'
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def upsert(connection: sqlite3.Connection, item: Item, path: Path, root: Path) -> None:
    relative = path.relative_to(root).as_posix()
    values = (
        item.id,
        relative,
        item.url,
        item.platform,
        item.title,
        item.author,
        item.user_comment,
        item.summary,
        item.transcript,
        json.dumps(item.tags, ensure_ascii=False),
        json.dumps(item.categories, ensure_ascii=False),
        item.status,
        item.created_at,
        item.updated_at,
    )
    connection.execute(
        """
        INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          path=excluded.path, url=excluded.url, platform=excluded.platform,
          title=excluded.title, author=excluded.author,
          user_comment=excluded.user_comment, summary=excluded.summary,
          transcript=excluded.transcript, tags=excluded.tags,
          categories=excluded.categories, status=excluded.status,
          created_at=excluded.created_at, updated_at=excluded.updated_at
        """,
        values,
    )
    connection.execute("DELETE FROM items_fts WHERE id = ?", (item.id,))
    connection.execute(
        "INSERT INTO items_fts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            item.id,
            item.title,
            item.author,
            item.user_comment,
            item.summary,
            item.transcript,
            " ".join(item.tags),
            " ".join(item.categories),
        ),
    )
    connection.commit()


def find_by_url(connection: sqlite3.Connection, url: str):
    return connection.execute("SELECT * FROM items WHERE url = ?", (url,)).fetchone()


def find_by_id(connection: sqlite3.Connection, item_id: str):
    return connection.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def by_status(connection: sqlite3.Connection, status: str, limit: int):
    return connection.execute(
        "SELECT * FROM items WHERE status = ? ORDER BY created_at LIMIT ?",
        (status, limit),
    ).fetchall()


def search(connection: sqlite3.Connection, query: str, limit: int = 20):
    words = re.findall(r"[^\W_]+", query.casefold(), flags=re.UNICODE)
    if not words:
        return []
    expression = " AND ".join(f'"{word.replace(chr(34), chr(34) * 2)}"*' for word in words)
    return connection.execute(
        """
        SELECT i.*, bm25(items_fts) AS rank
        FROM items_fts JOIN items i ON i.id = items_fts.id
        WHERE items_fts MATCH ?
        ORDER BY rank, i.created_at DESC
        LIMIT ?
        """,
        (expression, limit),
    ).fetchall()


def recent(connection: sqlite3.Connection, limit: int = 20):
    return connection.execute(
        "SELECT * FROM items ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()


def count(connection: sqlite3.Connection) -> int:
    return int(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0])


def clear(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM items")
    connection.execute("DELETE FROM items_fts")
    connection.commit()
