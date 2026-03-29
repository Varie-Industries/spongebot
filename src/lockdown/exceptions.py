"""
Lockdown-specific exceptions.

All security violation errors funnel through LockdownViolation so
callers can catch a single base type.
"""

from __future__ import annotations


class LockdownViolation(Exception):
    """Raised when any lockdown layer detects a security breach."""

    def __init__(
        self,
        message: str,
        *,
        layer: str = "unknown",
        details: dict | None = None,
    ) -> None:
        self.layer = layer
        self.details = details or {}
        super().__init__(f"[Layer:{layer}] {message}")


class LockoutActive(LockdownViolation):
    """Raised when the system is permanently locked out."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"PERMANENT LOCKOUT ACTIVE: {reason}",
            layer="lockout_manager",
        )
