"""
Layer 0 -- Hardware Fingerprint.

Binds the running instance to physical hardware via a composite hash of
MAC address, CPU identifier, and (on macOS/Linux) root-disk UUID.

On first boot the fingerprint is persisted (encrypted) in the vault
directory.  Every subsequent boot compares the live fingerprint against
the stored one.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import platform
import subprocess
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger("spongebot.lockdown.hardware_fingerprint")

_VAULT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "vault"
_FP_FILE = _VAULT_DIR / ".hardware_fingerprint"
_SALT = b"spongebot-hw-bind-v1"


# ------------------------------------------------------------------
# Fingerprint generation
# ------------------------------------------------------------------

def _mac_address() -> str:
    """Return the MAC address as a hex string."""
    return format(uuid.getnode(), "012x")


def _cpu_info() -> str:
    """Return a stable CPU identifier string."""
    return platform.processor() or platform.machine()


def _disk_uuid() -> str:
    """Best-effort root disk UUID (macOS / Linux)."""
    system = platform.system()
    try:
        if system == "Darwin":
            result = subprocess.run(
                ["diskutil", "info", "-plist", "/"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            # Pull DiskUUID from plist output (simple grep, no plist dep)
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if stripped.startswith("<string>") and "-" in stripped:
                    return stripped.replace("<string>", "").replace("</string>", "")
        elif system == "Linux":
            blkid = Path("/etc/machine-id")
            if blkid.exists():
                return blkid.read_text().strip()
    except Exception:
        pass
    return "no-disk-uuid"


def generate_fingerprint() -> str:
    """Create a deterministic fingerprint hash from hardware attributes."""
    raw = "|".join([_mac_address(), _cpu_info(), _disk_uuid()])
    return hmac.new(_SALT, raw.encode(), hashlib.sha256).hexdigest()


# ------------------------------------------------------------------
# Persistence helpers
# ------------------------------------------------------------------

def _ensure_vault() -> None:
    _VAULT_DIR.mkdir(parents=True, exist_ok=True)


def _xor_mask(data: bytes) -> bytes:
    """Simple XOR mask using SALT -- not crypto-grade, but adds a layer."""
    key = hashlib.sha256(_SALT).digest()
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def store_fingerprint(fingerprint: str) -> None:
    """Persist the fingerprint (masked) to the vault."""
    _ensure_vault()
    payload = json.dumps({"fp": fingerprint}).encode()
    _FP_FILE.write_bytes(_xor_mask(payload))
    logger.info("Hardware fingerprint stored in vault.")


def load_stored_fingerprint() -> Optional[str]:
    """Load and return the previously stored fingerprint, or None."""
    if not _FP_FILE.exists():
        return None
    try:
        raw = _xor_mask(_FP_FILE.read_bytes())
        data = json.loads(raw)
        return data.get("fp")
    except Exception:
        logger.warning("Failed to load stored fingerprint -- file corrupt?")
        return None


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

class HardwareFingerprint:
    """Layer 0: device binding via composite hardware hash."""

    LAYER_NAME = "hardware_fingerprint"
    LAYER_INDEX = 0

    def __init__(self) -> None:
        self.current_fingerprint: str = ""
        self.stored_fingerprint: Optional[str] = None
        self.verified: bool = False

    def initialize(self) -> None:
        """Generate fingerprint and compare against vault."""
        self.current_fingerprint = generate_fingerprint()
        self.stored_fingerprint = load_stored_fingerprint()

        if self.stored_fingerprint is None:
            # First run -- store and trust
            store_fingerprint(self.current_fingerprint)
            self.stored_fingerprint = self.current_fingerprint
            self.verified = True
            logger.info("First boot -- hardware fingerprint registered.")
        elif hmac.compare_digest(self.current_fingerprint, self.stored_fingerprint):
            self.verified = True
            logger.info("Hardware fingerprint verified.")
        else:
            self.verified = False
            logger.critical(
                "HARDWARE MISMATCH  stored=%s  current=%s",
                self.stored_fingerprint[:12] + "...",
                self.current_fingerprint[:12] + "...",
            )

    def verify(self) -> tuple[bool, str]:
        """Return (passed, reason)."""
        if not self.current_fingerprint:
            self.initialize()
        if self.verified:
            return True, "Hardware fingerprint matches."
        return False, "Hardware fingerprint mismatch -- possible device migration."

    def status(self) -> dict:
        return {
            "layer": self.LAYER_INDEX,
            "name": self.LAYER_NAME,
            "verified": self.verified,
            "fingerprint_prefix": (self.current_fingerprint[:12] + "...")
            if self.current_fingerprint
            else "not-generated",
        }
