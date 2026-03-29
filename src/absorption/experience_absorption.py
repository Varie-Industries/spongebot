"""
Mode 3 -- Experience Absorption.

Captures successful task trajectories (sequences of tool calls and
their results) and distils them into reusable plan templates.  The
Token Saver L3 (AgenticPlanCache) detects and caches recurring
patterns automatically.

Initial confidence: 0.6 (proven by at least one successful execution,
so higher than agent/document modes).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger("spongebot.absorption.experience_absorption")

# ------------------------------------------------------------------
# LLM prompt
# ------------------------------------------------------------------

_DISTILLATION_PROMPT = """\
You are SpongeBot's Absorption Engine. Analyse the following \
successful task trajectory (a sequence of tool calls with inputs and \
outputs) and distil it into a reusable plan template.

Trajectory ({step_count} steps):
{trajectory_json}

Respond with ONLY a JSON object (no markdown fences):
{{
  "name": "<snake_case_plan_name>",
  "description": "<what this plan achieves, one sentence>",
  "parameters": [
    {{"name": "<slot>", "type": "<python_type>", "required": true/false}}
  ],
  "steps": [
    "<step 1 with {{parameter_slots}}>",
    "<step 2>",
    ...
  ],
  "decision_points": [
    {{"condition": "<when to branch>", "action": "<what to do>"}}
  ],
  "prerequisites": ["<prerequisite>", ...],
  "tags": ["<tag>", ...]
}}

