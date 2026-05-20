"""
SpongeBot Security Vault System

Absorbed from IT_NEXUS SecurityCore patterns:
- AES-256 Fernet encryption with PBKDF2-HMAC-SHA256 key derivation
- Tamper-evident SHA-256 audit chain
- Dual-signature memory guard (A-MemGuard consensus)
- Emergency self-destruct with 3-pass overwrite
"""

from __future__ import annotations

from .audit_chain import AuditChain, AuditEntry
from .memguard import MemGuard, MemGuardError, SignatureRequest
from .self_destruct import SelfDestruct, SelfDestructError
from .vault_core import VaultCore, VaultError

__all__ = [
    "VaultCore",
    "VaultError",
    "AuditEntry",
    "AuditChain",
    "MemGuard",
    "SignatureRequest",
    "MemGuardError",
    "SelfDestruct",
    "SelfDestructError",
]
