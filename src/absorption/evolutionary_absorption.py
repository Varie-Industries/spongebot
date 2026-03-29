"""
Mode 5 -- Evolutionary Absorption.

Breeds new skills from existing ones using genetic operations:
crossover, mutation, and selection.  Claude-as-judge evaluates
fitness on a 1-10 scale; only candidates scoring >= MIN_FITNESS
survive.

Confidence for offspring = avg(parent confidences) * 0.8
(slightly discounted because they haven't been validated in the wild).
"""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any

logger = logging.getLogger("spongebot.absorption.evolutionary_absorption")

# ------------------------------------------------------------------
# LLM prompts
# ------------------------------------------------------------------

_CROSSOVER_PROMPT = """\
You are SpongeBot's Evolution Engine. Combine the best elements of \
two parent skills into a single offspring skill.

Parent A:
{parent_a_json}

Parent B:
{parent_b_json}

Create a new skill that combines complementary strengths from both \
parents. Respond with ONLY a JSON object (no markdown fences):
{{
  "name": "<snake_case_offspring_name>",
  "description": "<what the offspring does>",
  "parameters": [{{"name": "<p>", "type": "<t>", "required": true/false}}],
  "steps": ["<step>", ...],
  "prerequisites": ["<req>", ...],
  "tags": ["<tag>", ...]
}}
"""

_MUTATION_PROMPT = """\
You are SpongeBot's Mutation Engine. Take this skill and randomly \
alter ONE aspect to explore a new variation. You may:
- Add a new step
- Remove a redundant step
- Change a parameter
- Alter the approach in one step
- Add or change a prerequisite

Skill to mutate:
{skill_json}

Respond with ONLY the mutated JSON object (no markdown fences):
{{
  "name": "<name_with_v2_or_alt_suffix>",
  "description": "<updated description>",
  "parameters": [...],
  "steps": [...],
  "prerequisites": [...],
  "tags": [...]
}}
"""

_FITNESS_PROMPT = """\
You are SpongeBot's Fitness Judge. Evaluate this skill on a scale of \
1-10 for practical usefulness, clarity, and reusability.

Skill:
{skill_json}

Score criteria:
- Clarity (1-10): Are the steps clear and unambiguous?
- Usefulness (1-10): Would this skill solve real problems?
- Reusability (1-10): Can it be applied to many situations?
- Completeness (1-10): Are prerequisites and parameters well-defined?

Respond with ONLY a JSON object (no markdown fences):
{{
  "overall_score": <1-10>,
  "clarity": <1-10>,
  "usefulness": <1-10>,
  "reusability": <1-10>,
  "completeness": <1-10>,
  "rationale": "<one sentence justification>"
}}
"""


