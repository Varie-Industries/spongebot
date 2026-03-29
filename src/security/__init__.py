"""
SpongeBot Security Vault System

Absorbed from IT_NEXUS SecurityCore patterns:
- AES-256 Fernet encryption with PBKDF2-HMAC-SHA256 key derivation
- Tamper-evident SHA-256 audit chain
- Dual-signature memory guard (A-MemGuard consensus)
- Emergency self-destruct with 3-pass overwrite
"""

from __future__ import annotations

from .vault_core import VaultCore, VaultError
from .audit_chain import AuditEntry, AuditChain
from .memguard import MemGuard, SignatureRequest, MemGuardError
from .self_destruct import SelfDestruct, SelfDestructError

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
