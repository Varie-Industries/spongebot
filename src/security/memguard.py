"""
SpongeBot A-MemGuard -- Dual-Signature Memory Guard

Consensus system that prevents single-point-of-compromise memory
poisoning. Vault writes require dual signatures from two independent
engines (absorption + learning) before they are accepted.

This ensures that a compromised single subsystem cannot unilaterally
modify the vault contents or inject malicious patterns.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class MemGuardError(Exception):
    """Raised when MemGuard consensus validation fails."""


class SignatureStatus(Enum):
    """Status of a signature request through the consensus pipeline."""
    PENDING = auto()
    PARTIALLY_SIGNED = auto()
    APPROVED = auto()
    REJECTED = auto()
    EXPIRED = auto()


@dataclass
class SignatureRequest:
    """A request for dual-signature consensus on a vault write operation.

    Both the absorption engine and the learning engine must sign
    the request before the write is permitted.

    Attributes
    ----------
    request_id : str
        Unique identifier for this request (hex-encoded random bytes).
    operation : str
        The vault operation being requested (e.g., "store_secret",
        "store_skill", "update_memory").
    payload_hash : str
        SHA-256 hash of the data payload to be written.
    payload : Any
        The actual data payload (kept in memory, not persisted).
    requester : str
        Identity of the subsystem that initiated the request.
    created_at : float
        Unix timestamp of request creation.
    ttl : float
        Time-to-live in seconds before the request expires (default 300).
    signatures : dict[str, str]
        Map of signer identity to their HMAC signature.
    status : SignatureStatus
        Current status of the request.
    """

    request_id: str
    operation: str
    payload_hash: str
    payload: Any
    requester: str
    created_at: float = field(default_factory=time.time)
    ttl: float = 300.0
    signatures: dict[str, str] = field(default_factory=dict)
    status: SignatureStatus = SignatureStatus.PENDING

    @property
    def is_expired(self) -> bool:
        """Check if this request has exceeded its TTL."""
        return (time.time() - self.created_at) > self.ttl

    @property
    def signed_by(self) -> list[str]:
        """Return the list of identities that have signed this request."""
        return list(self.signatures.keys())


class MemGuard:
    """Dual-signature consensus guard for vault write operations.

    Requires two independent signatures (from distinct engine identities)
    before permitting any vault write. This prevents a single compromised
    subsystem from poisoning SpongeBot's memory or skill store.

    Parameters
    ----------
    required_signers : frozenset[str]
        The set of engine identities required to sign (must have
        exactly 2 members, e.g., ``{"absorption", "learning"}``).
    shared_secret : str
        Shared HMAC secret used to verify signature authenticity.
        Each signer computes ``HMAC-SHA256(secret, request_id + payload_hash)``.
    request_ttl : float
        Default TTL in seconds for signature requests (default 300).
    max_pending : int
        Maximum number of pending requests before oldest are pruned
        (default 1000).
    """

    _DEFAULT_SIGNERS = frozenset({"absorption", "learning"})

    def __init__(
        self,
        required_signers: frozenset[str] | None = None,
        shared_secret: str | None = None,
        request_ttl: float = 300.0,
        max_pending: int = 1000,
    ) -> None:
        self._required_signers = required_signers or self._DEFAULT_SIGNERS
        if len(self._required_signers) < 2:
            raise MemGuardError(
                "MemGuard requires at least 2 signers for consensus"
            )

        self._shared_secret = (
            shared_secret or os.urandom(32).hex()
        ).encode("utf-8")
        self._request_ttl = request_ttl
        self._max_pending = max_pending

        self._lock = threading.Lock()
        self._pending: dict[str, SignatureRequest] = {}
        self._approved_count = 0
        self._rejected_count = 0

    # ------------------------------------------------------------------
    # Request lifecycle
    # ------------------------------------------------------------------

    def create_request(
        self,
        operation: str,
        payload: Any,
        requester: str,
        ttl: float | None = None,
    ) -> SignatureRequest:
        """Create a new signature request for a vault write operation.

        Parameters
        ----------
        operation : str
            Description of the vault operation.
        payload : Any
            The data to be written. Will be serialised for hashing.
        requester : str
            Identity of the requesting subsystem.
        ttl : float, optional
            Custom TTL for this request. Uses default if not specified.

        Returns
        -------
        SignatureRequest
            The created request, ready to collect signatures.

        Raises
        ------
        MemGuardError
            If the requester is not a recognised signer.
        """
        if requester not in self._required_signers:
            raise MemGuardError(
                f"Unknown requester '{requester}'. "
                f"Must be one of: {', '.join(sorted(self._required_signers))}"
            )

        request_id = os.urandom(16).hex()
        payload_str = str(payload).encode("utf-8")
        payload_hash = hashlib.sha256(payload_str).hexdigest()

        request = SignatureRequest(
            request_id=request_id,
            operation=operation,
            payload_hash=payload_hash,
            payload=payload,
            requester=requester,
            ttl=ttl or self._request_ttl,
        )

        with self._lock:
            self._prune_expired()
            if len(self._pending) >= self._max_pending:
                self._evict_oldest()
            self._pending[request_id] = request

        return request

    def sign(
        self,
        request_id: str,
        signer: str,
    ) -> SignatureRequest:
        """Add a signature to a pending request.

        The signer computes ``HMAC-SHA256(shared_secret, request_id + payload_hash)``
        to prove knowledge of the shared secret and bind the signature
        to the specific request and payload.

        Parameters
        ----------
        request_id : str
            ID of the request to sign.
        signer : str
            Identity of the signing engine.

        Returns
        -------
        SignatureRequest
            Updated request with the new signature.

        Raises
        ------
        MemGuardError
            If the request is not found, expired, already fully signed,
            or the signer is not a recognised identity.
        """
        if signer not in self._required_signers:
            raise MemGuardError(
                f"Unknown signer '{signer}'. "
                f"Must be one of: {', '.join(sorted(self._required_signers))}"
            )

        with self._lock:
            request = self._pending.get(request_id)
            if request is None:
                raise MemGuardError(
                    f"Request '{request_id}' not found or already completed"
                )

            if request.is_expired:
                request.status = SignatureStatus.EXPIRED
                del self._pending[request_id]
                raise MemGuardError(
                    f"Request '{request_id}' has expired"
                )

            if request.status == SignatureStatus.APPROVED:
                raise MemGuardError(
                    f"Request '{request_id}' is already fully approved"
                )

            if signer in request.signatures:
                raise MemGuardError(
                    f"Signer '{signer}' has already signed request '{request_id}'"
                )

            # Compute HMAC signature
            sign_data = f"{request_id}{request.payload_hash}".encode()
            signature = hmac.new(
                self._shared_secret, sign_data, hashlib.sha256
            ).hexdigest()
            request.signatures[signer] = signature

            # Check if we have all required signatures
            if self._required_signers.issubset(set(request.signatures.keys())):
                request.status = SignatureStatus.APPROVED
                self._approved_count += 1
            else:
                request.status = SignatureStatus.PARTIALLY_SIGNED

            return request

    def reject(self, request_id: str, reason: str = "") -> SignatureRequest:
        """Reject a pending request, preventing it from being approved.

        Parameters
        ----------
        request_id : str
            ID of the request to reject.
        reason : str
            Optional reason for rejection.

        Returns
        -------
        SignatureRequest
            The rejected request.

        Raises
        ------
        MemGuardError
            If the request is not found.
        """
        with self._lock:
            request = self._pending.get(request_id)
            if request is None:
                raise MemGuardError(
                    f"Request '{request_id}' not found"
                )
            request.status = SignatureStatus.REJECTED
            self._rejected_count += 1
            del self._pending[request_id]
            return request

    def validate(self, request_id: str) -> tuple[bool, SignatureRequest]:
        """Validate that a request has achieved full consensus.

        Parameters
        ----------
        request_id : str
            ID of the request to validate.

        Returns
        -------
        tuple[bool, SignatureRequest]
            ``(True, request)`` if approved with all required signatures,
            ``(False, request)`` otherwise.

        Raises
        ------
        MemGuardError
            If the request is not found.
        """
        with self._lock:
            request = self._pending.get(request_id)
            if request is None:
                raise MemGuardError(
                    f"Request '{request_id}' not found"
                )

            if request.is_expired:
                request.status = SignatureStatus.EXPIRED
                del self._pending[request_id]
                return (False, request)

            if request.status == SignatureStatus.APPROVED:
                # Verify all HMAC signatures
                for signer, sig in request.signatures.items():
                    sign_data = (
                        f"{request_id}{request.payload_hash}".encode()
                    )
                    expected = hmac.new(
                        self._shared_secret, sign_data, hashlib.sha256
                    ).hexdigest()
                    if not hmac.compare_digest(sig, expected):
                        request.status = SignatureStatus.REJECTED
                        self._rejected_count += 1
                        return (False, request)
                return (True, request)

            return (False, request)

    def complete(self, request_id: str) -> SignatureRequest | None:
        """Remove a completed (approved) request from the pending queue.

        Parameters
        ----------
        request_id : str
            ID of the request to complete.

        Returns
        -------
        SignatureRequest or None
            The completed request, or ``None`` if not found.
        """
        with self._lock:
            return self._pending.pop(request_id, None)

    # ------------------------------------------------------------------
    # Convenience: full consensus flow
    # ------------------------------------------------------------------

    def request_and_sign(
        self,
        operation: str,
        payload: Any,
        signer_a: str,
        signer_b: str,
    ) -> tuple[bool, SignatureRequest]:
        """Convenience method for immediate dual-signature consensus.

        Creates a request signed by ``signer_a``, then co-signed by
        ``signer_b``, and validates the result. Useful when both engines
        are available in the same execution context.

        Parameters
        ----------
        operation : str
            The vault operation description.
        payload : Any
            Data to be written.
        signer_a : str
            First signer identity.
        signer_b : str
            Second signer identity.

        Returns
        -------
        tuple[bool, SignatureRequest]
            ``(True, request)`` if consensus achieved, ``(False, request)`` otherwise.
        """
        request = self.create_request(
            operation=operation,
            payload=payload,
            requester=signer_a,
        )
        self.sign(request.request_id, signer_a)
        self.sign(request.request_id, signer_b)
        return self.validate(request.request_id)

    # ------------------------------------------------------------------
    # Status & metrics
    # ------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        """Return the number of pending signature requests."""
        with self._lock:
            return len(self._pending)

    @property
    def stats(self) -> dict[str, int]:
        """Return MemGuard statistics.

        Returns
        -------
        dict
            Keys: pending, approved, rejected.
        """
        with self._lock:
            return {
                "pending": len(self._pending),
                "approved": self._approved_count,
                "rejected": self._rejected_count,
            }

    # ------------------------------------------------------------------
    # Internal maintenance
    # ------------------------------------------------------------------

    def _prune_expired(self) -> None:
        """Remove expired requests (caller must hold lock)."""
        expired_ids = [
            rid
            for rid, req in self._pending.items()
            if req.is_expired
        ]
        for rid in expired_ids:
            del self._pending[rid]

    def _evict_oldest(self) -> None:
        """Evict the oldest pending request (caller must hold lock)."""
        if not self._pending:
            return
        oldest_id = min(
            self._pending, key=lambda rid: self._pending[rid].created_at
        )
        del self._pending[oldest_id]
