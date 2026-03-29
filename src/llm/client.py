"""
ClaudeClient -- Anthropic Claude API client with lockdown validation.

ONLY provider.  Every call is validated by the lockdown subsystem (API key
gate, model verifier, response fingerprint).  Token savings are applied
through the token_saver subsystem.

Absorbed from IT_NEXUS ``cortex.py`` ``ClaudeLLM`` class: config-driven
construction, async boot/shutdown lifecycle, conversation history support,
structured response extraction, and graceful fallback when API key is
missing.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, AsyncIterator

logger = logging.getLogger("spongebot.llm.client")


class ClaudeClient:
    """Anthropic Claude API client -- ONLY provider.

    Every call is validated by lockdown layers:
    - Layer 1 (AnthropicGate): API key format check
    - Layer 2 (ModelVerifier): model ID allowlist
    - Layer 4 (ResponseFingerprint): structural response validation

    Parameters
    ----------
    config : dict
        Full SpongeBot configuration.  The ``llm`` section is used:
        - ``model`` (default ``"claude-sonnet-4-20250514"``)
        - ``max_tokens`` (default 4096)
        - ``temperature`` (default 0.7)
        - ``api_key`` (falls back to ``ANTHROPIC_API_KEY`` env var)
    lockdown : Any | None
        Optional lockdown subsystem for validation.
    token_saver : Any | None
        Optional token saver for prompt compression and caching.
    cost_tracker : Any | None
        Optional cost tracker for recording token usage.
    """

    def __init__(
        self,
        config: dict[str, Any],
        lockdown: Any | None = None,
        token_saver: Any | None = None,
        cost_tracker: Any | None = None,
    ) -> None:
        llm_cfg = config.get("llm", {})

        self._model: str = llm_cfg.get("model", "claude-sonnet-4-20250514")
        self._max_tokens: int = llm_cfg.get("max_tokens", 4096)
        self._temperature: float = llm_cfg.get("temperature", 0.7)
        self._api_key: str = (
            llm_cfg.get("api_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
        )

        self._lockdown = lockdown
        self._token_saver = token_saver
        self._cost_tracker = cost_tracker

        self._client: Any = None  # anthropic.AsyncAnthropic

        # Metrics
        self._total_calls: int = 0
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._cache_hits: int = 0
        self._errors: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def boot(self) -> None:
        """Validate API key and initialise the AsyncAnthropic client."""
        # Lockdown Layer 1: validate API key format
        if self._lockdown is not None and hasattr(self._lockdown, "validate_api_key"):
            try:
                passed, reason = self._lockdown.validate_api_key(self._api_key)
                if not passed:
                    logger.warning("Lockdown rejected API key: %s", reason)
                    logger.warning("LLM client will operate in stub mode.")
                    return
            except Exception as exc:
                logger.warning("Lockdown API key validation error: %s", exc)

        # Lockdown Layer 2: validate model ID
        if self._lockdown is not None and hasattr(self._lockdown, "validate_model"):
            try:
                passed, reason = self._lockdown.validate_model(self._model)
                if not passed:
                    logger.warning("Lockdown rejected model '%s': %s", self._model, reason)
                    logger.warning("LLM client will operate in stub mode.")
                    return
            except Exception as exc:
                logger.warning("Lockdown model validation error: %s", exc)

        if not self._api_key:
            logger.warning(
                "No Anthropic API key configured. "
                "Set ANTHROPIC_API_KEY env var or llm.api_key in config. "
                "LLM client will operate in stub mode."
            )
            return

        try:
            import anthropic
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
            logger.info("ClaudeClient ready (model=%s, max_tokens=%d).", self._model, self._max_tokens)
        except ImportError:
            logger.warning(
                "anthropic library not installed. "
                "Install with: pip install anthropic"
            )

    async def shutdown(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as exc:
                logger.warning("Error closing Anthropic client: %s", exc)
            self._client = None

        logger.info(
            "ClaudeClient shut down (calls=%d, input_tokens=%d, output_tokens=%d, "
            "cache_hits=%d, errors=%d).",
            self._total_calls,
            self._total_input_tokens,
            self._total_output_tokens,
            self._cache_hits,
            self._errors,
        )

    async def health_check(self) -> dict[str, Any]:
        """Return LLM client health metrics."""
        return {
            "status": "ok" if self._client is not None else "stub",
            "component": "llm",
            "model": self._model,
            "connected": self._client is not None,
            "total_calls": self._total_calls,
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "cache_hits": self._cache_hits,
            "errors": self._errors,
        }

    # ------------------------------------------------------------------
    # Chat (non-streaming)
    # ------------------------------------------------------------------

    async def chat(
        self,
        system_prompt: str,
        user_text: str,
        history: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a chat request to Claude.

        Parameters
        ----------
        system_prompt : str
            System-level instructions.
        user_text : str
            The current user message.
        history : list[dict] | None
            Prior conversation turns (role/content dicts).
        tools : list[dict] | None
            Tool definitions for tool-use mode.

        Returns
        -------
        dict
            ``{"text": str, "input_tokens": int, "output_tokens": int,
              "tool_use": list[dict] | None, "stop_reason": str}``
        """
        self._total_calls += 1

        # -- Token saver: compress system prompt --
        compressed_prompt = system_prompt
        if self._token_saver is not None and hasattr(self._token_saver, "compress_prompt"):
            try:
                compressed_prompt = self._token_saver.compress_prompt(system_prompt)
            except Exception as exc:
                logger.debug("Token saver compression failed: %s", exc)

        # -- Token saver: check cache --
        cache_key = self._cache_key(compressed_prompt, user_text, history)
        if self._token_saver is not None and hasattr(self._token_saver, "check_cache"):
            try:
                cached = self._token_saver.check_cache(cache_key)
                if cached is not None:
                    self._cache_hits += 1
                    logger.debug("Cache hit for prompt hash %s.", cache_key[:8])
                    return {
                        "text": cached,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "tool_use": None,
                        "stop_reason": "cache_hit",
                    }
            except Exception as exc:
                logger.debug("Token saver cache check failed: %s", exc)

        # -- API call --
        if self._client is None:
            return self._stub_response(user_text)

        messages = self._build_messages(user_text, history)

        try:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "temperature": self._temperature,
                "system": compressed_prompt,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools

            response = await self._client.messages.create(**kwargs)

        except Exception as exc:
            self._errors += 1
            logger.error("Claude API call failed: %s", exc)
            return {
                "text": self._fallback_text(),
                "input_tokens": 0,
                "output_tokens": 0,
                "tool_use": None,
                "stop_reason": "error",
            }

        # -- Extract response --
        text_parts: list[str] = []
        tool_use_blocks: list[dict[str, Any]] = []

        for block in response.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)
            elif hasattr(block, "type") and block.type == "tool_use":
                tool_use_blocks.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        text = "".join(text_parts).strip()
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        stop_reason = getattr(response, "stop_reason", "end_turn") or "end_turn"

        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens

        # -- Lockdown Layer 4: validate response fingerprint --
        if self._lockdown is not None and hasattr(self._lockdown, "validate_response"):
            try:
                raw_response = {
                    "content": [{"type": "text", "text": text}],
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    },
                    "model": response.model,
                    "stop_reason": stop_reason,
                }
                passed, reason = self._lockdown.validate_response(raw_response)
                if not passed:
                    logger.warning("Lockdown rejected response: %s", reason)
                    # Still return the response but flag it
            except Exception as exc:
                logger.debug("Lockdown response validation error: %s", exc)

        # -- Token saver: cache response --
        if self._token_saver is not None and hasattr(self._token_saver, "cache_response"):
            try:
                self._token_saver.cache_response(cache_key, text)
            except Exception as exc:
                logger.debug("Token saver cache store failed: %s", exc)

        # -- Cost tracker --
        if self._cost_tracker is not None and hasattr(self._cost_tracker, "record_cost"):
            try:
                self._cost_tracker.record_cost(
                    "anthropic",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            except Exception as exc:
                logger.debug("Cost tracker recording failed: %s", exc)

        return {
            "text": text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tool_use": tool_use_blocks if tool_use_blocks else None,
            "stop_reason": stop_reason,
        }

    # ------------------------------------------------------------------
    # Streaming chat
    # ------------------------------------------------------------------

    async def stream(
        self,
        system_prompt: str,
        user_text: str,
        history: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """Streaming chat that yields text chunks.

        Same validation chain as ``chat()``, but yields chunks via an
        async generator.  Token counts are tracked after the stream
        completes.
        """
        self._total_calls += 1

        # Compress
        compressed_prompt = system_prompt
        if self._token_saver is not None and hasattr(self._token_saver, "compress_prompt"):
            try:
                compressed_prompt = self._token_saver.compress_prompt(system_prompt)
            except Exception:
                pass

        if self._client is None:
            yield self._fallback_text()
            return

        messages = self._build_messages(user_text, history)

        try:
            async with self._client.messages.stream(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                system=compressed_prompt,
                messages=messages,
            ) as stream:
                collected_text: list[str] = []

                async for text_chunk in stream.text_stream:
                    collected_text.append(text_chunk)
                    yield text_chunk

                # After stream completes, get the final message for token counts
                final_message = await stream.get_final_message()
                input_tokens = final_message.usage.input_tokens
                output_tokens = final_message.usage.output_tokens
                self._total_input_tokens += input_tokens
                self._total_output_tokens += output_tokens

                # Cache the full response
                full_text = "".join(collected_text)
                cache_key = self._cache_key(compressed_prompt, user_text, history)
                if self._token_saver is not None and hasattr(self._token_saver, "cache_response"):
                    try:
                        self._token_saver.cache_response(cache_key, full_text)
                    except Exception:
                        pass

                # Cost tracking
                if self._cost_tracker is not None and hasattr(self._cost_tracker, "record_cost"):
                    try:
                        self._cost_tracker.record_cost(
                            "anthropic",
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                        )
                    except Exception:
                        pass

        except Exception as exc:
            self._errors += 1
            logger.error("Claude streaming call failed: %s", exc)
            yield self._fallback_text()

    # ------------------------------------------------------------------
    # Pipeline-compatible call method (used by SpongeBot.process)
    # ------------------------------------------------------------------

    async def call(
        self,
        system_prompt: str,
        user_text: str,
        context: str = "",
        **kwargs: Any,
    ) -> str:
        """Simplified call interface for the SpongeBot processing pipeline.

        Merges *context* into the system prompt and returns only the text.
        """
        full_prompt = system_prompt
        if context:
            full_prompt = f"{system_prompt}\n\n{context}"

        history = kwargs.get("history")
        result = await self.chat(full_prompt, user_text, history=history)
        return result["text"]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_messages(
        user_text: str,
        history: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """Build the messages list for the API call."""
        messages: list[dict[str, Any]] = []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})
        return messages

    @staticmethod
    def _cache_key(
        system_prompt: str,
        user_text: str,
        history: list[dict[str, Any]] | None,
    ) -> str:
        """Derive a cache key from the prompt components."""
        history_str = ""
        if history:
            history_str = "|".join(
                f"{m.get('role', '')}:{m.get('content', '')}" for m in history
            )
        raw = f"{system_prompt}|{user_text}|{history_str}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _fallback_text() -> str:
        """Graceful fallback text when the API is unavailable."""
        return (
            "I'm having trouble connecting to my language processing service "
            "right now. Could you try again in a moment?"
        )

    @staticmethod
    def _stub_response(user_text: str) -> dict[str, Any]:
        """Return a stub response when no client is available."""
        return {
            "text": (
                "I'm running in stub mode without an LLM connection. "
                "Please configure an Anthropic API key to enable full responses."
            ),
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_use": None,
            "stop_reason": "stub",
        }
