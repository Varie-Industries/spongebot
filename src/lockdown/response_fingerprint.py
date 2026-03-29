"""
Layer 4 -- Response Fingerprint.

Validates that API responses carry the structural markers unique to
Anthropic Claude responses.  Three consecutive failures trigger a
lockout alert.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger("spongebot.lockdown.response_fingerprint")

_MAX_CONSECUTIVE_FAILURES = 3


def _has_content_blocks(response: dict) -> bool:
    """Check for Claude-style content blocks with type='text'."""
    content = response.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and block.get("type") == "text"
        for block in content
    )


def _has_usage_object(response: dict) -> bool:
    """Check for usage object with input_tokens and output_tokens."""
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return False
    return "input_tokens" in usage and "output_tokens" in usage


def _has_claude_model(response: dict) -> bool:
    """Check that the model field starts with 'claude-'."""
    model = response.get("model", "")
    return isinstance(model, str) and model.startswith("claude-")


def _has_stop_reason(response: dict) -> bool:
    """Check for stop_reason field (Anthropic uses stop_reason, not finish_reason)."""
    return "stop_reason" in response


# Ordered list of checks with human-readable labels
_CHECKS: list[tuple[str, Any]] = [
    ("content_blocks_with_type_text", _has_content_blocks),
    ("usage_with_token_counts", _has_usage_object),
    ("model_starts_with_claude", _has_claude_model),
    ("stop_reason_field_present", _has_stop_reason),
]


class ResponseFingerprint:
    """Layer 4: validate Claude-unique response structure."""

    LAYER_NAME = "response_fingerprint"
    LAYER_INDEX = 4

    def __init__(
        self,
        on_lockout_alert: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.on_lockout_alert = on_lockout_alert
        self._consecutive_failures: int = 0
        self._total_checks: int = 0
        self._total_failures: int = 0
        self.lockout_triggered: bool = False

    def validate(self, response: Any) -> tuple[bool, str]:
        """Validate a raw API response dict.

        Returns
        -------
        tuple[bool, str]
            (passed, reason)
        """
        self._total_checks += 1

        if not isinstance(response, dict):
            return self._fail(
                "Response is not a dict -- cannot validate structure."
            )

        failed_checks: list[str] = []
        for label, check_fn in _CHECKS:
            if not check_fn(response):
                failed_checks.append(label)

        if failed_checks:
            msg = (
                f"Response failed structural checks: {', '.join(failed_checks)}. "
                "Does not match Claude response fingerprint."
            )
            return self._fail(msg)

        # Reset consecutive counter on success
        self._consecutive_failures = 0
        logger.debug("Response fingerprint valid.")
        return True, "Response matches Claude fingerprint."

    def _fail(self, reason: str) -> tuple[bool, str]:
        """Record a failure and possibly trigger lockout alert."""
        self._consecutive_failures += 1
        self._total_failures += 1
        logger.warning(
            "Response fingerprint FAIL (%d/%d consecutive): %s",
            self._consecutive_failures,
            _MAX_CONSECUTIVE_FAILURES,
            reason,
        )

        if self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            self.lockout_triggered = True
            alert_msg = (
                f"LOCKOUT ALERT: {_MAX_CONSECUTIVE_FAILURES} consecutive "
                "response fingerprint failures. Possible provider substitution."
            )
            logger.critical(alert_msg)
            if self.on_lockout_alert:
                self.on_lockout_alert(alert_msg)

        return False, reason

    def reset(self) -> None:
        """Reset the consecutive failure counter (e.g. after manual review)."""
        self._consecutive_failures = 0

    def status(self) -> dict:
        return {
            "layer": self.LAYER_INDEX,
            "name": self.LAYER_NAME,
            "consecutive_failures": self._consecutive_failures,
            "max_before_lockout": _MAX_CONSECUTIVE_FAILURES,
            "total_checks": self._total_checks,
            "total_failures": self._total_failures,
            "lockout_triggered": self.lockout_triggered,
        }
