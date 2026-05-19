"""
Learning Engine -- 3-tier nested learning system with automatic promotion.

Tier 1 (Fast)   -> Per-interaction working memory.  Discarded at session end
                   unless promoted.  Pure in-memory dict keyed by session_id.

Tier 2 (Medium) -> Session-level pattern templates.  Promoted after 3+ similar
                   patterns appear in Tier 1.  Stored in an in-memory list
                   (optionally backed by SQLite via the memory subsystem).

Tier 3 (Slow)   -> Permanent core capabilities.  Promoted after 3+ successful
                   uses across sessions.  Written to the encrypted Skill DAG
                   as proven, battle-tested skills.

The engine owns no persistence itself -- Tier 2 patterns are persisted through
the memory subsystem (if available), and Tier 3 goes straight into the DAG.

Absorbed from IT_NEXUS cortex.py patterns: config-driven construction,
async boot/shutdown lifecycle, subsystem dependency injection.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

logger = logging.getLogger("spongebot.learning")

# ---------------------------------------------------------------------------
# Similarity helpers
# ---------------------------------------------------------------------------


def _pattern_fingerprint(interaction: dict[str, Any]) -> str:
    """Derive a coarse fingerprint from an interaction for grouping.

    Uses a normalised hash of the ``type`` and ``intent`` fields (if present),
    falling back to a keyword bag from ``user_input``.
    """
    parts: list[str] = []

    if "type" in interaction:
        parts.append(str(interaction["type"]).lower().strip())
    if "intent" in interaction:
        parts.append(str(interaction["intent"]).lower().strip())

    if not parts:
        # Fallback: first 5 significant words from user_input
        raw = str(interaction.get("user_input", "")).lower()
        words = [w for w in raw.split() if len(w) > 2][:5]
        parts.extend(sorted(words))

    joined = "|".join(parts)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def _word_overlap_similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Jaccard similarity between word bags of two interactions."""
    text_a = " ".join(str(v) for v in a.values()).lower()
    text_b = " ".join(str(v) for v in b.values()).lower()

    words_a = set(text_a.split())
    words_b = set(text_b.split())

    if not words_a or not words_b:
        return 0.0

    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# Tier 2 pattern template
# ---------------------------------------------------------------------------


def _make_pattern(
    fingerprint: str,
    interactions: list[dict[str, Any]],
    *,
    session_count: int = 1,
) -> dict[str, Any]:
    """Build a Tier 2 pattern template from a batch of similar interactions."""
    return {
        "fingerprint": fingerprint,
        "example": interactions[0] if interactions else {},
        "occurrence_count": len(interactions),
        "session_count": session_count,
        "success_count": 0,
        "created_at": time.time(),
        "last_seen": time.time(),
    }


# ---------------------------------------------------------------------------
# LearningEngine
# ---------------------------------------------------------------------------


