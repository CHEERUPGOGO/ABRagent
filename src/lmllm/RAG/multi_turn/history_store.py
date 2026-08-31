"""SQLite 对话历史存储 — 高比能锂电池材料筛选 RAG 会话管理

提供:
- 会话(session)创建/列出/删除
- 消息逐条落库(user/assistant + evidence)
- 重启后按 session_id 回溯历史对话
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import PROJECT_ROOT

# ── 数据库路径 ──
DB_DIR = PROJECT_ROOT / "src" / "lmllm" / "RAG" / "multi_turn" / "data"
DB_PATH = DB_DIR / "chat_history.db"

class HistoryStore:
    """对话历史 SQLite 持久化存储."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._is_memory = isinstance(self.db_path, str) and self.db_path == ":memory:"
        if isinstance(self.db_path, Path):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # 持久连接解决 :memory: 数据库的表丢失问题
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_db()

    def _column_exists(self, table: str, column: str) -> bool:
        """检查 SQLite 表中是否存在某列."""
        cursor = self._conn.execute(f"PRAGMA table_info({table})")
        return any(row[1] == column for row in cursor.fetchall())

    def _init_db(self) -> None:
        """建表(幂等) + schema 迁移."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                evidence_json TEXT DEFAULT '[]',
                timestamp TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)
        # ── schema 迁移: 新增 evidence_display / process_log 列 ──
        if not self._column_exists("messages", "evidence_display"):
            self._conn.execute(
                "ALTER TABLE messages ADD COLUMN evidence_display TEXT DEFAULT ''"
            )
        if not self._column_exists("messages", "process_log"):
            self._conn.execute(
                "ALTER TABLE messages ADD COLUMN process_log TEXT DEFAULT ''"
            )
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_session
            ON messages(session_id, id)
        """)
        self._conn.commit()

    # ── 会话管理 ──

    def create_session(self, title: str = "新会话") -> str:
        """创建新会话,返回 session_id(格式: session_YYYYMMDD_HHMMSS)."""
        import uuid
        session_id = f"session_{uuid.uuid4().hex[:12]}"
        now = datetime.now().isoformat()
        self._conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, title, now, now),
        )
        self._conn.commit()
        return session_id

    def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """列出最近会话,带消息数."""
        rows = self._conn.execute("""
            SELECT s.id, s.title, s.created_at, s.updated_at,
                   COUNT(m.id) AS message_count
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            GROUP BY s.id
            ORDER BY s.updated_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取单条会话信息."""
        row = self._conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_session_title(self, session_id: str, title: str) -> None:
        """更新会话标题."""
        now = datetime.now().isoformat()
        self._conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, session_id),
        )
        self._conn.commit()

    def delete_session(self, session_id: str) -> None:
        """删除会话及所有消息."""
        self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self._conn.commit()

    # ── 消息管理 ──

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        evidence: Optional[List[Dict[str, Any]]] = None,
        evidence_display: str = "",
        process_log: str = "",
    ) -> int:
        """添加一条消息,返回 message_id.

        Args:
            evidence_display: 格式化后的检索证据 Markdown（切换会话时恢复）
            process_log: 格式化后的过程日志 Markdown（切换会话时恢复）
        """
        now = datetime.now().isoformat()
        evidence_json = json.dumps(evidence or [], ensure_ascii=False)
        cur = self._conn.execute(
            "INSERT INTO messages (session_id, role, content, evidence_json, timestamp, evidence_display, process_log) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, role, content, evidence_json, now, evidence_display, process_log),
        )
        self._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """获取会话的全部消息(按时间升序)."""
        rows = self._conn.execute("""
            SELECT id, role, content, evidence_json,
                   evidence_display, process_log, timestamp
            FROM messages
            WHERE session_id = ?
            ORDER BY id ASC
        """, (session_id,)).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["evidence"] = json.loads(d.pop("evidence_json", "[]"))
            except Exception:
                d["evidence"] = []
            results.append(d)
        return results

    def get_message_count(self, session_id: str) -> int:
        """获取会话的消息数."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row[0] if row else 0

    def get_latest_session_id(self) -> Optional[str]:
        """获取最近更新的会话 id."""
        row = self._conn.execute(
            "SELECT id FROM sessions ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None
