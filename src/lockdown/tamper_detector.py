"""
Layer 6 -- Tamper Detector.

Maintains a SHA-256 chained audit trail.  Each entry's hash covers the
previous entry's hash, creating an append-only Merkle-like chain.  On
boot the chain is verified end-to-end; a broken link indicates
tampering.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("spongebot.lockdown.tamper_detector")

_VAULT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "vault"
_CHAIN_FILE = _VAULT_DIR / ".audit_chain.jsonl"

# Genesis block prev_hash
_GENESIS_PREV = "0" * 64


def _hash_entry(prev_hash: str, timestamp: float, event: str, data: Any) -> str:
    """Compute SHA-256 for a chain entry."""
    raw = json.dumps(
        {"prev": prev_hash, "ts": timestamp, "event": event, "data": data},
        sort_keys=True,
        default=str,
    ).encode()
    return hashlib.sha256(raw).hexdigest()


class TamperDetector:
    """Layer 6: SHA-256 chained audit trail."""

    LAYER_NAME = "tamper_detector"
    LAYER_INDEX = 6

    def __init__(self) -> None:
        self._chain: list[dict] = []
        self._last_hash: str = _GENESIS_PREV
        self._integrity_ok: bool = True
        self._break_index: Optional[int] = None

    # ----------------------------------------------------------
    # Chain management
    # ----------------------------------------------------------

    def _ensure_vault(self) -> None:
        _VAULT_DIR.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, data: Any = None) -> str:
        """Append an entry to the audit chain and persist it.

        Returns the hash of the new entry.
        """
        self._ensure_vault()
        ts = time.time()
        entry_hash = _hash_entry(self._last_hash, ts, event, data)

        entry = {
            "prev_hash": self._last_hash,
            "timestamp": ts,
            "event": event,
            "data": data,
            "hash": entry_hash,
        }
        self._chain.append(entry)
        self._last_hash = entry_hash

        # Append to JSONL file
        with open(_CHAIN_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

        logger.debug("Audit chain entry: %s -> %s", event, entry_hash[:16])
        return entry_hash

    def load_chain(self) -> None:
        """Load the persisted chain from disk."""
        self._chain = []
        self._last_hash = _GENESIS_PREV

        if not _CHAIN_FILE.exists():
            logger.info("No audit chain file -- starting fresh.")
            return

        with open(_CHAIN_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                self._chain.append(entry)
                self._last_hash = entry["hash"]

        logger.info("Loaded %d audit chain entries.", len(self._chain))

    def verify_chain(self) -> tuple[bool, str]:
        """Walk the full chain and verify every link.

        Returns
        -------
        tuple[bool, str]
            (intact, reason)
        """
        if not self._chain:
            self._integrity_ok = True
            return True, "Audit chain is empty -- nothing to verify."

        prev = _GENESIS_PREV
        for idx, entry in enumerate(self._chain):
            # Check prev_hash linkage
            if entry["prev_hash"] != prev:
                self._integrity_ok = False
                self._break_index = idx
                msg = (
                    f"Chain broken at index {idx}: expected prev_hash "
                    f"{prev[:16]}... but got {entry['prev_hash'][:16]}..."
                )
                logger.critical(msg)
                return False, msg

            # Recompute hash
            expected = _hash_entry(
                entry["prev_hash"],
                entry["timestamp"],
                entry["event"],
                entry["data"],
            )
            if entry["hash"] != expected:
                self._integrity_ok = False
                self._break_index = idx
                msg = (
                    f"Hash mismatch at index {idx}: stored "
                    f"{entry['hash'][:16]}... vs computed {expected[:16]}..."
                )
                logger.critical(msg)
                return False, msg

            prev = entry["hash"]

        self._integrity_ok = True
        self._break_index = None
        logger.info("Audit chain integrity verified (%d entries).", len(self._chain))
        return True, f"Audit chain intact ({len(self._chain)} entries)."

    def initialize(self) -> tuple[bool, str]:
        """Load and verify the chain -- called during boot."""
        self.load_chain()
        return self.verify_chain()

    # ----------------------------------------------------------
    # Status
    # ----------------------------------------------------------

    def status(self) -> dict:
        return {
            "layer": self.LAYER_INDEX,
            "name": self.LAYER_NAME,
            "chain_length": len(self._chain),
            "integrity_ok": self._integrity_ok,
            "break_index": self._break_index,
            "last_hash_prefix": self._last_hash[:16] + "..." if self._last_hash else None,
        }