class LearningEngine:
    """3-tier nested learning system with automatic promotion.

    Parameters
    ----------
    config : dict
        Full SpongeBot configuration.  The ``learning`` section is used:
        - ``tier1_promotion_threshold`` (default 3)
        - ``tier2_promotion_threshold`` (default 3)
    skill_dag : SkillDAG | None
        Optional Skill DAG instance for Tier 3 promotion.
    memory : Any | None
        Optional memory subsystem for Tier 2 persistence.
    """

    def __init__(
        self,
        config: dict[str, Any],
        skill_dag: Any | None = None,
        memory: Any | None = None,
    ) -> None:
        learning_cfg = config.get("learning", {})

        self._tier1_threshold: int = learning_cfg.get("tier1_promotion_threshold", 3)
        self._tier2_threshold: int = learning_cfg.get("tier2_promotion_threshold", 3)

        self._skill_dag = skill_dag
        self._memory = memory

        # Tier 1: session_id -> list of interaction dicts
        self._tier1: dict[str, list[dict[str, Any]]] = {}

        # Tier 2: list of pattern templates
        self._tier2_patterns: list[dict[str, Any]] = []

        # Metrics
        self._total_learned: int = 0
        self._promotions_t1_to_t2: int = 0
        self._promotions_t2_to_t3: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def boot(self) -> None:
        """Initialise the learning engine."""
        logger.info(
            "LearningEngine booted (T1 threshold=%d, T2 threshold=%d).",
            self._tier1_threshold,
            self._tier2_threshold,
        )

    async def shutdown(self) -> None:
        """Clean up -- consolidate any remaining sessions."""
        active_sessions = list(self._tier1.keys())
        for session_id in active_sessions:
            try:
                await self.consolidate_session(session_id)
            except Exception as exc:
                logger.warning("Failed to consolidate session '%s' on shutdown: %s", session_id, exc)

        logger.info(
            "LearningEngine shut down (learned=%d, T1->T2=%d, T2->T3=%d).",
            self._total_learned,
            self._promotions_t1_to_t2,
            self._promotions_t2_to_t3,
        )

    async def health_check(self) -> dict[str, Any]:
        """Return learning engine health metrics."""
        return {
            "status": "ok",
            "component": "learning",
            "tier1_sessions": len(self._tier1),
            "tier1_total_interactions": sum(len(v) for v in self._tier1.values()),
            "tier2_pattern_count": len(self._tier2_patterns),
            "total_learned": self._total_learned,
            "promotions_t1_to_t2": self._promotions_t1_to_t2,
            "promotions_t2_to_t3": self._promotions_t2_to_t3,
        }

    # ------------------------------------------------------------------
    # Core learning API
    # ------------------------------------------------------------------

    async def learn(
        self,
        interaction: dict[str, Any],
        session_id: str = "default",
    ) -> dict[str, Any]:
        """Process an interaction and update learning tiers.

        Parameters
        ----------
        interaction : dict
            Must contain at least ``user_input`` and ``response``.  Can also
            include ``type``, ``intent``, ``skills_used``, ``success``, etc.
        session_id : str
            Session identifier for Tier 1 grouping.

        Returns
        -------
        dict
            ``{"tier": 1|2|3, "action": "stored"|"promoted"|"reinforced",
              "pattern": dict}``
        """
        self._total_learned += 1
        interaction["_timestamp"] = time.time()
        interaction["_session_id"] = session_id
        fingerprint = _pattern_fingerprint(interaction)
        interaction["_fingerprint"] = fingerprint

        # -- Step 1: Store in Tier 1 --
        if session_id not in self._tier1:
            self._tier1[session_id] = []
        self._tier1[session_id].append(interaction)

        # -- Step 2: Count similar patterns in this session --
        similar_in_session = [
            i for i in self._tier1[session_id]
            if i.get("_fingerprint") == fingerprint
        ]

        # -- Step 3: Check Tier 1 -> Tier 2 promotion --
        if len(similar_in_session) >= self._tier1_threshold:
            existing_t2 = self._find_tier2_pattern(fingerprint)

            if existing_t2 is not None:
                # Reinforce existing Tier 2 pattern
                existing_t2["occurrence_count"] += 1
                existing_t2["session_count"] += 1
                existing_t2["last_seen"] = time.time()

                # -- Step 4: Check Tier 2 -> Tier 3 promotion --
                if existing_t2["session_count"] >= self._tier2_threshold:
                    await self._promote_to_tier3(existing_t2)
                    return {
                        "tier": 3,
                        "action": "promoted",
                        "pattern": existing_t2,
                    }

                return {
                    "tier": 2,
                    "action": "reinforced",
                    "pattern": existing_t2,
                }
            else:
                # Create new Tier 2 pattern
                pattern = _make_pattern(fingerprint, similar_in_session)
                self._tier2_patterns.append(pattern)
                self._promotions_t1_to_t2 += 1
                logger.info(
                    "Promoted pattern '%s' from Tier 1 -> Tier 2 (%d occurrences).",
                    fingerprint[:8],
                    len(similar_in_session),
                )
                return {
                    "tier": 2,
                    "action": "promoted",
                    "pattern": pattern,
                }

        # Stayed in Tier 1
        return {
            "tier": 1,
            "action": "stored",
            "pattern": {"fingerprint": fingerprint, "occurrence_count": len(similar_in_session)},
        }

    async def consolidate_session(self, session_id: str) -> dict[str, Any]:
        """End-of-session consolidation.

        Reviews all Tier 1 interactions for the session.  Patterns that meet
        the promotion threshold are promoted to Tier 2.  The Tier 1 buffer
        for this session is then cleared.

        Returns a summary of consolidation actions.
        """
        interactions = self._tier1.pop(session_id, [])
        if not interactions:
            return {
                "session_id": session_id,
                "interactions_reviewed": 0,
                "patterns_promoted": 0,
                "patterns_reinforced": 0,
            }

        # Group by fingerprint
        groups: dict[str, list[dict[str, Any]]] = {}
        for interaction in interactions:
            fp = interaction.get("_fingerprint", _pattern_fingerprint(interaction))
            groups.setdefault(fp, []).append(interaction)

        promoted = 0
        reinforced = 0

        for fingerprint, group in groups.items():
            if len(group) >= self._tier1_threshold:
                existing = self._find_tier2_pattern(fingerprint)
                if existing is not None:
                    existing["occurrence_count"] += len(group)
                    existing["session_count"] += 1
                    existing["last_seen"] = time.time()
                    reinforced += 1

                    # Check for Tier 3 promotion
                    if existing["session_count"] >= self._tier2_threshold:
                        await self._promote_to_tier3(existing)
                else:
                    pattern = _make_pattern(fingerprint, group)
                    self._tier2_patterns.append(pattern)
                    self._promotions_t1_to_t2 += 1
                    promoted += 1

        logger.info(
            "Consolidated session '%s': %d interactions, %d promoted, %d reinforced.",
            session_id,
            len(interactions),
            promoted,
            reinforced,
        )

        return {
            "session_id": session_id,
            "interactions_reviewed": len(interactions),
            "patterns_promoted": promoted,
            "patterns_reinforced": reinforced,
        }

    # ------------------------------------------------------------------
    # Tier query
    # ------------------------------------------------------------------

    async def get_tier_stats(self) -> dict[str, Any]:
        """Return counts and health of each tier."""
        tier1_count = sum(len(v) for v in self._tier1.values())
        tier2_count = len(self._tier2_patterns)

        # Tier 3 count comes from the skill DAG
        tier3_count = 0
        if self._skill_dag is not None:
            try:
                dag_stats = self._skill_dag.stats()
                tier3_count = dag_stats.get("node_count", 0)
            except Exception as exc:
                logger.warning("SkillDAG stats unavailable: %s", exc)

        return {
            "tier1": {
                "interaction_count": tier1_count,
                "session_count": len(self._tier1),
                "promotion_threshold": self._tier1_threshold,
            },
            "tier2": {
                "pattern_count": tier2_count,
                "promotion_threshold": self._tier2_threshold,
            },
            "tier3": {
                "skill_count": tier3_count,
            },
            "promotions": {
                "t1_to_t2": self._promotions_t1_to_t2,
                "t2_to_t3": self._promotions_t2_to_t3,
            },
        }

    # ------------------------------------------------------------------
    # Pipeline-compatible update method (called by SpongeBot.process)
    # ------------------------------------------------------------------

    async def update(
        self,
        interaction_result: dict[str, Any] | None = None,
    ) -> None:
        """Pipeline-compatible update called after each interaction.

        Wraps ``learn()`` for the orchestrator's simpler call convention.
        """
        if interaction_result is None:
            return

        session_id = interaction_result.get("session_id", "default")
        await self.learn(interaction_result, session_id=session_id)

    # ------------------------------------------------------------------
    # Similarity
    # ------------------------------------------------------------------

    def _similarity(self, a: dict[str, Any], b: dict[str, Any]) -> float:
        """Compute similarity between two interaction patterns.

        Uses fingerprint equality as a fast path, then falls back to
        word-overlap Jaccard similarity.
        """
        fp_a = a.get("_fingerprint", _pattern_fingerprint(a))
        fp_b = b.get("_fingerprint", _pattern_fingerprint(b))

        if fp_a == fp_b:
            return 1.0

        return _word_overlap_similarity(a, b)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_tier2_pattern(self, fingerprint: str) -> dict[str, Any] | None:
        """Find an existing Tier 2 pattern by fingerprint."""
        for pattern in self._tier2_patterns:
            if pattern["fingerprint"] == fingerprint:
                return pattern
        return None

    async def _promote_to_tier3(self, pattern: dict[str, Any]) -> None:
        """Promote a Tier 2 pattern to Tier 3 (Skill DAG).

        Creates a new SkillNode from the pattern and adds it to the DAG.
        """
        if self._skill_dag is None:
            logger.warning("No Skill DAG available -- cannot promote pattern to Tier 3.")
            return

        # Avoid circular import -- use the DAG's own SkillNode type
        try:
            from src.skills.dag import SkillNode
        except ImportError:
            logger.error("Cannot import SkillNode for Tier 3 promotion.")
            return

        example = pattern.get("example", {})
        fingerprint = pattern["fingerprint"]

        # Derive a skill name from the interaction
        intent = example.get("intent", "")
        skill_type = example.get("type", "")
        user_input = str(example.get("user_input", ""))

        name = intent or skill_type or f"learned_{fingerprint[:8]}"
        description = f"Auto-learned from {pattern['occurrence_count']} interactions across {pattern['session_count']} sessions."

        if user_input:
            description += f" Example: {user_input[:100]}"

        skill = SkillNode(
            name=name,
            description=description,
            skill_type="atomic",
            confidence=0.6,  # Starts with moderate confidence (proven pattern)
            absorbed_from="learning_engine",
            absorption_mode="tier2_promotion",
            tags=["auto-learned", f"fp:{fingerprint[:8]}"],
        )

        try:
            self._skill_dag.add_skill(skill)
            self._promotions_t2_to_t3 += 1

            # Remove from Tier 2 (it lives in the DAG now)
            self._tier2_patterns = [
                p for p in self._tier2_patterns
                if p["fingerprint"] != fingerprint
            ]

            logger.info(
                "Promoted pattern '%s' from Tier 2 -> Tier 3 as skill '%s'.",
                fingerprint[:8],
                name,
            )
        except (ValueError, Exception) as exc:
            logger.warning(
                "Failed to promote pattern '%s' to Tier 3: %s",
                fingerprint[:8],
                exc,
            )
