"""
Layer 8 -- Lockout Manager.

Once a permanent lockout is triggered it is stored encrypted in the
vault and can NEVER be recovered through software.  The system refuses
all operations once locked out.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("spongebot.lockdown.lockout_manager")

_VAULT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "vault"
_LOCKOUT_FILE = _VAULT_DIR / ".permanent_lockout"
_LOCKOUT_SALT = b"spongebot-lockout-v1-permanent"


def _xor_mask(data: bytes) -> bytes:
    """Simple XOR mask for lockout file."""
    key = hashlib.sha256(_LOCKOUT_SALT).digest()
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


class LockoutManager:
    """Layer 8: permanent ban -- once locked, never recoverable via software."""

    LAYER_NAME = "lockout_manager"
    LAYER_INDEX = 8

    def __init__(self) -> None:
        self._locked_out: bool = False
        self._lockout_record: Optional[dict] = None
        self._load_lockout()

    # ----------------------------------------------------------
    # Persistence
    # ----------------------------------------------------------

    def _ensure_vault(self) -> None:
        _VAULT_DIR.mkdir(parents=True, exist_ok=True)

    def _load_lockout(self) -> None:
        """Check if a lockout record exists on disk."""
        if not _LOCKOUT_FILE.exists():
            return

        try:
            raw = _xor_mask(_LOCKOUT_FILE.read_bytes())
            record = json.loads(raw)
            if record.get("locked_out") is True:
                self._locked_out = True
                self._lockout_record = record
                logger.critical(
                    "PERMANENT LOCKOUT LOADED -- reason: %s",
                    record.get("reason", "unknown"),
                )
        except Exception as exc:
            # If the file exists but is unreadable, treat as locked out
            # (defensive: assume worst case)
            logger.critical(
                "Lockout file exists but unreadable (%s) -- assuming locked out.",
                exc,
            )
            self._locked_out = True
            self._lockout_record = {
                "locked_out": True,
                "reason": "Lockout file corrupted -- assuming hostile tampering.",
                "timestamp": time.time(),
                "trigger_layer": "lockout_manager",
            }

    # ----------------------------------------------------------
    # Public API
    # ----------------------------------------------------------

    def check_lockout(self) -> bool:
        """Return True if the system is permanently locked out."""
        return self._locked_out

    def get_lockout_record(self) -> Optional[dict]:
        """Return the lockout record, or None if not locked out."""
        return self._lockout_record

    def lockout(
        self,
        reason: str,
        trigger_layer: str,
        details: Optional[dict] = None,
    ) -> None:
        """Trigger a PERMANENT lockout.  This is irreversible via software.

        Parameters
        ----------
        reason:
            Human-readable explanation of why the lockout was triggered.
        trigger_layer:
            The layer name that initiated the lockout.
        details:
            Optional extra context about the violation.
        """
        if self._locked_out:
            logger.warning("Lockout already active -- ignoring duplicate trigger.")
            return

        record = {
            "locked_out": True,
            "reason": reason,
            "timestamp": time.time(),
            "trigger_layer": trigger_layer,
            "details": details or {},
        }

        self._ensure_vault()
        payload = json.dumps(record).encode()
        _LOCKOUT_FILE.write_bytes(_xor_mask(payload))

        # Set file read-only as an extra barrier
        try:
            _LOCKOUT_FILE.chmod(0o444)
        except OSError:
            pass

        self._locked_out = True
        self._lockout_record = record

        logger.critical(
            "PERMANENT LOCKOUT ENGAGED -- reason: %s | trigger: %s",
            reason,
            trigger_layer,
        )

    # ----------------------------------------------------------
    # Status
    # ----------------------------------------------------------

    def status(self) -> dict:
        return {
            "layer": self.LAYER_INDEX,
            "name": self.LAYER_NAME,
            "locked_out": self._locked_out,
            "lockout_reason": (
                self._lockout_record.get("reason") if self._lockout_record else None
            ),
            "lockout_timestamp": (
                self._lockout_record.get("timestamp") if self._lockout_record else None
            ),
            "trigger_layer": (
                self._lockout_record.get("trigger_layer")
                if self._lockout_record
                else None
            ),
        }
