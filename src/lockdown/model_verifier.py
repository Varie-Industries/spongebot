"""
Layer 2 -- Model Verifier.

Only Claude model IDs are accepted.  Everything else is rejected with
a clear reason indicating the offending model family.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("spongebot.lockdown.model_verifier")

# Allowed Claude model families -- order matters for matching
_ALLOWED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^claude-opus-4(-\d+|-\d{8}|-.+)?$"),
    re.compile(r"^claude-sonnet-4(-\d+|-\d{8}|-.+)?$"),
    re.compile(r"^claude-haiku-4(-\d+|-\d{8}|-.+)?$"),
    re.compile(r"^claude-3(\.\d+)?-(opus|sonnet|haiku)(-\d{8}|-\d+|-.+)?$"),
    re.compile(r"^claude-3-5-(opus|sonnet|haiku)(-\d{8}|-\d+|-.+)?$"),
    # Catch-all for future Claude models
    re.compile(r"^claude-[a-z0-9]"),
]

# Explicitly blocked provider prefixes with human-readable names
_BLOCKED_PREFIXES: dict[str, str] = {
    "gpt-": "OpenAI GPT",
    "o1-": "OpenAI o1",
    "o3-": "OpenAI o3",
    "o4-": "OpenAI o4",
    "gemini-": "Google Gemini",
    "gemma-": "Google Gemma",
    "mistral-": "Mistral AI",
    "mixtral-": "Mistral Mixtral",
    "command-": "Cohere Command",
    "llama-": "Meta LLaMA",
    "llama2-": "Meta LLaMA 2",
    "llama3": "Meta LLaMA 3",
    "codellama": "Meta CodeLLaMA",
    "deepseek-": "DeepSeek",
    "qwen-": "Alibaba Qwen",
    "yi-": "01.AI Yi",
    "palm-": "Google PaLM",
    "titan-": "Amazon Titan",
    "falcon-": "TII Falcon",
    "phi-": "Microsoft Phi",
    "dbrx": "Databricks DBRX",
}


class ModelVerifier:
    """Layer 2: only Claude model IDs pass."""

    LAYER_NAME = "model_verifier"
    LAYER_INDEX = 2

    def validate(self, model_id: str) -> tuple[bool, str]:
        """Validate *model_id* against the Claude allowlist.

        Returns
        -------
        tuple[bool, str]
            (passed, reason)
        """
        if not model_id:
            return False, "Model ID is empty."

        if not isinstance(model_id, str):
            return False, f"Model ID must be a string, got {type(model_id).__name__}."

        model_id = model_id.strip().lower()

        # Check blocked prefixes first for precise rejection
        for prefix, family in _BLOCKED_PREFIXES.items():
            if model_id.startswith(prefix):
                msg = (
                    f"Model '{model_id}' belongs to {family}. "
                    "Only Anthropic Claude models are permitted."
                )
                logger.warning(msg)
                return False, msg

        # Check allowlist
        for pattern in _ALLOWED_PATTERNS:
            if pattern.match(model_id):
                logger.info("Model '%s' accepted.", model_id)
                return True, f"Model '{model_id}' is an approved Claude model."

        # Fallback: unknown model, reject
        msg = (
            f"Model '{model_id}' is not recognised as a Claude model. "
            "Only claude-* model IDs are accepted."
        )
        logger.warning(msg)
        return False, msg

    def status(self) -> dict:
        return {
            "layer": self.LAYER_INDEX,
            "name": self.LAYER_NAME,
            "allowed_pattern": "claude-*",
            "blocked_families": list(_BLOCKED_PREFIXES.values()),
        }
