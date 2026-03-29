"""
SpongeBot Memory - SQLite-only with keyword-based recall.

Simplified from ChromaDB+SQLite hybrid to pure SQLite.
Semantic search replaced with keyword overlap + importance scoring.
Zero external dependencies beyond stdlib + sqlite3.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Any

from src.memory.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


class HybridMemory:
    """SQLite-backed memory with keyword-based recall.

    Drop-in replacement for the old ChromaDB+SQLite hybrid.
    All the same methods, none of the ChromaDB dependency.

    Parameters
    ----------
    vault
        A ``VaultCore`` instance for encryption (used for future bridge ops).
    data_dir : str | Path
        Root data directory. SQLite database goes in ``data_dir/spongebot.db``.
    """

    def __init__(
        self,
        vault: Any = None,
        data_dir: str | Path = "data",
    ) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._vault = vault

        self._sqlite = SQLiteStore(
            db_path=self._data_dir / "spongebot.db",
        )

        # In-memory text store for keyword recall
        self._text_entries: list[dict[str, Any]] = []
        self._booted = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def boot(self) -> None:
        if self._booted:
            return
        logger.info("Memory booted (SQLite-only mode)")
        self._booted = True

    async def shutdown(self) -> None:
        self._sqlite.close()
        self._booted = False
        logger.info("Memory shut down.")

    # ------------------------------------------------------------------
    # Store & Recall
    # ------------------------------------------------------------------

    async def store(
        self,
        text: str,
        collection: str = "experiences",
        metadata: dict[str, Any] | None = None,
        importance: float = 1.0,
    ) -> str:
        doc_id = hashlib.sha256(
            f"{collection}:{text}:{time.time_ns()}".encode()
        ).hexdigest()[:24]

        entry = {
            "id": f"{collection}_{doc_id}",
            "text": text,
            "collection": collection,
            "metadata": metadata or {},
            "importance": importance,
            "stored_at": time.time(),
        }
        self._text_entries.append(entry)

        if len(self._text_entries) > 5000:
            self._text_entries = self._text_entries[-5000:]

        return entry["id"]

    async def recall(
        self,
        query: str,
        k: int = 5,
        collection: str | None = None,
    ) -> list[dict[str, Any]]:
        query_words = set(query.lower().split())
        if not query_words:
            return []

        scored: list[tuple[float, dict[str, Any]]] = []

        for entry in self._text_entries:
            if collection and entry["collection"] != collection:
                continue

            entry_words = set(entry["text"].lower().split())
            overlap = len(query_words & entry_words)
            if overlap == 0:
                continue

            ratio = overlap / len(query_words)
            importance = entry.get("importance", 1.0)

            decay = 1.0
            if entry["collection"] == "experiences":
                age = time.time() - entry.get("stored_at", time.time())
                if age > 0:
                    decay = 0.5 ** (age / (7 * 24 * 3600))

            score = ratio * importance * decay
            scored.append((score, {
                "text": entry["text"],
                "collection": entry["collection"],
                "score": score,
                "metadata": entry["metadata"],
            }))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:k]]

    async def recall_structured(self, table: str, **filters: Any) -> list[dict[str, Any]]:
        if table == "skills":
            return self._sqlite.list_skills(min_confidence=filters.get("min_confidence", 0.0))
        if table == "sessions":
            return self._sqlite.get_sessions(limit=filters.get("limit", 10))
        if table == "audit_log":
            return self._sqlite.get_audit_log(limit=filters.get("limit", 100), category=filters.get("category"))
        if table == "lockout_list":
            return self._sqlite.get_lockouts()
        if table == "cost_ledger":
            return [self._sqlite.get_cost_summary(hours=filters.get("hours", 24))]
        raise ValueError(f"Unknown table '{table}'")

    async def count(self) -> dict[str, Any]:
        by_collection: dict[str, int] = {}
        for entry in self._text_entries:
            coll = entry["collection"]
            by_collection[coll] = by_collection.get(coll, 0) + 1
        return {"text_entries": by_collection, "skills": len(self._sqlite.list_skills())}

    async def purge(self, collection: str) -> int:
        before = len(self._text_entries)
        self._text_entries = [e for e in self._text_entries if e["collection"] != collection]
        removed = before - len(self._text_entries)
        logger.info("Purged %d entries from '%s'", removed, collection)
        return removed

    # ------------------------------------------------------------------
    # Delegated SQLite operations
    # ------------------------------------------------------------------

    def store_skill(self, name, version, skill_json, confidence, absorbed_from, mode):
        return self._sqlite.store_skill(name, version, skill_json, confidence, absorbed_from, mode)

    def get_skill(self, name): return self._sqlite.get_skill(name)
    def update_skill_confidence(self, name, confidence): self._sqlite.update_skill_confidence(name, confidence)
    def list_skills(self, min_confidence=0.0): return self._sqlite.list_skills(min_confidence)
    def delete_skill(self, name): self._sqlite.delete_skill(name)
    def start_session(self, session_id): return self._sqlite.start_session(session_id)
    def end_session(self, session_id, summary, tokens_used, tokens_saved): self._sqlite.end_session(session_id, summary, tokens_used, tokens_saved)
    def get_sessions(self, limit=10): return self._sqlite.get_sessions(limit)
    def log_audit(self, category, action, detail="", entry_hash=""): return self._sqlite.log_audit(category, action, detail, entry_hash)
    def get_audit_log(self, limit=100, category=None): return self._sqlite.get_audit_log(limit, category)
    def store_lockout(self, reason, trigger_layer, permanent=False): return self._sqlite.store_lockout(reason, trigger_layer, permanent)
    def is_locked_out(self): return self._sqlite.is_locked_out()
    def get_lockouts(self): return self._sqlite.get_lockouts()
    def record_cost(self, api, input_tokens, output_tokens, cost_usd): return self._sqlite.record_cost(api, input_tokens, output_tokens, cost_usd)
    def get_cost_summary(self, hours=24): return self._sqlite.get_cost_summary(hours)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        sqlite_ok = True
        try:
            self._sqlite.get_sessions(limit=1)
        except Exception:
            sqlite_ok = False

        locked_out = False
        try:
            locked_out = self._sqlite.is_locked_out()
        except Exception:
            pass

        overall = "healthy" if sqlite_ok else "degraded"
        if locked_out:
            overall = "locked_out"

        return {
            "status": overall,
            "booted": self._booted,
            "mode": "sqlite_only",
            "text_entries": len(self._text_entries),
            "sqlite": {"ok": sqlite_ok, "db_path": str(self._sqlite.db_path)},
            "locked_out": locked_out,
        }

    @property
    def sqlite(self) -> SQLiteStore:
        return self._sqlite