Identify the KEY decision points where the trajectory made important \
choices. Replace concrete values with parameter slots (e.g. \
``{{filename}}``) so the plan is reusable for similar tasks.
"""


class ExperienceAbsorption:
    """Distil successful task trajectories into reusable skill templates.

    A trajectory is a list of step dicts, each containing:
    - ``tool``: name of the tool invoked
    - ``input``: dict of input parameters
    - ``output``: dict or str of the result

    Parameters
    ----------
    config : dict
        SpongeBot configuration dictionary.
    llm_client : object, optional
        LLM client for distilling trajectories.
    """

    INITIAL_CONFIDENCE: float = 0.6

    def __init__(self, config: dict[str, Any], llm_client: Any | None = None) -> None:
        self._config = config
        self._absorption_cfg = config.get("absorption", {})
        self._initial_confidence = self._absorption_cfg.get(
            "initial_confidence", {}
        ).get("experience", self.INITIAL_CONFIDENCE)
        self._llm_client = llm_client

        logger.debug(
            "ExperienceAbsorption initialised (confidence=%.2f)",
            self._initial_confidence,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def absorb(
        self,
        trajectory: list[dict[str, Any]],
        source_id: str | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Distil a successful task trajectory into reusable skills.

        Parameters
        ----------
        trajectory : list[dict]
            Ordered list of step dicts. Each step should contain
            ``tool``, ``input``, and ``output`` keys.
        source_id : str, optional
            Identifier for the source task/session.

        Returns
        -------
        list[dict]
            Skill dicts distilled from the trajectory.
        """
        if not trajectory:
            logger.warning("Empty trajectory provided, nothing to absorb.")
            return []

        source_label = source_id or f"trajectory_{len(trajectory)}_steps"
        logger.info(
            "Absorbing trajectory: %s (%d steps)",
            source_label,
            len(trajectory),
        )

        # Validate trajectory structure
        valid_steps = self._validate_trajectory(trajectory)
        if not valid_steps:
            logger.warning("No valid steps in trajectory %s", source_label)
            return []

        # Detect sub-patterns (recurring subsequences)
        patterns = self._detect_patterns(valid_steps)
        logger.info(
            "Detected %d sub-patterns in trajectory",
            len(patterns),
        )

        # Main distillation: full trajectory -> composed skill
        skills: list[dict[str, Any]] = []
        main_skill = await self._distill(valid_steps, source_label)
        if main_skill is not None:
            main_skill["type"] = "composed" if len(valid_steps) > 2 else "atomic"
            skills.append(main_skill)

        # Distil sub-patterns into atomic skills
        for i, pattern in enumerate(patterns):
            sub_skill = await self._distill(
                pattern, f"{source_label}_sub_{i}"
            )
            if sub_skill is not None:
                sub_skill["type"] = "atomic"
                skills.append(sub_skill)

        logger.info(
            "Absorbed %d skills from trajectory %s",
            len(skills),
            source_label,
        )
        return skills

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_trajectory(trajectory: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter trajectory to steps with the required keys."""
        valid: list[dict[str, Any]] = []
        for step in trajectory:
            if not isinstance(step, dict):
                continue
            if "tool" not in step:
                continue
            valid.append({
                "tool": step["tool"],
                "input": step.get("input", {}),
                "output": step.get("output", {}),
            })
        return valid

    # ------------------------------------------------------------------
    # Pattern detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_patterns(
        steps: list[dict[str, Any]],
        min_length: int = 2,
        min_occurrences: int = 2,
    ) -> list[list[dict[str, Any]]]:
        """Detect recurring tool-call subsequences in the trajectory.

        Returns sub-trajectories that appear at least *min_occurrences*
        times and are at least *min_length* steps long.
        """
        if len(steps) < min_length * min_occurrences:
            return []

        # Build tool-name sequence for pattern matching
        tool_seq = [s["tool"] for s in steps]
        patterns: list[list[dict[str, Any]]] = []
        seen_signatures: set[str] = set()

        for length in range(min_length, len(steps) // min_occurrences + 1):
            for start in range(len(tool_seq) - length + 1):
                window = tuple(tool_seq[start : start + length])
                signature = "|".join(window)

                if signature in seen_signatures:
                    continue

                # Count occurrences
                count = 0
                for j in range(len(tool_seq) - length + 1):
                    if tuple(tool_seq[j : j + length]) == window:
                        count += 1

                if count >= min_occurrences:
                    seen_signatures.add(signature)
                    patterns.append(steps[start : start + length])

        return patterns

    # ------------------------------------------------------------------
    # Distillation
    # ------------------------------------------------------------------

    async def _distill(
        self,
        steps: list[dict[str, Any]],
        source_label: str,
    ) -> dict[str, Any] | None:
        """Distil a sequence of steps into a single skill dict."""
        if self._llm_client is not None:
            skill_body = await self._llm_distill(steps)
        else:
            skill_body = self._deterministic_distill(steps)

        if skill_body is None:
            return None

        now = time.time()
        return {
            "name": skill_body.get("name", f"plan_{source_label}"),
            "description": skill_body.get("description", ""),
            "type": "composed",
            "parameters": skill_body.get("parameters", []),
            "steps": skill_body.get("steps", [s["tool"] for s in steps]),
            "prerequisites": skill_body.get("prerequisites", []),
            "confidence": self._initial_confidence,
            "version": "0.1.0",
            "absorbed_from": source_label,
            "absorption_mode": "experience",
            "created_at": now,
            "last_used": now,
            "use_count": 0,
            "tags": skill_body.get("tags", ["experience", "trajectory"]),
        }

    async def _llm_distill(self, steps: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Use the LLM to distil steps into a skill template."""
        # Truncate large outputs to keep prompt manageable
        compact_steps = []
        for s in steps:
            output = s.get("output", {})
            output_str = json.dumps(output, default=str)
            if len(output_str) > 500:
                output_str = output_str[:500] + "...<truncated>"
            compact_steps.append({
                "tool": s["tool"],
                "input": s.get("input", {}),
                "output": output_str,
            })

        trajectory_json = json.dumps(compact_steps, indent=2, default=str)
        prompt = _DISTILLATION_PROMPT.format(
            step_count=len(steps),
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
            logger.warning("LLM distillation JSON parse failed: %s", exc)
            return None
        except Exception as exc:
            logger.warning("LLM call failed during experience absorption: %s", exc)
            return None

    @staticmethod
    def _deterministic_distill(steps: list[dict[str, Any]]) -> dict[str, Any]:
        """Fallback: generate a basic plan from tool sequence."""
        tool_names = [s["tool"] for s in steps]
        unique_tools = list(dict.fromkeys(tool_names))
        return {
            "name": f"plan_{'_'.join(unique_tools[:3])}",
            "description": f"Plan using tools: {', '.join(unique_tools)}",
            "parameters": [],
            "steps": [
                f"Step {i + 1}: Call {s['tool']} with appropriate parameters"
                for i, s in enumerate(steps)
            ],
            "prerequisites": [],
            "tags": ["experience", "auto-distilled"],
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Return health status for the experience absorption mode."""
        return {
            "status": "ok",
            "mode": "experience",
            "initial_confidence": self._initial_confidence,
            "llm_available": self._llm_client is not None,
        }
