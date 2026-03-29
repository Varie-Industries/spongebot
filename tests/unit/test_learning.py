from pathlib import Path
"""
Unit tests for src.learning.engine -- 3-tier nested learning system.

Tests cover:
- Create learning engine
- Tier 1 fast learning: add interaction and retrieve from session
- Tier 1 expiry: Tier 1 data is session-scoped and clears on consolidation
"""

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio

import pytest

from src.learning.engine import LearningEngine


@pytest.fixture
def learning_config():
    """Minimal config for LearningEngine."""
    return {
        "learning": {
            "tier1_promotion_threshold": 3,
            "tier2_promotion_threshold": 3,
        },
    }


@pytest.fixture
def engine(learning_config):
    """Create a fresh LearningEngine."""
    return LearningEngine(config=learning_config)


def _make_interaction(user_input, response="ok", **extra):
    """Helper to create interaction dicts."""
    result = {
        "user_input": user_input,
        "response": response,
    }
    result.update(extra)
    return result


class TestCreateLearningEngine:

    def test_create_learning_engine(self, engine):
        """LearningEngine must initialize with empty tiers."""
        assert engine._tier1 == {}
        assert engine._tier2_patterns == []
        assert engine._total_learned == 0

    def test_engine_has_correct_thresholds(self, engine):
        """Engine must respect config thresholds."""
        assert engine._tier1_threshold == 3
        assert engine._tier2_threshold == 3


class TestTier1FastLearning:

    def test_tier1_fast_learning(self, engine):
        """Learning an interaction must store it in Tier 1 for the session."""
        interaction = _make_interaction("How do I debug Python?", "Use pdb")

        result = asyncio.get_event_loop().run_until_complete(
            engine.learn(interaction, session_id="test_session")
        )

        # Should be stored in Tier 1
        assert result["tier"] == 1
        assert result["action"] == "stored"

        # Verify it is in the session buffer
        assert "test_session" in engine._tier1
        assert len(engine._tier1["test_session"]) == 1
        assert engine._total_learned == 1

    def test_tier1_multiple_interactions(self, engine):
        """Multiple interactions in the same session must accumulate."""
        loop = asyncio.get_event_loop()

        loop.run_until_complete(
            engine.learn(
                _make_interaction("question 1", type="coding"),
                session_id="session_a",
            )
        )
        loop.run_until_complete(
            engine.learn(
                _make_interaction("question 2", type="debugging"),
                session_id="session_a",
            )
        )

        assert len(engine._tier1["session_a"]) == 2
        assert engine._total_learned == 2

    def test_tier1_separate_sessions(self, engine):
        """Different session IDs must maintain separate Tier 1 buffers."""
        loop = asyncio.get_event_loop()

        loop.run_until_complete(
            engine.learn(
                _make_interaction("q1"),
                session_id="session_x",
            )
        )
        loop.run_until_complete(
            engine.learn(
                _make_interaction("q2"),
                session_id="session_y",
            )
        )

        assert len(engine._tier1["session_x"]) == 1
        assert len(engine._tier1["session_y"]) == 1


class TestTier1Expiry:

    def test_tier1_expiry(self, engine):
        """Tier 1 data must be cleared when a session is consolidated."""
        loop = asyncio.get_event_loop()

        # Add a single interaction (below promotion threshold)
        loop.run_until_complete(
            engine.learn(
                _make_interaction("one-off question"),
                session_id="ephemeral_session",
            )
        )

        assert "ephemeral_session" in engine._tier1

        # Consolidate the session -- this should clear Tier 1 data
        result = loop.run_until_complete(
            engine.consolidate_session("ephemeral_session")
        )

        # Session buffer must be cleared
        assert "ephemeral_session" not in engine._tier1
        assert result["interactions_reviewed"] == 1

    def test_tier1_consolidation_with_no_promotion(self, engine):
        """Below-threshold patterns must not promote to Tier 2 on consolidation."""
        loop = asyncio.get_event_loop()

        # Add 2 interactions (threshold is 3, so no promotion)
        loop.run_until_complete(
            engine.learn(
                _make_interaction("debug python", type="coding", intent="debug"),
                session_id="sess",
            )
        )
        loop.run_until_complete(
            engine.learn(
                _make_interaction("debug python error", type="coding", intent="debug"),
                session_id="sess",
            )
        )

        result = loop.run_until_complete(engine.consolidate_session("sess"))

        assert result["patterns_promoted"] == 0
        assert len(engine._tier2_patterns) == 0

    def test_tier1_promotion_to_tier2(self, engine):
        """When threshold is met within a session, pattern must promote to Tier 2."""
        loop = asyncio.get_event_loop()

        # Add 3 similar interactions (same type and intent = same fingerprint)
        for i in range(3):
            result = loop.run_until_complete(
                engine.learn(
                    _make_interaction(
                        f"debug python issue {i}",
                        type="coding",
                        intent="debug",
                    ),
                    session_id="promo_session",
                )
            )

        # Third interaction should trigger promotion
        assert result["tier"] == 2
        assert result["action"] == "promoted"
        assert len(engine._tier2_patterns) == 1
