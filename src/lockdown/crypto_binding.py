"""
Layer 5 -- Cryptographic Binding.

Every internal subsystem message is signed with HMAC-SHA256 so that
tampering between modules is detectable.  The signature covers a
timestamp, the source module name, and a SHA-256 hash of the payload.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("spongebot.lockdown.crypto_binding")

# The signing key is derived from an env var or falls back to a
# per-installation random key persisted in the vault.
_VAULT_DIR = (
    __import__("pathlib").Path(__file__).resolve().parent.parent.parent
    / "data"
    / "vault"
)
_KEY_FILE = _VAULT_DIR / ".hmac_key"

# Maximum allowed clock skew for signature validation (seconds)
MAX_AGE_SECONDS = 300  # 5 minutes


def _load_or_create_key() -> bytes:
    """Return the 32-byte HMAC signing key, creating one if missing."""
    env_key = os.environ.get("SPONGEBOT_HMAC_KEY")
    if env_key:
        return hashlib.sha256(env_key.encode()).digest()

    _VAULT_DIR.mkdir(parents=True, exist_ok=True)
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes()

    key = os.urandom(32)
    _KEY_FILE.write_bytes(key)
    logger.info("Generated new HMAC signing key.")
    return key


_SIGNING_KEY: bytes | None = None


def _get_key() -> bytes:
    global _SIGNING_KEY
    if _SIGNING_KEY is None:
        _SIGNING_KEY = _load_or_create_key()
    return _SIGNING_KEY


# ------------------------------------------------------------------
# Signed message dataclass
# ------------------------------------------------------------------


@dataclass(frozen=True)
class SignedMessage:
    """An HMAC-signed internal message."""

    timestamp: float
    source_module: str
    payload: Any
    payload_hash: str
    signature: str
    _raw_payload_bytes: bytes = field(repr=False, default=b"")


def _payload_hash(payload: Any) -> str:
    """Deterministic SHA-256 of the JSON-serialised payload."""
    raw = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _compute_signature(
    timestamp: float,
    source_module: str,
    p_hash: str,
) -> str:
    """HMAC-SHA256 over the canonical message fields."""
    message = f"{timestamp}|{source_module}|{p_hash}".encode()
    return hmac.new(_get_key(), message, hashlib.sha256).hexdigest()


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


class CryptoBinding:
    """Layer 5: HMAC-SHA256 signed internal messages."""

    LAYER_NAME = "crypto_binding"
    LAYER_INDEX = 5

    def __init__(self) -> None:
        self._signed_count: int = 0
        self._verified_count: int = 0
        self._tamper_count: int = 0

    def sign(self, source_module: str, payload: Any) -> SignedMessage:
        """Create a signed message.

        Parameters
        ----------
        source_module:
            Identifier of the module originating the message.
        payload:
            Arbitrary JSON-serialisable data.
        """
        ts = time.time()
        p_hash = _payload_hash(payload)
        sig = _compute_signature(ts, source_module, p_hash)
        self._signed_count += 1
        return SignedMessage(
            timestamp=ts,
            source_module=source_module,
            payload=payload,
            payload_hash=p_hash,
            signature=sig,
        )

    def verify(self, msg: SignedMessage) -> tuple[bool, str]:
        """Verify the integrity of a signed message.

        Returns
        -------
        tuple[bool, str]
            (valid, reason)
        """
        self._verified_count += 1

        # 1. Check age
        age = time.time() - msg.timestamp
        if age > MAX_AGE_SECONDS:
            self._tamper_count += 1
            return False, (
                f"Message too old ({age:.1f}s > {MAX_AGE_SECONDS}s) -- "
                "possible replay attack."
            )

        # 2. Recompute payload hash
        expected_hash = _payload_hash(msg.payload)
        if not hmac.compare_digest(expected_hash, msg.payload_hash):
            self._tamper_count += 1
            return False, "Payload hash mismatch -- payload was tampered."

        # 3. Recompute signature
        expected_sig = _compute_signature(
            msg.timestamp, msg.source_module, msg.payload_hash
        )
        if not hmac.compare_digest(expected_sig, msg.signature):
            self._tamper_count += 1
            return False, "HMAC signature mismatch -- message was tampered."

        return True, "Message integrity verified."

    def status(self) -> dict:
        return {
            "layer": self.LAYER_INDEX,
            "name": self.LAYER_NAME,
            "signed_count": self._signed_count,
            "verified_count": self._verified_count,
            "tamper_attempts": self._tamper_count,
        }
