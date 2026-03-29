"""
Absorption Engine -- Main Orchestrator.

Routes incoming sources to the appropriate absorption mode, manages
lifecycle for all six sub-engines, and exposes a unified ``absorb()``
API.  This is the stomach of the SpongeBot: everything enters here.

Modes:
    1. agent        -- Extract skills from agent capability manifests
    2. document     -- Parse docs/code into actionable skill templates
    3. experience   -- Distill successful task trajectories into plans
    4. failure      -- Generate anti-skills from failed trajectories
    5. evolutionary -- Breed new skills via genetic operations
    6. federated    -- Share encrypted metadata between instances
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.absorption.agent_absorption import AgentAbsorption
from src.absorption.document_absorption import DocumentAbsorption
from src.absorption.evolutionary_absorption import EvolutionaryAbsorption
from src.absorption.experience_absorption import ExperienceAbsorption
from src.absorption.failure_absorption import FailureAbsorption
from src.absorption.federated_absorption import FederatedAbsorption

logger = logging.getLogger("spongebot.absorption.engine")


class AbsorptionEngine:
    """Central orchestrator for all six absorption modes.

    Parameters
    ----------
    config : dict
        SpongeBot configuration dictionary. The ``absorption`` section
        is read for mode-specific tunables (confidence defaults,
        evolutionary generations, etc.).
    llm_client : object, optional
        An LLM client instance (e.g. Anthropic SDK client) used by
        modes that require Claude for skill extraction / evaluation.
    skill_dag : object, optional
        Reference to the skill DAG for dependency lookups and
        confidence adjustments (used by failure mode).
    memory : object, optional
        Memory backend for storing / retrieving absorbed skills.
    """

    MODES: list[str] = [
        "agent",
        "document",
        "experience",
        "failure",
        "evolutionary",
        "federated",
    ]

    def __init__(
        self,
        config: dict[str, Any],
        llm_client: Any | None = None,
        skill_dag: Any | None = None,
        memory: Any | None = None,
    ) -> None:
        self._config = config
        self._absorption_cfg = config.get("absorption", {})
        self._llm_client = llm_client
        self._skill_dag = skill_dag
        self._memory = memory
        self._booted = False

        # Instantiate all six mode handlers
        self._modes: dict[str, Any] = {
            "agent": AgentAbsorption(config, llm_client),
            "document": DocumentAbsorption(config, llm_client),
            "experience": ExperienceAbsorption(config, llm_client),
            "failure": FailureAbsorption(config, llm_client, skill_dag),
            "evolutionary": EvolutionaryAbsorption(config, llm_client),
            "federated": FederatedAbsorption(config),
        }

        # Absorption statistics
        self._stats: dict[str, int] = {mode: 0 for mode in self.MODES}
        self._total_skills_absorbed = 0

        logger.info(
            "AbsorptionEngine initialised with %d modes: %s",
            len(self._modes),
            ", ".join(self.MODES),
        )

    # ------------------------------------------------------------------
    # Core absorption API
    # ------------------------------------------------------------------

    async def absorb(self, source: Any, mode: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Absorb from a source using the specified mode.

        Parameters
        ----------
        source : Any
            The input to absorb. Type depends on the mode:
            - ``agent``: dict (agent manifest)
            - ``document``: str (document content)
            - ``experience``: list[dict] (task trajectory)
            - ``failure``: list[dict] (failed trajectory; also pass ``error_info``)
            - ``evolutionary``: list[dict] (parent skills)
            - ``federated``: list[dict] (remote metadata for import)
                             or list[dict] (local skills for export)
        mode : str
            One of the six absorption modes.
        **kwargs
            Additional keyword arguments forwarded to the mode handler.

        Returns
        -------
        list[dict]
            List of skill dicts produced by the absorption.

        Raises
        ------
        ValueError
            If *mode* is not a recognised absorption mode.
        RuntimeError
            If the engine has not been booted.
        """
        if mode not in self.MODES:
            raise ValueError(
                f"Unknown absorption mode {mode!r}. "
                f"Valid modes: {', '.join(self.MODES)}"
            )

        handler = self._modes[mode]
        start = time.monotonic()

        logger.info("Absorbing via mode=%s", mode)

        try:
            skills = await handler.absorb(source, **kwargs)
        except Exception:
            logger.exception("Absorption failed for mode=%s", mode)
            raise

        elapsed = time.monotonic() - start
        self._stats[mode] += len(skills)
        self._total_skills_absorbed += len(skills)

        logger.info(
            "Absorbed %d skills via mode=%s in %.2fs",
            len(skills),
            mode,
            elapsed,
        )
        return skills

    async def absorb_auto(self, source: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Auto-detect the best absorption mode for *source* and absorb.

        Detection heuristics:
        - ``dict`` with ``"tools"`` or ``"capabilities"`` key -> ``agent``
        - ``str`` -> ``document``
        - ``list[dict]`` with ``"tool"`` keys -> ``experience``
        - ``list[dict]`` with ``"error"`` or ``error_info`` kwarg -> ``failure``
        - ``list[dict]`` with ``"confidence"`` keys -> ``evolutionary``
        - ``list[dict]`` with ``"sha256_hash"`` keys -> ``federated``

        Parameters
        ----------
        source : Any
            The input to absorb.
        **kwargs
            Additional keyword arguments forwarded to the mode handler.

        Returns
        -------
        list[dict]
            Skill dicts from the detected mode.

        Raises
        ------
        TypeError
            If the source type cannot be auto-detected.
        """
        mode = self._detect_mode(source, **kwargs)
        logger.info("Auto-detected absorption mode: %s", mode)
        return await self.absorb(source, mode, **kwargs)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def boot(self) -> None:
        """Initialise all mode handlers and mark the engine as ready."""
        if self._booted:
            logger.debug("AbsorptionEngine already booted, skipping.")
            return

        logger.info("Booting AbsorptionEngine...")

        for name, handler in self._modes.items():
            if hasattr(handler, "boot"):
                await handler.boot()
                logger.debug("Booted mode handler: %s", name)

        self._booted = True
        logger.info("AbsorptionEngine boot complete.")

    async def shutdown(self) -> None:
        """Gracefully shut down all mode handlers."""
        logger.info("Shutting down AbsorptionEngine...")

        for name, handler in self._modes.items():
            if hasattr(handler, "shutdown"):
                await handler.shutdown()
                logger.debug("Shut down mode handler: %s", name)

        self._booted = False
        logger.info(
            "AbsorptionEngine shutdown complete. Total skills absorbed: %d",
            self._total_skills_absorbed,
        )

    async def health_check(self) -> dict[str, Any]:
        """Return the health status of the engine and all mode handlers.

        Returns
        -------
        dict
            Health report with per-mode status, stats, and overall
            engine readiness.
        """
        mode_health: dict[str, dict[str, Any]] = {}
        for name, handler in self._modes.items():
            if hasattr(handler, "health_check"):
                mode_health[name] = await handler.health_check()
            else:
                mode_health[name] = {"status": "ok", "has_health_check": False}

        return {
            "engine": "absorption",
            "booted": self._booted,
            "modes": mode_health,
            "stats": dict(self._stats),
            "total_skills_absorbed": self._total_skills_absorbed,
            "llm_client_connected": self._llm_client is not None,
            "memory_connected": self._memory is not None,
            "skill_dag_connected": self._skill_dag is not None,
        }

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict[str, int]:
        """Per-mode absorption counts."""
        return dict(self._stats)

    @property
    def total_absorbed(self) -> int:
        """Total number of skills absorbed across all modes."""
        return self._total_skills_absorbed

    def get_mode(self, name: str) -> Any:
        """Return a mode handler by name for direct access.

        Parameters
        ----------
        name : str
            Mode name (e.g. ``"agent"``, ``"failure"``).

        Returns
        -------
        object
            The mode handler instance.

        Raises
        ------
        KeyError
            If the mode name is not recognised.
        """
        if name not in self._modes:
            raise KeyError(
                f"Unknown mode {name!r}. Available: {', '.join(self._modes)}"
            )
        return self._modes[name]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_mode(source: Any, **kwargs: Any) -> str:
        """Heuristically detect the absorption mode for *source*."""
        # Agent manifest (dict with tools/capabilities)
        if isinstance(source, dict):
            if "tools" in source or "capabilities" in source or "manifest" in source:
                return "agent"
            # Fallback: treat unknown dicts as agent manifests
            return "agent"

        # Raw document content
        if isinstance(source, str):
            return "document"

        # List-based sources
        if isinstance(source, list) and source:
            first = source[0] if isinstance(source[0], dict) else {}

            # Failure: explicit error_info kwarg or items with "error" key
            if kwargs.get("error_info") or first.get("error"):
                return "failure"

            # Federated: items with sha256_hash (metadata exchange)
            if "sha256_hash" in first:
                return "federated"

            # Evolutionary: items with confidence (parent skills)
            if "confidence" in first and "steps" in first:
                return "evolutionary"

            # Experience: items with "tool" key (trajectory steps)
            if "tool" in first:
                return "experience"

        raise TypeError(
            f"Cannot auto-detect absorption mode for source type "
            f"{type(source).__name__}. Specify mode explicitly."
        )
