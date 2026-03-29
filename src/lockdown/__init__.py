"""
SpongeBot Lockdown -- 9-Layer Anthropic Security System.

Layers
------
0. HardwareFingerprint  -- device binding via MAC/CPU/disk UUID
1. AnthropicGate        -- API key pattern validation
2. ModelVerifier        -- Claude-only model allowlist
3. EnvironmentScanner   -- foreign SDK detection (async background)
4. ResponseFingerprint  -- Claude response structure validation
5. CryptoBinding        -- HMAC-SHA256 signed internal messages
6. TamperDetector       -- SHA-256 chained audit trail
7. SelfDestructTrigger  -- vault wipe after 3 decrypt failures
8. LockoutManager       -- permanent irrecoverable ban

Usage
-----
::

    from src.lockdown import LockdownGate

    gate = LockdownGate()
    gate.boot()
    gate.validate_request(api_key="sk-ant-api03-...", model_id="claude-sonnet-4-20250514")
"""

from __future__ import annotations

from .anthropic_gate import AnthropicGate
from .crypto_binding import CryptoBinding
from .environment_scanner import EnvironmentScanner
from .exceptions import LockdownViolation, LockoutActive
from .gate import LockdownGate
from .hardware_fingerprint import HardwareFingerprint
from .lockout_manager import LockoutManager
from .model_verifier import ModelVerifier
from .response_fingerprint import ResponseFingerprint
from .self_destruct_trigger import SelfDestructTrigger
from .tamper_detector import TamperDetector

__all__ = [
    "LockdownGate",
    "LockdownViolation",
    "LockoutActive",
    "HardwareFingerprint",
    "AnthropicGate",
    "ModelVerifier",
    "EnvironmentScanner",
    "ResponseFingerprint",
    "CryptoBinding",
    "TamperDetector",
    "SelfDestructTrigger",
    "LockoutManager",
]
