"""
SpongeBot Tamper-Evident Audit Chain

SHA-256 chained audit log absorbed from IT_NEXUS AuditEntry pattern.
Each entry hashes prev_hash to form a tamper-evident chain. Any
modification to a past entry breaks the chain and is detectable.

Tracks: lockdown checks, skill modifications, absorptions,
federated exchanges, security events, and system operations.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

# Valid audit categories
AuditCategory = Literal[
    "lockdown",
    "skill",
    "absorption",
    "federated",
    "security",
    "system",
]

_VALID_CATEGORIES: frozenset[str] = frozenset(
    ["lockdown", "skill", "absorption", "federated", "security", "system"]
)

_GENESIS_HASH = "GENESIS"


@dataclass
class AuditEntry:
    """Single entry in the tamper-evident audit chain.

    Attributes
    ----------
    sequence : int
        Monotonically increasing entry number (0-based).
    timestamp : float
        Unix timestamp when the entry was created.
    category : str
        One of: lockdown, skill, absorption, federated, security, system.
    action : str
        Short verb or event name (e.g., "vault_initialized", "skill_added").
    detail : str
        Human-readable description of the event.
    prev_hash : str
        SHA-256 hash of the preceding entry, or "GENESIS" for the first.
    entry_hash : str
        SHA-256 hash computed over all fields except entry_hash itself.
    """

    sequence: int
    timestamp: float
    category: str
    action: str
    detail: str
    prev_hash: str
    entry_hash: str = ""

    def compute_hash(self) -> str:
        """Compute SHA-256 hash over all fields except ``entry_hash``.

        The hash covers sequence, timestamp, category, action, detail,
        and prev_hash in a deterministic pipe-separated format.

        Returns
        -------
        str
            Hex-encoded SHA-256 digest.
        """
        payload = (
            f"{self.sequence}|{self.timestamp}|{self.category}|"
            f"{self.action}|{self.detail}|{self.prev_hash}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        """Serialise the entry to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> AuditEntry:
        """Deserialise an entry from a dictionary.

        Parameters
        ----------
        data : dict
            Dictionary with all AuditEntry fields.

        Returns
        -------
        AuditEntry
            Reconstructed entry.
        """
        return cls(
            sequence=data["sequence"],
            timestamp=data["timestamp"],
            category=data["category"],
            action=data["action"],
            detail=data["detail"],
            prev_hash=data["prev_hash"],
            entry_hash=data.get("entry_hash", ""),
        )


