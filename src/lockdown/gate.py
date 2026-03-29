"""
LockdownGate -- 9-Layer Anthropic Lockdown Orchestrator.

Coordinates all security layers in sequence.  Any single layer failure
can escalate to a permanent lockout depending on severity.

Layer Index | Module                  | Purpose
----------- | ----------------------- | --------------------------------
0           | hardware_fingerprint    | Device binding via MAC/CPU/disk
1           | anthropic_gate          | API key format validation
2           | model_verifier          | Claude-only model IDs
3           | environment_scanner     | Foreign SDK detection
4           | response_fingerprint    | Claude response structure check
5           | crypto_binding          | HMAC-signed internal messages
6           | tamper_detector         | SHA-256 chained audit trail
7           | self_destruct_trigger   | Vault wipe after 3 decrypt fails
8           | lockout_manager         | Permanent irrecoverable ban
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .anthropic_gate import AnthropicGate
from .crypto_binding import CryptoBinding
from .environment_scanner import EnvironmentScanner
from .exceptions import LockdownViolation, LockoutActive
from .hardware_fingerprint import HardwareFingerprint
from .lockout_manager import LockoutManager
from .model_verifier import ModelVerifier
from .response_fingerprint import ResponseFingerprint
from .self_destruct_trigger import SelfDestructTrigger
from .tamper_detector import TamperDetector

logger = logging.getLogger("spongebot.lockdown.gate")


class LockdownGate:
    """Orchestrator for the 9-layer Anthropic lockdown system.

    Usage
    -----
    ::

        gate = LockdownGate()
        gate.boot()                           # layers 0, 6, 8

        gate.validate_request(api_key, model)  # layers 1, 2
        gate.validate_response(raw_response)   # layer 4
        gate.scan_environment()                # layer 3

        status = gate.get_status()             # all layers
    """

    LAYERS = [
        "hardware_fingerprint",
        "anthropic_gate",
        "model_verifier",
        "environment_scanner",
        "response_fingerprint",
        "crypto_binding",
        "tamper_detector",
        "self_destruct_trigger",
        "lockout_manager",
    ]

    def __init__(self, *, scan_interval: float = 30.0) -> None:
        # Instantiate all layers
        self.lockout_manager = LockoutManager()
        self.hardware_fingerprint = HardwareFingerprint()
        self.anthropic_gate = AnthropicGate()
        self.model_verifier = ModelVerifier()
        self.environment_scanner = EnvironmentScanner(
            scan_interval=scan_interval,
            on_violation=self._on_env_violation,
        )
        self.response_fingerprint = ResponseFingerprint(
            on_lockout_alert=self._on_response_lockout,
        )
        self.crypto_binding = CryptoBinding()
        self.tamper_detector = TamperDetector()
        self.self_destruct_trigger = SelfDestructTrigger(
            on_destruct=self._on_self_destruct,
        )

        self._booted: bool = False

    # ----------------------------------------------------------
    # Boot sequence
    # ----------------------------------------------------------

    def boot(self) -> None:
        """Initialize all layers and verify the security chain.

        Raises
        ------
        LockoutActive
            If a permanent lockout is already in effect.
        LockdownViolation
            If any boot-time layer fails verification.
        """
        logger.info("=== LOCKDOWN GATE BOOT SEQUENCE ===")

        # Layer 8 -- check for existing lockout FIRST
        if self.lockout_manager.check_lockout():
            record = self.lockout_manager.get_lockout_record()
            reason = record.get("reason", "unknown") if record else "unknown"
            logger.critical("Boot aborted: permanent lockout active.")
            raise LockoutActive(reason)

        # Layer 0 -- hardware fingerprint
        hw_ok, hw_reason = self.hardware_fingerprint.verify()
        if not hw_ok:
            self._violation(
                "hardware_fingerprint",
                hw_reason,
                permanent=True,
            )

        # Layer 6 -- audit chain integrity
        chain_ok, chain_reason = self.tamper_detector.initialize()
        if not chain_ok:
            self._violation(
                "tamper_detector",
                chain_reason,
                permanent=False,  # alert, not auto-lockout
            )

        # Record successful boot in audit chain
        self.tamper_detector.append("boot", {"status": "success"})

        self._booted = True
        logger.info("=== LOCKDOWN GATE BOOT COMPLETE (all layers green) ===")

    # ----------------------------------------------------------
    # Runtime validation
    # ----------------------------------------------------------

    def validate_request(self, api_key: str, model_id: str) -> None:
        """Run layers 1 (API key) and 2 (model ID).

        Raises
        ------
        LockoutActive
            If system is locked out.
        LockdownViolation
            If key or model validation fails.
        """
        self._assert_not_locked()

        # Layer 1 -- Anthropic key gate
        key_ok, key_reason = self.anthropic_gate.validate(api_key)
        if not key_ok:
            self.tamper_detector.append(
                "api_key_rejected",
                {"reason": key_reason},
            )
            self._violation("anthropic_gate", key_reason, permanent=True)

        # Layer 2 -- Model verifier
        model_ok, model_reason = self.model_verifier.validate(model_id)
        if not model_ok:
            self.tamper_detector.append(
                "model_rejected",
                {"model_id": model_id, "reason": model_reason},
            )
            self._violation("model_verifier", model_reason, permanent=True)

        self.tamper_detector.append(
            "request_validated",
            {"model_id": model_id},
        )

    def validate_response(self, response: Any) -> None:
        """Run layer 4 (response fingerprint).

        Raises
        ------
        LockoutActive
            If system is locked out.
        LockdownViolation
            If response fingerprint fails AND consecutive threshold hit.
        """
        self._assert_not_locked()

        resp_ok, resp_reason = self.response_fingerprint.validate(response)
        if not resp_ok:
            self.tamper_detector.append(
                "response_fingerprint_fail",
                {"reason": resp_reason},
            )
            if self.response_fingerprint.lockout_triggered:
                self._violation(
                    "response_fingerprint",
                    resp_reason,
                    permanent=True,
                )
            else:
                logger.warning("Response fingerprint fail (non-fatal): %s", resp_reason)

    def scan_environment(self) -> None:
        """Run layer 3 (environment scan) synchronously.

        Raises
        ------
        LockoutActive
            If system is locked out.
        LockdownViolation
            If foreign SDKs are detected.
        """
        self._assert_not_locked()

        clean, reason = self.environment_scanner.scan_now()
        if not clean:
            self.tamper_detector.append(
                "environment_violation",
                {"reason": reason, "details": self.environment_scanner.violation_details},
            )
            self._violation("environment_scanner", reason, permanent=True)

    async def start_background_scanner(self) -> None:
        """Start the async background environment scanner (layer 3)."""
        await self.environment_scanner.start_background_scan()

    async def stop_background_scanner(self) -> None:
        """Stop the async background environment scanner."""
        await self.environment_scanner.stop_background_scan()

    def validate_all(
        self,
        api_key: str,
        model_id: str,
        response: Optional[Any] = None,
    ) -> None:
        """Run the full validation chain (layers 0-8).

        Raises
        ------
        LockoutActive
            If system is locked out.
        LockdownViolation
            If any layer fails.
        """
        self._assert_not_locked()

        if not self._booted:
            self.boot()

        self.validate_request(api_key, model_id)
        self.scan_environment()

        if response is not None:
            self.validate_response(response)

        logger.info("Full validation chain passed.")

    # ----------------------------------------------------------
    # Crypto binding pass-through
    # ----------------------------------------------------------

    def sign_message(self, source_module: str, payload: Any) -> Any:
        """Sign an internal message (layer 5)."""
        return self.crypto_binding.sign(source_module, payload)

    def verify_message(self, signed_msg: Any) -> tuple[bool, str]:
        """Verify a signed internal message (layer 5)."""
        valid, reason = self.crypto_binding.verify(signed_msg)
        if not valid:
            self.tamper_detector.append(
                "crypto_tamper_detected",
                {"reason": reason},
            )
        return valid, reason

    # ----------------------------------------------------------
    # Self-destruct pass-through (layer 7)
    # ----------------------------------------------------------

    def record_decrypt_success(self) -> None:
        """Record a successful vault decrypt."""
        self.self_destruct_trigger.record_decrypt_success()

    def record_decrypt_failure(self) -> None:
        """Record a failed vault decrypt. May trigger self-destruct.

        Raises
        ------
        LockdownViolation
            If self-destruct is triggered.
        """
        safe, reason = self.self_destruct_trigger.record_decrypt_failure()
        if not safe:
            self.tamper_detector.append("self_destruct_triggered", {"reason": reason})
            self._violation("self_destruct_trigger", reason, permanent=True)

    # ----------------------------------------------------------
    # Status
    # ----------------------------------------------------------

    def get_status(self) -> dict:
        """Return a consolidated status dict for all 9 layers."""
        return {
            "booted": self._booted,
            "locked_out": self.lockout_manager.check_lockout(),
            "layers": {
                "layer_0_hardware_fingerprint": self.hardware_fingerprint.status(),
                "layer_1_anthropic_gate": self.anthropic_gate.status(),
                "layer_2_model_verifier": self.model_verifier.status(),
                "layer_3_environment_scanner": self.environment_scanner.status(),
                "layer_4_response_fingerprint": self.response_fingerprint.status(),
                "layer_5_crypto_binding": self.crypto_binding.status(),
                "layer_6_tamper_detector": self.tamper_detector.status(),
                "layer_7_self_destruct_trigger": self.self_destruct_trigger.status(),
                "layer_8_lockout_manager": self.lockout_manager.status(),
            },
        }

    # ----------------------------------------------------------
    # Internal violation handling
    # ----------------------------------------------------------

    def _assert_not_locked(self) -> None:
        """Raise immediately if system is locked out."""
        if self.lockout_manager.check_lockout():
            record = self.lockout_manager.get_lockout_record()
            reason = record.get("reason", "unknown") if record else "unknown"
            raise LockoutActive(reason)

    def _violation(
        self,
        layer: str,
        reason: str,
        *,
        permanent: bool = False,
    ) -> None:
        """Handle a security violation from any layer.

        Parameters
        ----------
        layer:
            Name of the layer that triggered the violation.
        reason:
            Human-readable description of the violation.
        permanent:
            If True, engage permanent lockout before raising.
        """
        logger.critical("VIOLATION [%s]: %s (permanent=%s)", layer, reason, permanent)

        if permanent:
            self.lockout_manager.lockout(
                reason=reason,
                trigger_layer=layer,
                details={"permanent": True},
            )

        raise LockdownViolation(
            reason,
            layer=layer,
            details={"permanent": permanent},
        )

    # ----------------------------------------------------------
    # Callbacks for sub-layers
    # ----------------------------------------------------------

    def _on_env_violation(self, reason: str) -> None:
        """Called by background environment scanner on detection."""
        logger.critical("Background env scanner violation: %s", reason)
        self.tamper_detector.append("background_env_violation", {"reason": reason})
        self.lockout_manager.lockout(
            reason=reason,
            trigger_layer="environment_scanner",
            details={"background": True},
        )

    def _on_response_lockout(self, alert_msg: str) -> None:
        """Called by response fingerprint on consecutive failure threshold."""
        logger.critical("Response fingerprint lockout alert: %s", alert_msg)
        self.tamper_detector.append("response_lockout_alert", {"msg": alert_msg})
        self.lockout_manager.lockout(
            reason=alert_msg,
            trigger_layer="response_fingerprint",
        )

    def _on_self_destruct(self) -> None:
        """Called after vault wipe by self-destruct trigger."""
        logger.critical("Self-destruct completed -- engaging permanent lockout.")
        self.lockout_manager.lockout(
            reason="Self-destruct triggered after repeated vault decrypt failures.",
            trigger_layer="self_destruct_trigger",
        )
