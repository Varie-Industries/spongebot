"""
Layer 7 -- Self-Destruct Trigger.

Tracks vault-decrypt failures.  After 3 consecutive failed decrypts
the vault is wiped irreversibly.  This wraps whatever self-destruct
logic exists in ``security/self_destruct.py``.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("spongebot.lockdown.self_destruct_trigger")

_VAULT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "vault"
_FAIL_COUNTER_FILE = _VAULT_DIR / ".decrypt_failures"
_MAX_FAILURES = 3


class SelfDestructTrigger:
    """Layer 7: 3 failed vault decrypts = vault wipe."""

    LAYER_NAME = "self_destruct_trigger"
    LAYER_INDEX = 7

    def __init__(
        self,
        on_destruct: Optional[Callable[[], None]] = None,
    ) -> None:
        self.on_destruct = on_destruct
        self._failure_count: int = 0
        self._destructed: bool = False
        self._load_counter()

    # ----------------------------------------------------------
    # Counter persistence
    # ----------------------------------------------------------

    def _ensure_vault(self) -> None:
        _VAULT_DIR.mkdir(parents=True, exist_ok=True)

    def _load_counter(self) -> None:
        """Load the persisted failure counter."""
        if _FAIL_COUNTER_FILE.exists():
            try:
                data = json.loads(_FAIL_COUNTER_FILE.read_text())
                self._failure_count = int(data.get("failures", 0))
            except Exception:
                self._failure_count = 0
        else:
            self._failure_count = 0

    def _save_counter(self) -> None:
        """Persist the failure counter."""
        self._ensure_vault()
        _FAIL_COUNTER_FILE.write_text(
            json.dumps({"failures": self._failure_count})
        )

    # ----------------------------------------------------------
    # Decrypt tracking
    # ----------------------------------------------------------

    def record_decrypt_success(self) -> None:
        """Reset failure counter on successful decrypt."""
        if self._failure_count > 0:
            logger.info("Vault decrypt succeeded -- resetting failure counter.")
        self._failure_count = 0
        self._save_counter()

    def record_decrypt_failure(self) -> tuple[bool, str]:
        """Record a failed vault decrypt attempt.

        Returns
        -------
        tuple[bool, str]
            (safe, reason) -- safe is False if self-destruct triggered.
        """
        self._failure_count += 1
        self._save_counter()

        logger.warning(
            "Vault decrypt failure %d/%d.",
            self._failure_count,
            _MAX_FAILURES,
        )

        if self._failure_count >= _MAX_FAILURES:
            return self._trigger_destruct()

        remaining = _MAX_FAILURES - self._failure_count
        return True, (
            f"Decrypt failure recorded ({self._failure_count}/{_MAX_FAILURES}). "
            f"{remaining} attempt(s) remaining before vault wipe."
        )

    # ----------------------------------------------------------
    # Self-destruct
    # ----------------------------------------------------------

    def _trigger_destruct(self) -> tuple[bool, str]:
        """Irreversibly wipe the vault directory."""
        self._destructed = True
        msg = (
            f"SELF-DESTRUCT TRIGGERED: {_MAX_FAILURES} consecutive "
            "vault decrypt failures. Wiping vault."
        )
        logger.critical(msg)

        # Attempt to call external self_destruct module if available
        try:
            from ..security import self_destruct  # type: ignore[import-untyped]

            self_destruct.execute()
        except (ImportError, AttributeError):
            logger.debug("No security.self_destruct module -- using built-in wipe.")

        # Built-in wipe: remove entire vault directory
        try:
            if _VAULT_DIR.exists():
                shutil.rmtree(_VAULT_DIR)
                logger.critical("Vault directory wiped: %s", _VAULT_DIR)
        except Exception as exc:
            logger.error("Vault wipe failed: %s", exc)

        # Notify callback
        if self.on_destruct:
            try:
                self.on_destruct()
            except Exception:
                pass

        return False, msg

    # ----------------------------------------------------------
    # Status
    # ----------------------------------------------------------

    def status(self) -> dict:
        return {
            "layer": self.LAYER_INDEX,
            "name": self.LAYER_NAME,
            "failure_count": self._failure_count,
            "max_failures": _MAX_FAILURES,
            "destructed": self._destructed,
        }