class AuditChain:
    """Tamper-evident audit chain with SHA-256 hash linking.

    Each appended entry includes the hash of the previous entry,
    forming a chain. Any modification to a historical entry will
    cause ``verify_chain`` to fail.

    The chain is persisted to a JSON file in the data directory
    and reloaded on construction.

    Parameters
    ----------
    data_dir : str | Path
        Directory for the audit log JSON file.
    auto_persist : bool
        If ``True`` (default), the chain is written to disk after
        every append. Set to ``False`` for batch operations, then
        call ``persist`` manually.
    """

    _AUDIT_FILE = "audit_chain.json"

    def __init__(
        self,
        data_dir: str | Path,
        auto_persist: bool = True,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._auto_persist = auto_persist
        self._lock = threading.Lock()
        self._chain: list[AuditEntry] = []

        # Load existing chain from disk
        self._load()

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def append(
        self,
        category: str,
        action: str,
        detail: str,
    ) -> AuditEntry:
        """Append a new entry to the audit chain.

        Parameters
        ----------
        category : str
            Event category. Must be one of: lockdown, skill, absorption,
            federated, security, system.
        action : str
            Short verb or event name.
        detail : str
            Human-readable event description.

        Returns
        -------
        AuditEntry
            The newly created and hashed entry.

        Raises
        ------
        ValueError
            If the category is not one of the valid categories.
        """
        if category not in _VALID_CATEGORIES:
            raise ValueError(
                f"Invalid category '{category}'. "
                f"Must be one of: {', '.join(sorted(_VALID_CATEGORIES))}"
            )

        with self._lock:
            prev_hash = (
                self._chain[-1].entry_hash if self._chain else _GENESIS_HASH
            )
            entry = AuditEntry(
                sequence=len(self._chain),
                timestamp=time.time(),
                category=category,
                action=action,
                detail=detail,
                prev_hash=prev_hash,
            )
            entry.entry_hash = entry.compute_hash()
            self._chain.append(entry)

            if self._auto_persist:
                self._persist_unlocked()

        return entry

    def verify_chain(self) -> tuple[bool, str]:
        """Verify the integrity of the entire audit hash chain.

        Checks that every entry's hash is correctly computed and that
        each entry's ``prev_hash`` matches the preceding entry's hash.

        Returns
        -------
        tuple[bool, str]
            ``(True, "ok")`` if the chain is intact, or
            ``(False, description)`` with details of the first tamper detected.
        """
        with self._lock:
            for i, entry in enumerate(self._chain):
                # Verify the entry's own hash
                computed = entry.compute_hash()
                if entry.entry_hash != computed:
                    return (
                        False,
                        f"Entry {i}: hash mismatch "
                        f"(stored={entry.entry_hash[:16]}..., "
                        f"computed={computed[:16]}...)",
                    )

                # Verify chain linkage
                if i == 0:
                    if entry.prev_hash != _GENESIS_HASH:
                        return (
                            False,
                            f"Entry 0: prev_hash is '{entry.prev_hash}', "
                            f"expected '{_GENESIS_HASH}'",
                        )
                else:
                    expected_prev = self._chain[i - 1].entry_hash
                    if entry.prev_hash != expected_prev:
                        return (
                            False,
                            f"Entry {i}: prev_hash mismatch "
                            f"(stored={entry.prev_hash[:16]}..., "
                            f"expected={expected_prev[:16]}...)",
                        )

            return (True, "ok")

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_log(
        self,
        category: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Return audit entries as dictionaries, optionally filtered.

        Parameters
        ----------
        category : str, optional
            Filter to entries of a specific category.
        limit : int
            Maximum number of entries to return (most recent first).
        offset : int
            Number of entries to skip from the end before applying limit.

        Returns
        -------
        list[dict]
            Serialised audit entries, most recent first.
        """
        with self._lock:
            entries = self._chain
            if category is not None:
                entries = [e for e in entries if e.category == category]

            # Slice from the end: skip `offset` from the tail, then take `limit`
            if offset > 0:
                entries = entries[: -offset] if offset < len(entries) else []
            selected = entries[-limit:] if limit < len(entries) else entries

            return [e.to_dict() for e in reversed(selected)]

    @property
    def length(self) -> int:
        """Return the number of entries in the chain."""
        with self._lock:
            return len(self._chain)

    @property
    def last_hash(self) -> str:
        """Return the hash of the most recent entry, or GENESIS if empty."""
        with self._lock:
            if self._chain:
                return self._chain[-1].entry_hash
            return _GENESIS_HASH

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def persist(self) -> None:
        """Write the current chain to disk (thread-safe)."""
        with self._lock:
            self._persist_unlocked()

    def _persist_unlocked(self) -> None:
        """Write the chain to disk (caller must hold lock)."""
        audit_path = self._data_dir / self._AUDIT_FILE
        data = [e.to_dict() for e in self._chain]
        audit_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _load(self) -> None:
        """Load the chain from disk if the file exists."""
        audit_path = self._data_dir / self._AUDIT_FILE
        if not audit_path.exists():
            return
        try:
            raw = audit_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            self._chain = [AuditEntry.from_dict(entry) for entry in data]
        except (json.JSONDecodeError, KeyError, TypeError):
            # Corrupted file: start fresh but do not crash
            self._chain = []

    # ------------------------------------------------------------------
    # Import / Export
    # ------------------------------------------------------------------

    def export_json(self) -> str:
        """Export the entire chain as a JSON string.

        Returns
        -------
        str
            JSON-serialised audit chain.
        """
        with self._lock:
            data = [e.to_dict() for e in self._chain]
            return json.dumps(data, indent=2, ensure_ascii=False)

    def import_json(self, json_str: str, verify: bool = True) -> int:
        """Import entries from a JSON string, appending to the current chain.

        If the imported chain starts with GENESIS, it replaces the current
        chain entirely. Otherwise, entries are appended if they continue
        the current chain (prev_hash must match).

        Parameters
        ----------
        json_str : str
            JSON-serialised audit entries.
        verify : bool
            If ``True``, verify imported entries' hashes before accepting.

        Returns
        -------
        int
            Number of entries imported.

        Raises
        ------
        ValueError
            If verification fails or the chain cannot be continued.
        """
        data = json.loads(json_str)
        entries = [AuditEntry.from_dict(d) for d in data]

        if not entries:
            return 0

        if verify:
            for i, entry in enumerate(entries):
                computed = entry.compute_hash()
                if entry.entry_hash != computed:
                    raise ValueError(
                        f"Import entry {i}: hash verification failed"
                    )
                if i == 0:
                    if entry.prev_hash != _GENESIS_HASH:
                        # Must continue from current chain
                        pass
                else:
                    if entry.prev_hash != entries[i - 1].entry_hash:
                        raise ValueError(
                            f"Import entry {i}: chain linkage broken"
                        )

        with self._lock:
            if entries[0].prev_hash == _GENESIS_HASH:
                # Full chain replacement
                self._chain = entries
            else:
                # Continuation: verify it connects
                if self._chain:
                    if entries[0].prev_hash != self._chain[-1].entry_hash:
                        raise ValueError(
                            "Imported chain does not continue from current chain"
                        )
                self._chain.extend(entries)

            if self._auto_persist:
                self._persist_unlocked()

        return len(entries)
