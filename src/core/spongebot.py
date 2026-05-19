"""
SpongeBot Core Orchestrator -- The Brain.

Wires together all subsystems (security, memory, token_saver, absorption,
skills, learning, llm) into a unified absorption pipeline.

Dependency-ordered startup with graceful fallback to stub implementations,
timed boot steps, and reverse-order shutdown.

Boot order:
    1. security     -- encryption vault / audit trail
    2. memory       -- vector store for recall
    3. token_saver  -- response cache / prompt compression
    4. absorption   -- pattern extraction from interactions
    5. skills       -- skill DAG for capability routing
    6. learning     -- tier progression engine
    7. llm_client   -- Claude API interface

Usage:
    bot = SpongeBot()
    await bot.boot()
    response = await bot.process("Hello, SpongeBot!")
    await bot.shutdown()
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from src.core.config import load_config, DEFAULT_CONFIG, _deep_merge

logger = logging.getLogger("spongebot.core")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Boot banner
# ---------------------------------------------------------------------------

_BANNER = r"""
  ____                            ____        _
 / ___| _ __   ___  _ __   __ _ | __ )  ___ | |_
 \___ \| '_ \ / _ \| '_ \ / _` ||  _ \ / _ \| __|
  ___) | |_) | (_) | | | | (_| || |_) | (_) | |_
 |____/| .__/ \___/|_| |_|\__, ||____/ \___/ \__|
       |_|                |___/
"""

# ---------------------------------------------------------------------------
# Subsystem Protocol -- every subsystem must satisfy this contract
# ---------------------------------------------------------------------------


@runtime_checkable
class Subsystem(Protocol):
    """Protocol that all SpongeBot subsystems must implement."""

    async def boot(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def health_check(self) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Boot status tracking
# ---------------------------------------------------------------------------


@dataclass
class SubsystemStatus:
    """Tracks the lifecycle state of a single subsystem."""

    name: str
    booted: bool = False
    healthy: bool = False
    boot_time_ms: float = 0.0
    error: Optional[str] = None
    is_stub: bool = False


# ---------------------------------------------------------------------------
# Stub implementations -- used when real modules are not yet available
# ---------------------------------------------------------------------------


class _StubSecurity:
    """Stub security vault -- passthrough encryption."""

    async def boot(self) -> None:
        logger.info("  SecurityVault (stub) ready")

    async def shutdown(self) -> None:
        pass

    async def health_check(self) -> dict[str, Any]:
        return {"status": "stub", "component": "security"}

    def encrypt(self, data: str) -> str:
        return data

    def decrypt(self, data: str) -> str:
        return data

    def audit_log(self, event: str, **details: Any) -> None:
        logger.debug("Audit (stub): %s %s", event, details)


class _StubMemory:
    """Stub memory -- simple in-memory list with keyword overlap recall."""

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []

    async def boot(self) -> None:
        logger.info("  Memory (stub) ready -- in-memory store")

    async def shutdown(self) -> None:
        pass

    async def health_check(self) -> dict[str, Any]:
        return {"status": "stub", "component": "memory", "entries": len(self._entries)}

    async def store(self, text: str, metadata: dict[str, Any] | None = None) -> None:
        self._entries.append(
            {"text": text, "metadata": metadata or {}, "ts": time.time()}
        )
        if len(self._entries) > 1000:
            self._entries = self._entries[-1000:]

    async def recall(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        query_words = set(query.lower().split())
        scored = []
        for entry in self._entries:
            overlap = len(query_words & set(entry["text"].lower().split()))
            if overlap > 0:
                scored.append((overlap, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:k]]


class _StubTokenSaver:
    """Stub token saver -- basic TTL cache and passthrough compression."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, float]] = {}
        self._ttl: float = 300.0

    async def boot(self) -> None:
        logger.info("  TokenSaver (stub) ready")

    async def shutdown(self) -> None:
        pass

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "stub",
            "component": "token_saver",
            "cache_size": len(self._cache),
        }

    def compress(self, text: str) -> str:
        """Compress prompt text. Stub returns as-is."""
        return text

    def check_cache(self, prompt_hash: str) -> Optional[str]:
        entry = self._cache.get(prompt_hash)
        if entry and (time.time() - entry[1]) < self._ttl:
            return entry[0]
        return None

    def cache_response(self, prompt_hash: str, response: str) -> None:
        self._cache[prompt_hash] = (response, time.time())


class _StubAbsorptionEngine:
    """Stub absorption engine -- records interactions but does not learn."""

    def __init__(self) -> None:
        self._interaction_count: int = 0

    async def boot(self) -> None:
        logger.info("  AbsorptionEngine (stub) ready")

    async def shutdown(self) -> None:
        pass

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "stub",
            "component": "absorption",
            "interactions": self._interaction_count,
        }

    async def absorb(
        self, user_input: str, response: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Absorb an interaction for pattern learning. Stub increments counter."""
        self._interaction_count += 1


class _StubSkillDAG:
    """Stub skill DAG -- returns empty skill matches."""

    async def boot(self) -> None:
        logger.info("  SkillDAG (stub) ready")

    async def shutdown(self) -> None:
        pass

    async def health_check(self) -> dict[str, Any]:
        return {"status": "stub", "component": "skills", "skill_count": 0}

    async def find_skills(
        self, user_input: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Find relevant skills for a user input. Stub returns empty list."""
        return []


class _StubLearningEngine:
    """Stub learning engine -- static tier, no progression."""

    def __init__(self) -> None:
        self._tier: str = "novice"
        self._xp: float = 0.0

    async def boot(self) -> None:
        logger.info("  LearningEngine (stub) ready")

    async def shutdown(self) -> None:
        pass

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "stub",
            "component": "learning",
            "tier": self._tier,
            "xp": self._xp,
        }

    async def update(
        self, interaction_result: dict[str, Any] | None = None
    ) -> None:
        """Update learning tier based on interaction. Stub adds token XP."""
        self._xp += 1.0


class _StubLLMClient:
    """Stub LLM client -- returns a canned response."""

    async def boot(self) -> None:
        logger.info("  LLMClient (stub) ready -- no API key configured")

    async def shutdown(self) -> None:
        pass

    async def health_check(self) -> dict[str, Any]:
        return {"status": "stub", "component": "llm", "connected": False}

    async def call(
        self,
        system_prompt: str,
        user_text: str,
        context: str = "",
        **kwargs: Any,
    ) -> str:
        """Call the LLM. Stub returns a placeholder response."""
        return (
            "I'm running in stub mode without an LLM connection. "
            "Please configure an API key to enable full responses."
        )


# ---------------------------------------------------------------------------
# Subsystem import helper (absorbed from IT_NEXUS _import_subsystem)
# ---------------------------------------------------------------------------


def _import_subsystem(
    module_path: str,
    class_name: str,
    stub_factory: type | callable,
    config: dict[str, Any],
    **kwargs: Any,
) -> tuple[Any, bool]:
    """Try importing a real subsystem module; fall back to stub.

    Args:
        module_path: Dotted import path (e.g. ``src.memory.store``).
        class_name: Class to import from the module.
        stub_factory: Callable that creates a stub instance (no args or config).
        config: The full configuration dict.
        **kwargs: Extra kwargs forwarded to ``cls.from_config`` or ``cls()``.

    Returns:
        Tuple of (instance, is_stub).
    """
    try:
        mod = __import__(module_path, fromlist=[class_name])
        cls = getattr(mod, class_name)

        if hasattr(cls, "from_config"):
            instance = cls.from_config(config, **kwargs)
        else:
            try:
                instance = cls(config, **kwargs)
            except TypeError:
                instance = cls(**kwargs)

        logger.info("  Loaded %s from %s", class_name, module_path)
        return instance, False

    except (ImportError, AttributeError, TypeError, Exception) as exc:
        logger.warning(
            "  Using stub for %s (%s: %s)", class_name, type(exc).__name__, exc
        )
        # Stubs take no args or config
        try:
            return stub_factory(config), True
        except TypeError:
            return stub_factory(), True


# ---------------------------------------------------------------------------
# SpongeBot Orchestrator
# ---------------------------------------------------------------------------


class SpongeBot:
    """Central brain orchestrator for the SpongeBot absorption pipeline.

    Boots all subsystems in dependency order, provides a unified ``process``
    method that chains the full pipeline, and shuts down gracefully in
    reverse order.
    """

    # Ordered subsystem definitions: (attr_name, step_label, module_path, class_name, stub)
    _BOOT_ORDER: list[tuple[str, str, str, str, type]] = [
        ("_security", "SecurityVault", "src.security.vault", "SecurityVault", _StubSecurity),
        ("_memory", "Memory", "src.memory.store", "MemoryStore", _StubMemory),
        ("_token_saver", "TokenSaver", "src.token_saver.saver", "TokenSaver", _StubTokenSaver),
        ("_absorption", "AbsorptionEngine", "src.absorption.engine", "AbsorptionEngine", _StubAbsorptionEngine),
        ("_skills", "SkillDAG", "src.skills.dag", "SkillDAG", _StubSkillDAG),
        ("_learning", "LearningEngine", "src.learning.engine", "LearningEngine", _StubLearningEngine),
        ("_llm_client", "LLMClient", "src.llm.client", "LLMClient", _StubLLMClient),
    ]

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        raw = config if config is not None else load_config()
        self._config: dict[str, Any] = _deep_merge(DEFAULT_CONFIG, raw)

        # Subsystem references -- populated during boot
        self._security: Any = None
        self._memory: Any = None
        self._token_saver: Any = None
        self._absorption: Any = None
        self._skills: Any = None
        self._learning: Any = None
        self._llm_client: Any = None

        # Boot tracking
        self._statuses: dict[str, SubsystemStatus] = {}
        self._booted: bool = False
        self._boot_time_ms: float = 0.0

    # ------------------------------------------------------------------ boot

    async def boot(self) -> None:
        """Boot all subsystems in dependency order with timing and fallbacks."""
        overall_start = time.monotonic()
        total = len(self._BOOT_ORDER)

        logger.info(_BANNER)
        logger.info("=" * 60)
        logger.info("SpongeBot booting...")
        logger.info("=" * 60)

        data_dir = Path(
            self._config.get("spongebot", {}).get("data_dir", str(PROJECT_ROOT / "data"))
        )
        data_dir.mkdir(parents=True, exist_ok=True)

        for idx, (attr, label, mod_path, cls_name, stub_cls) in enumerate(
            self._BOOT_ORDER, start=1
        ):
            step_start = time.monotonic()
            status = SubsystemStatus(name=label)

            try:
                # Build extra kwargs for subsystems that need references
                extra_kwargs: dict[str, Any] = {}
                if attr == "_memory" and self._security is not None:
                    extra_kwargs["security"] = self._security

                instance, is_stub = _import_subsystem(
                    mod_path, cls_name, stub_cls, self._config, **extra_kwargs
                )
                setattr(self, attr, instance)
                status.is_stub = is_stub

                # Boot the subsystem
                if hasattr(instance, "boot"):
                    await instance.boot()

                elapsed = (time.monotonic() - step_start) * 1000
                status.booted = True
                status.healthy = True
                status.boot_time_ms = elapsed

                tag = "STUB" if is_stub else "OK"
                logger.info(
                    "[%d/%d] Booting %-22s [%s %5.0fms]",
                    idx,
                    total,
                    label + "...",
                    tag,
                    elapsed,
                )

            except Exception as exc:
                elapsed = (time.monotonic() - step_start) * 1000
                status.error = str(exc)
                status.boot_time_ms = elapsed
                logger.error(
                    "[%d/%d] Booting %-22s [FAIL %5.0fms] %s",
                    idx,
                    total,
                    label + "...",
                    elapsed,
                    exc,
                )
                # Install the stub so the pipeline can still run degraded
                try:
                    fallback = stub_cls()
                except TypeError:
                    fallback = stub_cls(self._config)
                setattr(self, attr, fallback)
                status.is_stub = True
                if hasattr(fallback, "boot"):
                    try:
                        await fallback.boot()
                        status.booted = True
                    except Exception as boot_exc:
                        logger.warning(
                            "Stub %s boot failed: %s", label, boot_exc
                        )

            self._statuses[label] = status

        self._boot_time_ms = (time.monotonic() - overall_start) * 1000
        self._booted = True

        stubs = sum(1 for s in self._statuses.values() if s.is_stub)
        failures = sum(1 for s in self._statuses.values() if s.error)

        logger.info("=" * 60)
        logger.info(
            "SpongeBot READY in %.0fms  (%d/%d subsystems, %d stubs, %d errors)",
            self._boot_time_ms,
            total - failures,
            total,
            stubs,
            failures,
        )
        logger.info("=" * 60)

    # -------------------------------------------------------------- shutdown

    async def shutdown(self) -> None:
        """Graceful shutdown in reverse boot order."""
        if not self._booted:
            return

        logger.info("SpongeBot shutting down...")

        for attr, label, _, _, _ in reversed(self._BOOT_ORDER):
            subsystem = getattr(self, attr, None)
            if subsystem is None:
                continue

            try:
                if hasattr(subsystem, "shutdown"):
                    await subsystem.shutdown()
                logger.info("  Stopped %s", label)
            except Exception as exc:
                logger.warning("  Error stopping %s: %s", label, exc)

        self._booted = False
        logger.info("SpongeBot stopped.")

    # --------------------------------------------------------------- process

    async def process(self, user_input: str, session_id: str = "default") -> str:
        """Main absorption pipeline.

        Chains the full processing flow:
            1. TokenSaver: check response cache
            2. SkillDAG: find relevant skills
            3. Memory: recall context
            4. LLM: generate response with enriched prompt
            5. Absorption: learn from the interaction
            6. Learning: update progression tiers
            7. TokenSaver: cache the response

        Args:
            user_input: Raw user input text.
            session_id: Session identifier for multi-session support.

        Returns:
            The assistant's response string.
        """
        if not self._booted:
            raise RuntimeError("SpongeBot has not been booted. Call boot() first.")

        pipeline_start = time.monotonic()

        # ------ 1. TokenSaver: check cache ------
        prompt_hash = hashlib.sha256(
            f"{session_id}:{user_input}".encode()
        ).hexdigest()[:16]

        cached = self._token_saver.check_cache(prompt_hash)
        if cached is not None:
            logger.debug("Cache hit for session=%s", session_id)
            return cached

        # ------ 2. SkillDAG: find relevant skills ------
        matched_skills: list[dict[str, Any]] = []
        try:
            matched_skills = await self._skills.find_skills(user_input)
        except Exception as exc:
            logger.warning("SkillDAG lookup failed: %s", exc)

        # ------ 3. Memory: recall relevant context ------
        memories: list[dict[str, Any]] = []
        try:
            memories = await self._memory.recall(user_input, k=5)
        except Exception as exc:
            logger.warning("Memory recall failed: %s", exc)

        # ------ 4. Build enriched prompt and call LLM ------
        context_parts: list[str] = []

        if memories:
            mem_texts = [m.get("text", "") for m in memories if m.get("text")]
            if mem_texts:
                context_parts.append(
                    "Relevant memories:\n" + "\n".join(f"- {t}" for t in mem_texts)
                )

        if matched_skills:
            skill_names = [s.get("name", "unknown") for s in matched_skills]
            context_parts.append(f"Available skills: {', '.join(skill_names)}")

        context = "\n\n".join(context_parts)

        # Compress context through token saver
        compressed_context = self._token_saver.compress(context)

        system_prompt = (
            "You are SpongeBot, an absorption-based AI assistant. "
            "You learn from every interaction and continuously improve. "
            "Be helpful, precise, and curious."
        )

        try:
            response = await self._llm_client.call(
                system_prompt=system_prompt,
                user_text=user_input,
                context=compressed_context,
                session_id=session_id,
            )
        except Exception as exc:
            logger.error("LLM call failed: %s", exc)
            response = (
                "I encountered an error generating a response. "
                "Please try again in a moment."
            )

        # ------ 5. Absorption: learn from interaction ------
        try:
            await self._absorption.absorb(
                user_input=user_input,
                response=response,
                metadata={
                    "session_id": session_id,
                    "skills": [s.get("name") for s in matched_skills],
                    "memory_hits": len(memories),
                    "timestamp": time.time(),
                },
            )
        except Exception as exc:
            logger.warning("Absorption failed: %s", exc)

        # ------ 6. Learning: update tier progression ------
        try:
            await self._learning.update(
                interaction_result={
                    "user_input": user_input,
                    "response": response,
                    "skills_used": len(matched_skills),
                    "memory_recall_count": len(memories),
                }
            )
        except Exception as exc:
            logger.warning("Learning update failed: %s", exc)

        # ------ 7. TokenSaver: cache response ------
        try:
            self._token_saver.cache_response(prompt_hash, response)
        except Exception as exc:
            logger.warning("Response caching failed: %s", exc)

        # ------ 8. Store in memory ------
        try:
            await self._memory.store(
                f"User: {user_input}\nAssistant: {response}",
                metadata={
                    "session_id": session_id,
                    "timestamp": time.time(),
                },
            )
        except Exception as exc:
            logger.warning("Memory store failed: %s", exc)

        # Audit trail
        if hasattr(self._security, "audit_log"):
            elapsed_ms = (time.monotonic() - pipeline_start) * 1000
            self._security.audit_log(
                "process_complete",
                session_id=session_id,
                pipeline_ms=round(elapsed_ms, 1),
                cache_hit=False,
                skills_matched=len(matched_skills),
                memories_recalled=len(memories),
            )

        return response

    # ---------------------------------------------------------------- health

    async def health(self) -> dict[str, Any]:
        """Return health status of all subsystems.

        Returns a dict with overall status and per-subsystem details.
        """
        subsystem_health: dict[str, Any] = {}

        for attr, label, _, _, _ in self._BOOT_ORDER:
            subsystem = getattr(self, attr, None)
            if subsystem is None:
                subsystem_health[label] = {"status": "not_initialized"}
                continue

            try:
                if hasattr(subsystem, "health_check"):
                    check = await subsystem.health_check()
                else:
                    check = {"status": "no_health_check"}

                boot_status = self._statuses.get(label)
                if boot_status:
                    check["booted"] = boot_status.booted
                    check["boot_time_ms"] = boot_status.boot_time_ms
                    check["is_stub"] = boot_status.is_stub
                    if boot_status.error:
                        check["boot_error"] = boot_status.error

                subsystem_health[label] = check

            except Exception as exc:
                subsystem_health[label] = {"status": "error", "error": str(exc)}

        all_booted = all(
            s.booted for s in self._statuses.values()
        )
        any_errors = any(s.error for s in self._statuses.values())
        stub_count = sum(1 for s in self._statuses.values() if s.is_stub)

        if any_errors:
            overall = "degraded"
        elif stub_count == len(self._statuses):
            overall = "stub_only"
        elif stub_count > 0:
            overall = "partial"
        elif all_booted:
            overall = "healthy"
        else:
            overall = "unknown"

        return {
            "status": overall,
            "booted": self._booted,
            "boot_time_ms": self._boot_time_ms,
            "subsystem_count": len(self._BOOT_ORDER),
            "stub_count": stub_count,
            "subsystems": subsystem_health,
        }

    # ----------------------------------------------------------- convenience

    @property
    def is_booted(self) -> bool:
        """Whether the bot has completed its boot sequence."""
        return self._booted

    @property
    def config(self) -> dict[str, Any]:
        """The active configuration dict."""
        return self._config

    def __repr__(self) -> str:
        status = "booted" if self._booted else "not_booted"
        stubs = sum(1 for s in self._statuses.values() if s.is_stub)
        return f"<SpongeBot {status} subsystems={len(self._BOOT_ORDER)} stubs={stubs}>"
