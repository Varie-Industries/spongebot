"""
SpongeBot SQLite Store - Structured persistence for skills, sessions, and audit data.

Absorbed from AVA Memory Server's SQLite persistence pattern with SpongeBot-specific
tables for skill tracking, cost ledger, lockout management, and audit logging.

Tables
------
- skills       : absorbed skill definitions with confidence scoring
- sessions     : conversation session tracking with token accounting
- audit_log    : tamper-evident event logging with chained hashes
- cost_ledger  : per-API-call token and cost accounting
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SQLiteStore:
    """SQLite-backed structured storage for SpongeBot operational data.

    Provides CRUD operations for skills, sessions, audit logs, lockouts,
    and cost tracking. All tables are created on first use.

    Parameters
    ----------
    db_path : str | Path
        File path for the SQLite database. Parent directories are created
        automatically.
    """

    def __init__(self, db_path: str | Path = "data/spongebot.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        logger.info("SQLiteStore ready at %s", self._db_path)

    # ------------------------------------------------------------------
    # Connection helper
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Return a new connection with row_factory set to sqlite3.Row."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ------------------------------------------------------------------
    # Schema initialisation
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Create all tables if they do not exist."""
        conn = self._connect()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS skills (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    name            TEXT NOT NULL UNIQUE,
                    version         TEXT NOT NULL DEFAULT '1.0.0',
                    skill_json      TEXT NOT NULL,
                    confidence      REAL NOT NULL DEFAULT 0.0,
                    created_at      REAL NOT NULL,
                    updated_at      REAL NOT NULL,
                    absorbed_from   TEXT NOT NULL DEFAULT '',
                    absorption_mode TEXT NOT NULL DEFAULT 'unknown'
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      TEXT NOT NULL UNIQUE,
                    start_time      REAL NOT NULL,
                    end_time        REAL,
                    summary         TEXT DEFAULT '',
                    tokens_used     INTEGER DEFAULT 0,
                    tokens_saved    INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       REAL NOT NULL,
                    category        TEXT NOT NULL,
                    action          TEXT NOT NULL,
                    detail          TEXT DEFAULT '',
                    entry_hash      TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS lockout_list (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       REAL NOT NULL,
                    reason          TEXT NOT NULL,
                    trigger_layer   TEXT NOT NULL DEFAULT '',
                    permanent       INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS cost_ledger (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       REAL NOT NULL,
                    api             TEXT NOT NULL,
                    input_tokens    INTEGER NOT NULL DEFAULT 0,
                    output_tokens   INTEGER NOT NULL DEFAULT 0,
                    cost_usd        REAL NOT NULL DEFAULT 0.0
                );

                CREATE INDEX IF NOT EXISTS idx_skills_name
                    ON skills(name);

                CREATE INDEX IF NOT EXISTS idx_sessions_session_id
                    ON sessions(session_id);

                CREATE INDEX IF NOT EXISTS idx_audit_log_category
                    ON audit_log(category);

                CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp
                    ON audit_log(timestamp);

                CREATE INDEX IF NOT EXISTS idx_cost_ledger_timestamp
                    ON cost_ledger(timestamp);

                CREATE INDEX IF NOT EXISTS idx_cost_ledger_api
                    ON cost_ledger(api);
            """)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Row helper
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        """Convert a sqlite3.Row to a plain dict, or return None."""
        if row is None:
            return None
        return dict(row)

    @staticmethod
    def _rows_to_list(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        """Convert a list of sqlite3.Row objects to a list of dicts."""
        return [dict(r) for r in rows]

    # ==================================================================
    # SKILLS CRUD
    # ==================================================================

    def store_skill(
        self,
        name: str,
        version: str,
        skill_json: str,
        confidence: float,
        absorbed_from: str,
        mode: str,
    ) -> int:
        """Insert or replace a skill record.

        Parameters
        ----------
        name : str
            Unique skill name.
        version : str
            Semantic version string.
        skill_json : str
            JSON-serialised skill definition.
        confidence : float
            Confidence score (0.0 to 1.0).
        absorbed_from : str
            Source project or module the skill was absorbed from.
        mode : str
            Absorption mode (e.g. 'full', 'partial', 'reference').

        Returns
        -------
        int
            The row ID of the inserted or replaced skill.
        """
        now = time.time()
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO skills
                    (name, version, skill_json, confidence, created_at, updated_at,
                     absorbed_from, absorption_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    version = excluded.version,
                    skill_json = excluded.skill_json,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at,
                    absorbed_from = excluded.absorbed_from,
                    absorption_mode = excluded.absorption_mode
                """,
                (name, version, skill_json, confidence, now, now,
                 absorbed_from, mode),
            )
            conn.commit()
            return cursor.lastrowid or 0
        finally:
            conn.close()

    def get_skill(self, name: str) -> dict[str, Any] | None:
        """Retrieve a skill by name.

        Parameters
        ----------
        name : str
            Skill name to look up.

        Returns
        -------
        dict or None
            Skill record as a dict, or None if not found.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM skills WHERE name = ?", (name,)
            ).fetchone()
            return self._row_to_dict(row)
        finally:
            conn.close()

    def update_skill_confidence(self, name: str, confidence: float) -> None:
        """Update the confidence score for a skill.

        Parameters
        ----------
        name : str
            Skill name.
        confidence : float
            New confidence value (0.0 to 1.0).
        """
        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE skills SET confidence = ?, updated_at = ? WHERE name = ?",
                (confidence, now, name),
            )
            conn.commit()
        finally:
            conn.close()

    def list_skills(self, min_confidence: float = 0.0) -> list[dict[str, Any]]:
        """List skills filtered by minimum confidence.

        Parameters
        ----------
        min_confidence : float
            Minimum confidence threshold (default 0.0 = all skills).

        Returns
        -------
        list[dict]
            Skill records sorted by confidence descending.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM skills WHERE confidence >= ? ORDER BY confidence DESC",
                (min_confidence,),
            ).fetchall()
            return self._rows_to_list(rows)
        finally:
            conn.close()

    def delete_skill(self, name: str) -> None:
        """Delete a skill by name.

        Parameters
        ----------
        name : str
            Skill name to delete.
        """
        conn = self._connect()
        try:
            conn.execute("DELETE FROM skills WHERE name = ?", (name,))
            conn.commit()
        finally:
            conn.close()

    # ==================================================================
    # SESSIONS
    # ==================================================================

    def start_session(self, session_id: str) -> int:
        """Record the start of a new session.

        Parameters
        ----------
        session_id : str
            Unique session identifier.

        Returns
        -------
        int
            Row ID of the new session record.
        """
        now = time.time()
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO sessions (session_id, start_time)
                VALUES (?, ?)
                ON CONFLICT(session_id) DO UPDATE SET start_time = excluded.start_time
                """,
                (session_id, now),
            )
            conn.commit()
            return cursor.lastrowid or 0
        finally:
            conn.close()

    def end_session(
        self,
        session_id: str,
        summary: str,
        tokens_used: int,
        tokens_saved: int,
    ) -> None:
        """Record the end of a session with summary and token accounting.

        Parameters
        ----------
        session_id : str
            Session identifier (must already exist).
        summary : str
            Brief summary of the session.
        tokens_used : int
            Total tokens consumed during the session.
        tokens_saved : int
            Tokens saved through caching or compression.
        """
        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE sessions
                SET end_time = ?, summary = ?, tokens_used = ?, tokens_saved = ?
                WHERE session_id = ?
                """,
                (now, summary, tokens_used, tokens_saved, session_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_sessions(self, limit: int = 10) -> list[dict[str, Any]]:
        """Retrieve recent sessions ordered by start time descending.

        Parameters
        ----------
        limit : int
            Maximum number of sessions to return.

        Returns
        -------
        list[dict]
            Session records.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY start_time DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return self._rows_to_list(rows)
        finally:
            conn.close()

    # ==================================================================
    # AUDIT LOG
    # ==================================================================

    def log_audit(
        self,
        category: str,
        action: str,
        detail: str = "",
        entry_hash: str = "",
    ) -> int:
        """Append an entry to the audit log.

        If ``entry_hash`` is not provided, a SHA-256 hash is computed from
        the previous entry's hash chained with the current entry data,
        creating a tamper-evident log chain (absorbed from IT_NEXUS
        SecurityCore audit pattern).

        Parameters
        ----------
        category : str
            Event category (e.g. 'security', 'absorption', 'skill').
        action : str
            Action description (e.g. 'skill_absorbed', 'vault_initialized').
        detail : str
            Additional detail text.
        entry_hash : str
            Pre-computed hash, or empty to auto-compute.

        Returns
        -------
        int
            Row ID of the new audit entry.
        """
        now = time.time()

        if not entry_hash:
            # Build chain hash from previous entry
            conn = self._connect()
            try:
                prev_row = conn.execute(
                    "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
                ).fetchone()
                prev_hash = prev_row["entry_hash"] if prev_row else ""
            finally:
                conn.close()

            payload = f"{prev_hash}|{now}|{category}|{action}|{detail}"
            entry_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO audit_log (timestamp, category, action, detail, entry_hash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (now, category, action, detail, entry_hash),
            )
            conn.commit()
            return cursor.lastrowid or 0
        finally:
            conn.close()

    def get_audit_log(
        self,
        limit: int = 100,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve audit log entries, optionally filtered by category.

        Parameters
        ----------
        limit : int
            Maximum entries to return.
        category : str, optional
            If provided, filter to this category only.

        Returns
        -------
        list[dict]
            Audit entries ordered by timestamp descending.
        """
        conn = self._connect()
        try:
            if category:
                rows = conn.execute(
                    """
                    SELECT * FROM audit_log
                    WHERE category = ?
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return self._rows_to_list(rows)
        finally:
            conn.close()

    # ==================================================================
    # LOCKOUT
    # ==================================================================

    def store_lockout(
        self,
        reason: str,
        trigger_layer: str,
        permanent: bool = False,
    ) -> int:
        """Record a lockout event.

        Parameters
        ----------
        reason : str
            Human-readable reason for the lockout.
        trigger_layer : str
            Which subsystem triggered this (e.g. 'cost_guard', 'safety').
        permanent : bool
            Whether this lockout is permanent (default False).

        Returns
        -------
        int
            Row ID of the lockout record.
        """
        now = time.time()
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO lockout_list (timestamp, reason, trigger_layer, permanent)
                VALUES (?, ?, ?, ?)
                """,
                (now, reason, trigger_layer, int(permanent)),
            )
            conn.commit()
            return cursor.lastrowid or 0
        finally:
            conn.close()

    def is_locked_out(self) -> bool:
        """Check whether any active (permanent) lockout exists.

        Returns
        -------
        bool
            True if a permanent lockout is active.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM lockout_list WHERE permanent = 1"
            ).fetchone()
            return (row["cnt"] if row else 0) > 0
        finally:
            conn.close()

    def get_lockouts(self) -> list[dict[str, Any]]:
        """Retrieve all lockout records ordered by timestamp descending.

        Returns
        -------
        list[dict]
            All lockout entries.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM lockout_list ORDER BY timestamp DESC"
            ).fetchall()
            return self._rows_to_list(rows)
        finally:
            conn.close()

    # ==================================================================
    # COST LEDGER
    # ==================================================================

    def record_cost(
        self,
        api: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> int:
        """Record a single API call's token usage and cost.

        Parameters
        ----------
        api : str
            API identifier (e.g. 'anthropic', 'openai', 'elevenlabs').
        input_tokens : int
            Number of input tokens consumed.
        output_tokens : int
            Number of output tokens generated.
        cost_usd : float
            Estimated cost in USD.

        Returns
        -------
        int
            Row ID of the cost record.
        """
        now = time.time()
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO cost_ledger (timestamp, api, input_tokens, output_tokens, cost_usd)
                VALUES (?, ?, ?, ?, ?)
                """,
                (now, api, input_tokens, output_tokens, cost_usd),
            )
            conn.commit()
            return cursor.lastrowid or 0
        finally:
            conn.close()

    def get_cost_summary(self, hours: int = 24) -> dict[str, Any]:
        """Summarise costs over the last N hours.

        Parameters
        ----------
        hours : int
            Look-back window in hours (default 24).

        Returns
        -------
        dict
            Summary with keys: total_cost_usd, total_input_tokens,
            total_output_tokens, call_count, by_api (per-API breakdown).
        """
        cutoff = time.time() - (hours * 3600)
        conn = self._connect()
        try:
            # Overall totals
            totals_row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(cost_usd), 0.0) AS total_cost_usd,
                    COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
                    COUNT(*) AS call_count
                FROM cost_ledger
                WHERE timestamp >= ?
                """,
                (cutoff,),
            ).fetchone()

            # Per-API breakdown
            api_rows = conn.execute(
                """
                SELECT
                    api,
                    COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COUNT(*) AS calls
                FROM cost_ledger
                WHERE timestamp >= ?
                GROUP BY api
                ORDER BY cost_usd DESC
                """,
                (cutoff,),
            ).fetchall()

            by_api: dict[str, dict[str, Any]] = {}
            for row in api_rows:
                by_api[row["api"]] = {
                    "cost_usd": row["cost_usd"],
                    "input_tokens": row["input_tokens"],
                    "output_tokens": row["output_tokens"],
                    "calls": row["calls"],
                }

            return {
                "hours": hours,
                "total_cost_usd": totals_row["total_cost_usd"] if totals_row else 0.0,
                "total_input_tokens": totals_row["total_input_tokens"] if totals_row else 0,
                "total_output_tokens": totals_row["total_output_tokens"] if totals_row else 0,
                "call_count": totals_row["call_count"] if totals_row else 0,
                "by_api": by_api,
            }
        finally:
            conn.close()

    # ==================================================================
    # Utility
    # ==================================================================

    def close(self) -> None:
        """No-op for API compatibility (connections are per-operation)."""

    @property
    def db_path(self) -> Path:
        """Return the database file path."""
        return self._db_path
