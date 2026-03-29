"""
Mode 4 -- Failure Absorption.

Captures failed task trajectories with error information and generates
anti-skills -- documented patterns of what NOT to do.  Anti-skills
reduce the confidence of related positive skills, acting as a negative
feedback signal in the skill DAG.

Anti-skill confidence: 0.7 (failures are highly informative -- a
failure tells you something definitive about what doesn't work).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger("spongebot.absorption.failure_absorption")

# ------------------------------------------------------------------
# LLM prompt
# ------------------------------------------------------------------

_FAILURE_ANALYSIS_PROMPT = """\
You are SpongeBot's Failure Absorption Engine. Analyse the following \
failed task trajectory and error information to generate an \
"anti-skill" -- a documented pattern of what NOT to do.

Error information:
{error_info}

Failed trajectory ({step_count} steps):
{trajectory_json}

Respond with ONLY a JSON object (no markdown fences):
{{
  "name": "avoid_<descriptive_name>",
  "description": "<what went wrong and why, one sentence>",
  "failure_mode": "<category: wrong_tool | bad_params | missing_prereq | logic_error | timeout | permission | other>",
  "root_cause": "<the fundamental reason for failure>",
  "warning_signs": ["<observable indicator before failure>", ...],
  "avoidance_steps": ["<what to do instead>", ...],
  "related_positive_skills": ["<skill_name that should be penalised>", ...],
  "tags": ["<tag>", ...]
}}
"""


class FailureAbsorption:
    """Generate anti-skills from failed task trajectories.

    Anti-skills serve as negative knowledge: documented patterns of
    what NOT to do.  When an anti-skill is created, related positive
    skills have their confidence reduced by ``CONFIDENCE_PENALTY``.

    Parameters
    ----------
    config : dict
        SpongeBot configuration dictionary.
    llm_client : object, optional
        LLM client for failure analysis.
    skill_dag : object, optional
        Reference to the skill DAG for confidence adjustments.
    """

    INITIAL_CONFIDENCE: float = 0.7
    CONFIDENCE_PENALTY: float = 0.1

    def __init__(
        self,
        config: dict[str, Any],
        llm_client: Any | None = None,
        skill_dag: Any | None = None,
    ) -> None:
        self._config = config
        self._absorption_cfg = config.get("absorption", {})
        self._initial_confidence = self._absorption_cfg.get(
            "initial_confidence", {}
        ).get("failure", self.INITIAL_CONFIDENCE)
        self._llm_client = llm_client
        self._skill_dag = skill_dag

        # Track failure statistics
        self._failure_count = 0
        self._penalty_applications = 0

        logger.debug(
            "FailureAbsorption initialised (confidence=%.2f, penalty=%.2f)",
            self._initial_confidence,
            self.CONFIDENCE_PENALTY,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def absorb(
        self,
        trajectory: list[dict[str, Any]],
        error_info: str = "",
        source_id: str | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Generate anti-skills from a failed trajectory.

        Parameters
        ----------
        trajectory : list[dict]
            The failed task trajectory (same format as experience mode).
        error_info : str
            Human-readable or machine-readable error description.
        source_id : str, optional
            Identifier for the source task/session.

        Returns
        -------
        list[dict]
            Anti-skill dicts with ``type="anti_skill"``.
        """
        if not trajectory and not error_info:
            logger.warning("Empty trajectory and no error info -- nothing to absorb.")
            return []

        source_label = source_id or f"failure_{self._failure_count}"
        self._failure_count += 1

        logger.info(
            "Absorbing failure: %s (%d steps, error: %s)",
            source_label,
            len(trajectory),
            error_info[:120] if error_info else "<none>",
        )

        # Analyse the failure
        anti_skill = await self._analyse_failure(trajectory, error_info, source_label)
        if anti_skill is None:
            logger.warning("Failed to generate anti-skill from %s", source_label)
            return []

        # Apply confidence penalties to related positive skills
        related = anti_skill.get("_related_positive_skills", [])
        await self._apply_penalties(related)

        skills = [anti_skill]
        logger.info(
            "Generated anti-skill '%s' from failure %s (penalised %d related skills)",
            anti_skill["name"],
            source_label,
            len(related),
        )
        return skills

    # ------------------------------------------------------------------
    # Failure analysis
    # ------------------------------------------------------------------

    async def _analyse_failure(
        self,
        trajectory: list[dict[str, Any]],
        error_info: str,
        source_label: str,
    ) -> dict[str, Any] | None:
        """Analyse a failure and produce an anti-skill dict."""
        if self._llm_client is not None:
            analysis = await self._llm_analyse(trajectory, error_info)
        else:
            analysis = self._deterministic_analyse(trajectory, error_info)

        if analysis is None:
            return None

        # Extract related skills before building the final dict
        related_positive = analysis.pop("related_positive_skills", [])

        now = time.time()
        return {
            "name": analysis.get("name", f"avoid_{source_label}"),
            "description": analysis.get("description", error_info[:200] if error_info else "Unknown failure"),
            "type": "anti_skill",
            "parameters": [],
            "steps": analysis.get("avoidance_steps", []),
            "prerequisites": [],
            "confidence": self._initial_confidence,
            "version": "0.1.0",
            "absorbed_from": source_label,
            "absorption_mode": "failure",
            "created_at": now,
            "last_used": now,
            "use_count": 0,
            "tags": analysis.get("tags", ["failure", "anti-skill"]),
            # Anti-skill-specific metadata
            "failure_mode": analysis.get("failure_mode", "other"),
            "root_cause": analysis.get("root_cause", ""),
            "warning_signs": analysis.get("warning_signs", []),
            # Internal: used for penalty application, not persisted
            "_related_positive_skills": related_positive,
        }

    async def _llm_analyse(
        self,
        trajectory: list[dict[str, Any]],
        error_info: str,
    ) -> dict[str, Any] | None:
        """Use the LLM to analyse the failure."""
        # Compact the trajectory for the prompt
        compact_steps = []
        for s in trajectory:
            if not isinstance(s, dict):
                continue
            output = s.get("output", {})
            output_str = json.dumps(output, default=str)
            if len(output_str) > 300:
                output_str = output_str[:300] + "...<truncated>"
            compact_steps.append({
                "tool": s.get("tool", "unknown"),
                "input": s.get("input", {}),
                "output": output_str,
                "error": s.get("error", None),
            })

        trajectory_json = json.dumps(compact_steps, indent=2, default=str)
        prompt = _FAILURE_ANALYSIS_PROMPT.format(
            error_info=error_info or "No explicit error message available.",
            step_count=len(compact_steps),
            trajectory_json=trajectory_json,
        )

        try:
            response = await self._llm_client.generate(prompt)  # type: ignore[union-attr]
            text = response if isinstance(response, str) else str(response)
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[: text.rfind("```")]
            return json.loads(text.strip())
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            logger.warning("Failure analysis JSON parse failed: %s", exc)
            return None
        except Exception as exc:
            logger.warning("LLM call failed during failure absorption: %s", exc)
            return None

    @staticmethod
    def _deterministic_analyse(
        trajectory: list[dict[str, Any]],
        error_info: str,
    ) -> dict[str, Any]:
        """Fallback deterministic failure analysis."""
        tool_names = [
            s.get("tool", "unknown") for s in trajectory if isinstance(s, dict)
        ]

        # Find the step that likely caused the failure (last step or one with error)
        failing_tool = "unknown"
        for step in reversed(trajectory):
            if isinstance(step, dict):
                if step.get("error"):
                    failing_tool = step.get("tool", "unknown")
                    break
                failing_tool = step.get("tool", "unknown")

        return {
            "name": f"avoid_{failing_tool}_failure",
            "description": error_info[:200] if error_info else f"Failure in {failing_tool}",
            "failure_mode": "other",
            "root_cause": error_info[:300] if error_info else "Unknown root cause",
            "warning_signs": [],
            "avoidance_steps": [
                f"Verify preconditions before calling {failing_tool}",
                "Check input parameters match expected schema",
                "Ensure required resources are available",
            ],
            "related_positive_skills": [
                name for name in tool_names if name != "unknown"
            ],
            "tags": ["failure", "anti-skill", "auto-analysed"],
        }

    # ------------------------------------------------------------------
    # Confidence penalty application
    # ------------------------------------------------------------------

    async def _apply_penalties(self, related_skill_names: list[str]) -> None:
        """Reduce confidence of related positive skills by CONFIDENCE_PENALTY.

        This is the negative feedback mechanism: when something fails,
        related positive skills become slightly less trusted.
        """
        if not related_skill_names:
            return

        if self._skill_dag is None:
            logger.debug(
                "No skill_dag available -- cannot apply penalties to %d related skills",
                len(related_skill_names),
            )
            return

        for skill_name in related_skill_names:
            try:
                if hasattr(self._skill_dag, "adjust_confidence"):
                    await self._skill_dag.adjust_confidence(
                        skill_name, -self.CONFIDENCE_PENALTY
                    )
                    self._penalty_applications += 1
                    logger.debug(
                        "Penalised skill '%s' by -%.2f",
                        skill_name,
                        self.CONFIDENCE_PENALTY,
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to penalise skill '%s': %s",
                    skill_name,
                    exc,
                )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Return health status for the failure absorption mode."""
        return {
            "status": "ok",
            "mode": "failure",
            "initial_confidence": self._initial_confidence,
            "confidence_penalty": self.CONFIDENCE_PENALTY,
            "failures_absorbed": self._failure_count,
            "penalties_applied": self._penalty_applications,
            "llm_available": self._llm_client is not None,
            "skill_dag_connected": self._skill_dag is not None,
        }
