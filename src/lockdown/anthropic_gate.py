"""
Layer 1 -- Anthropic API-Key Gate.

Validates that the supplied API key matches the Anthropic key pattern.
Rejects empty keys, wrong prefixes, and keys that look like they
belong to other providers.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("spongebot.lockdown.anthropic_gate")

# Anthropic production key pattern
_ANTHROPIC_PATTERN = re.compile(r"^sk-ant-api03-[A-Za-z0-9_-]+$")

# Known non-Anthropic prefixes to reject with clear messaging
_FOREIGN_PATTERNS: dict[str, re.Pattern[str]] = {
    "OpenAI": re.compile(r"^sk-(?!ant)"),
    "Google AI": re.compile(r"^AIza"),
    "Cohere": re.compile(r"^[A-Za-z0-9]{40}$"),  # generic 40-char hex
    "AWS Bedrock": re.compile(r"^AKIA"),
    "Azure": re.compile(r"^[a-f0-9]{32}$"),
}


class AnthropicGate:
    """Layer 1: API key must be a valid Anthropic key."""

    LAYER_NAME = "anthropic_gate"
    LAYER_INDEX = 1

    def validate(self, api_key: str) -> tuple[bool, str]:
        """Validate *api_key* against the Anthropic pattern.

        Returns
        -------
        tuple[bool, str]
            (passed, reason) -- *reason* explains rejection on failure.
        """
        if not api_key:
            logger.warning("API key is empty.")
            return False, "API key is empty -- provide a valid Anthropic key."

        if not isinstance(api_key, str):
            logger.warning("API key is not a string (type=%s).", type(api_key).__name__)
            return False, "API key must be a string."

        # Strip accidental whitespace
        api_key = api_key.strip()

        # Check for foreign providers first so we give a precise message
        for provider, pattern in _FOREIGN_PATTERNS.items():
            if pattern.match(api_key):
                msg = (
                    f"Key looks like a {provider} key, not Anthropic. "
                    "SpongeBot only operates with Anthropic Claude."
                )
                logger.warning(msg)
                return False, msg

        # Validate Anthropic format
        if not _ANTHROPIC_PATTERN.match(api_key):
            msg = (
                "API key does not match Anthropic format "
                "(expected prefix 'sk-ant-api03-')."
            )
            logger.warning(msg)
            return False, msg

        logger.info("Anthropic API key validated.")
        return True, "API key matches Anthropic pattern."

    def status(self) -> dict:
        return {
            "layer": self.LAYER_INDEX,
            "name": self.LAYER_NAME,
            "pattern": _ANTHROPIC_PATTERN.pattern,
        }
