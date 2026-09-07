"""
History item data model and SQLite database persistence.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from app.config import HISTORY_DB


@dataclass
class HistoryItem:
    id: str
    title: str
    url: str
    media_type: str
    format_ext: str
    file_path: str
    file_size_bytes: int
    duration_sec: float
    completed_at: str
    channel: str = ""
    version_label: str = ""


class HistoryManager:
    """Manages history storage in SQLite."""

    def __init__(self, db_path=HISTORY_DB):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    url TEXT,
                    media_type TEXT,
                    format_ext TEXT,
                    file_path TEXT,
                    file_size_bytes INTEGER,
                    duration_sec REAL,
                    completed_at TEXT,
                    channel TEXT,
                    version_label TEXT
                )
                """
            )
            conn.commit()

    def add_item(self, item: HistoryItem):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO history
                (id, title, url, media_type, format_ext, file_path, file_size_bytes, duration_sec, completed_at, channel, version_label)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.title,
                    item.url,
                    item.media_type,
                    item.format_ext,
                    item.file_path,
                    item.file_size_bytes,
                    item.duration_sec,
                    item.completed_at,
                    item.channel,
                    item.version_label,
                ),
            )
            conn.commit()

    def get_all(self, limit: int = 200) -> List[HistoryItem]:
        items = []
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, title, url, media_type, format_ext, file_path, file_size_bytes, duration_sec, completed_at, channel, version_label FROM history ORDER BY completed_at DESC LIMIT ?",
                (limit,),
            )
            for row in cursor.fetchall():
                items.append(
                    HistoryItem(
                        id=row[0],
                        title=row[1],
                        url=row[2],
                        media_type=row[3],
                        format_ext=row[4],
                        file_path=row[5],
                        file_size_bytes=row[6],
                        duration_sec=row[7],
                        completed_at=row[8],
                        channel=row[9] or "",
                        version_label=row[10] or "",
                    )
                )
        return items

    def clear(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM history")
            conn.commit()