class EvolutionaryAbsorption:
    """Evolve new skills from existing parents using genetic operations.

    The evolutionary cycle per generation:
    1. **Select** parent pairs from the population
    2. **Crossover**: Combine elements from two parents
    3. **Mutate**: Randomly alter one aspect of the offspring
    4. **Evaluate**: Claude judges fitness (1-10)
    5. **Select survivors**: Keep offspring scoring >= MIN_FITNESS

    Parameters
    ----------
    config : dict
        SpongeBot configuration dictionary.
    llm_client : object, optional
        LLM client for crossover, mutation, and fitness evaluation.
    """

    MIN_FITNESS: int = 7
    DEFAULT_GENERATIONS: int = 5
    CONFIDENCE_DISCOUNT: float = 0.8

    def __init__(self, config: dict[str, Any], llm_client: Any | None = None) -> None:
        self._config = config
        self._absorption_cfg = config.get("absorption", {})
        self._min_fitness = self._absorption_cfg.get(
            "evolutionary_min_fitness", self.MIN_FITNESS
        )
        self._default_generations = self._absorption_cfg.get(
            "evolutionary_generations", self.DEFAULT_GENERATIONS
        )
        self._llm_client = llm_client

        # Statistics
        self._total_candidates = 0
        self._total_survivors = 0

        logger.debug(
            "EvolutionaryAbsorption initialised "
            "(min_fitness=%d, default_generations=%d)",
            self._min_fitness,
            self._default_generations,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def absorb(
        self,
        parent_skills: list[dict[str, Any]],
        generations: int | None = None,
        source_id: str | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Evolve new skills from *parent_skills* over multiple generations.

        Parameters
        ----------
        parent_skills : list[dict]
            Pool of existing skill dicts to breed from. Need at least 2.
        generations : int, optional
            Number of evolutionary generations. Defaults to config value.
        source_id : str, optional
            Identifier for tracing lineage.

        Returns
        -------
        list[dict]
            Surviving offspring skill dicts that passed fitness evaluation.
        """
        if len(parent_skills) < 2:
            logger.warning(
                "Need at least 2 parent skills for evolution, got %d",
                len(parent_skills),
            )
            return []

        num_generations = generations or self._default_generations
        source_label = source_id or "evolutionary"

        logger.info(
            "Starting evolution: %d parents, %d generations, min_fitness=%d",
            len(parent_skills),
            num_generations,
            self._min_fitness,
        )

        # The population starts as the parent skills
        population = list(parent_skills)
        all_survivors: list[dict[str, Any]] = []

        for gen in range(1, num_generations + 1):
            logger.info("Generation %d/%d (population: %d)", gen, num_generations, len(population))

            offspring = await self._breed_generation(population, gen, source_label)
            self._total_candidates += len(offspring)

            # Evaluate fitness and select survivors
            survivors = await self._select_survivors(offspring)
            self._total_survivors += len(survivors)

            logger.info(
                "Generation %d: %d offspring -> %d survivors",
                gen,
                len(offspring),
                len(survivors),
            )

            all_survivors.extend(survivors)

            # Add survivors to the population for the next generation
            population.extend(survivors)

        logger.info(
            "Evolution complete: %d total survivors across %d generations",
            len(all_survivors),
            num_generations,
        )
        return all_survivors

    # ------------------------------------------------------------------
    # Breeding
    # ------------------------------------------------------------------

    async def _breed_generation(
        self,
        population: list[dict[str, Any]],
        generation: int,
        source_label: str,
    ) -> list[dict[str, Any]]:
        """Breed one generation of offspring from the population.

        Creates len(population) // 2 offspring by selecting parent
        pairs, applying crossover, and then mutation.
        """
        offspring: list[dict[str, Any]] = []
        pairs = self._select_parents(population)

        for parent_a, parent_b in pairs:
            # Crossover
            child = await self._crossover(parent_a, parent_b)
            if child is None:
                continue

            # Mutation
            mutant = await self._mutate(child)
            if mutant is None:
                mutant = child

            # Set confidence as discounted average of parents
            parent_conf_a = parent_a.get("confidence", 0.5)
            parent_conf_b = parent_b.get("confidence", 0.5)
            avg_confidence = (parent_conf_a + parent_conf_b) / 2.0
            child_confidence = round(avg_confidence * self.CONFIDENCE_DISCOUNT, 4)

            now = time.time()
            skill = {
                "name": mutant.get("name", f"evolved_gen{generation}"),
                "description": mutant.get("description", ""),
                "type": "composed",
                "parameters": mutant.get("parameters", []),
                "steps": mutant.get("steps", []),
                "prerequisites": mutant.get("prerequisites", []),
                "confidence": child_confidence,
                "version": "0.1.0",
                "absorbed_from": source_label,
                "absorption_mode": "evolutionary",
                "created_at": now,
                "last_used": now,
                "use_count": 0,
                "tags": mutant.get("tags", []) + [f"gen_{generation}", "evolved"],
                # Lineage metadata
                "parent_a": parent_a.get("name", "unknown"),
                "parent_b": parent_b.get("name", "unknown"),
                "generation": generation,
            }
            offspring.append(skill)

        return offspring

    @staticmethod
    def _select_parents(
        population: list[dict[str, Any]],
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Select parent pairs using fitness-proportionate selection.

        Higher-confidence skills are more likely to be selected.
        """
        if len(population) < 2:
            return []

        # Weight by confidence (higher confidence = more likely parent)
        weights = [max(s.get("confidence", 0.5), 0.1) for s in population]
        total_weight = sum(weights)
        normalised = [w / total_weight for w in weights]

        num_pairs = max(1, len(population) // 2)
        pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []

        for _ in range(num_pairs):
            # Weighted random selection without replacement within a pair
            indices = list(range(len(population)))
            a_idx = random.choices(indices, weights=normalised, k=1)[0]

            # Select second parent (exclude first)
            remaining_indices = [i for i in indices if i != a_idx]
            remaining_weights = [normalised[i] for i in remaining_indices]
            total_remaining = sum(remaining_weights)
            if total_remaining == 0:
                remaining_weights = [1.0 / len(remaining_indices)] * len(remaining_indices)
            else:
                remaining_weights = [w / total_remaining for w in remaining_weights]

            b_idx = random.choices(remaining_indices, weights=remaining_weights, k=1)[0]
            pairs.append((population[a_idx], population[b_idx]))

        return pairs

    # ------------------------------------------------------------------
    # Genetic operations
    # ------------------------------------------------------------------

    async def _crossover(
        self,
        parent_a: dict[str, Any],
        parent_b: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Combine two parent skills into an offspring."""
        if self._llm_client is not None:
            return await self._llm_crossover(parent_a, parent_b)
        return self._deterministic_crossover(parent_a, parent_b)

    async def _llm_crossover(
        self,
        parent_a: dict[str, Any],
        parent_b: dict[str, Any],
    ) -> dict[str, Any] | None:
        """LLM-powered crossover."""
        # Compact parents for prompt
        pa = self._compact_skill(parent_a)
        pb = self._compact_skill(parent_b)
        prompt = _CROSSOVER_PROMPT.format(
            parent_a_json=json.dumps(pa, indent=2, default=str),
            parent_b_json=json.dumps(pb, indent=2, default=str),
        )
        return await self._llm_json_call(prompt, "crossover")

    @staticmethod
    def _deterministic_crossover(
        parent_a: dict[str, Any],
        parent_b: dict[str, Any],
    ) -> dict[str, Any]:
        """Fallback deterministic crossover: interleave steps."""
        steps_a = parent_a.get("steps", [])
        steps_b = parent_b.get("steps", [])

        # Interleave steps from both parents
        combined_steps: list[str] = []
        max_len = max(len(steps_a), len(steps_b))
        for i in range(max_len):
            if i < len(steps_a):
                combined_steps.append(steps_a[i])
            if i < len(steps_b) and (i >= len(steps_a) or steps_b[i] != steps_a[i]):
                combined_steps.append(steps_b[i])

        # Merge parameters (deduplicate by name)
        params_a = parent_a.get("parameters", [])
        params_b = parent_b.get("parameters", [])
        seen_params: set[str] = set()
        merged_params: list[dict[str, Any]] = []
        for p in params_a + params_b:
            pname = p.get("name", "")
            if pname not in seen_params:
                seen_params.add(pname)
                merged_params.append(p)

        name_a = parent_a.get("name", "a")
        name_b = parent_b.get("name", "b")
        return {
            "name": f"cross_{name_a}_{name_b}",
            "description": f"Crossover of {name_a} and {name_b}",
            "parameters": merged_params,
            "steps": combined_steps,
            "prerequisites": list(
                set(parent_a.get("prerequisites", []))
                | set(parent_b.get("prerequisites", []))
            ),
            "tags": list(set(parent_a.get("tags", []) + parent_b.get("tags", []))),
        }

    async def _mutate(self, skill: dict[str, Any]) -> dict[str, Any] | None:
        """Apply a random mutation to a skill."""
        if self._llm_client is not None:
            return await self._llm_mutate(skill)
        return self._deterministic_mutate(skill)

    async def _llm_mutate(self, skill: dict[str, Any]) -> dict[str, Any] | None:
        """LLM-powered mutation."""
        compact = self._compact_skill(skill)
        prompt = _MUTATION_PROMPT.format(
            skill_json=json.dumps(compact, indent=2, default=str),
        )
        return await self._llm_json_call(prompt, "mutation")

    @staticmethod
    def _deterministic_mutate(skill: dict[str, Any]) -> dict[str, Any]:
        """Fallback deterministic mutation: alter one random aspect."""
        mutated = {
            "name": skill.get("name", "mutated") + "_v2",
            "description": skill.get("description", ""),
            "parameters": list(skill.get("parameters", [])),
            "steps": list(skill.get("steps", [])),
            "prerequisites": list(skill.get("prerequisites", [])),
            "tags": list(skill.get("tags", [])),
        }

        steps = mutated["steps"]
        if steps:
            mutation_type = random.choice(["add", "remove", "modify"])
            if mutation_type == "add":
                steps.append("Validate results and handle edge cases")
            elif mutation_type == "remove" and len(steps) > 1:
                steps.pop(random.randrange(len(steps)))
            elif mutation_type == "modify":
                idx = random.randrange(len(steps))
                steps[idx] = f"[OPTIMISED] {steps[idx]}"

        return mutated

    # ------------------------------------------------------------------
    # Fitness evaluation
    # ------------------------------------------------------------------

    async def _select_survivors(
        self,
        offspring: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Evaluate fitness and keep offspring that meet the threshold."""
        survivors: list[dict[str, Any]] = []

        for child in offspring:
            score = await self._evaluate_fitness(child)
            if score >= self._min_fitness:
                child["fitness_score"] = score
                survivors.append(child)
                logger.debug(
                    "Offspring '%s' survived (fitness=%d)",
                    child.get("name", "?"),
                    score,
                )
            else:
                logger.debug(
                    "Offspring '%s' eliminated (fitness=%d < %d)",
                    child.get("name", "?"),
                    score,
                    self._min_fitness,
                )

        return survivors

    async def _evaluate_fitness(self, skill: dict[str, Any]) -> int:
        """Evaluate a skill's fitness score (1-10)."""
        if self._llm_client is not None:
            return await self._llm_evaluate(skill)
        return self._deterministic_evaluate(skill)

    async def _llm_evaluate(self, skill: dict[str, Any]) -> int:
        """LLM-powered fitness evaluation."""
        compact = self._compact_skill(skill)
        prompt = _FITNESS_PROMPT.format(
            skill_json=json.dumps(compact, indent=2, default=str),
        )
        result = await self._llm_json_call(prompt, "fitness")
        if result is not None and "overall_score" in result:
            score = result["overall_score"]
            if isinstance(score, (int, float)):
                return max(1, min(10, int(score)))
        return 5  # Default middle score on parse failure

    @staticmethod
    def _deterministic_evaluate(skill: dict[str, Any]) -> int:
        """Fallback heuristic fitness evaluation."""
        score = 5  # Base score

        # Bonus for having steps
        steps = skill.get("steps", [])
        if len(steps) >= 2:
            score += 1
        if len(steps) >= 4:
            score += 1

        # Bonus for having parameters
        if skill.get("parameters"):
            score += 1

        # Bonus for having a description
        if len(skill.get("description", "")) > 20:
            score += 1

        # Penalty for too many steps (overly complex)
        if len(steps) > 10:
            score -= 1

        return max(1, min(10, score))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compact_skill(skill: dict[str, Any]) -> dict[str, Any]:
        """Return a compact version of a skill for LLM prompts."""
        return {
            "name": skill.get("name", ""),
            "description": skill.get("description", ""),
            "parameters": skill.get("parameters", []),
            "steps": skill.get("steps", []),
            "prerequisites": skill.get("prerequisites", []),
            "tags": skill.get("tags", []),
        }

    async def _llm_json_call(
        self,
        prompt: str,
        operation: str,
    ) -> dict[str, Any] | None:
        """Make an LLM call expecting JSON output."""
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
            logger.warning(
                "Evolutionary %s JSON parse failed: %s", operation, exc
            )
            return None
        except Exception as exc:
            logger.warning(
                "LLM call failed during evolutionary %s: %s", operation, exc
            )
            return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Return health status for the evolutionary absorption mode."""
        return {
            "status": "ok",
            "mode": "evolutionary",
            "min_fitness": self._min_fitness,
            "default_generations": self._default_generations,
            "confidence_discount": self.CONFIDENCE_DISCOUNT,
            "total_candidates": self._total_candidates,
            "total_survivors": self._total_survivors,
            "survival_rate": (
                round(self._total_survivors / self._total_candidates, 3)
                if self._total_candidates > 0
                else 0.0
            ),
            "llm_available": self._llm_client is not None,
        }
