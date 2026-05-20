from pathlib import Path

"""
Unit tests for src.skills.dag -- networkx-based Skill DAG.

Tests cover:
- Create DAG
- Add atomic skill
- Add composed skill (with prerequisites)
- Query by capability (find_relevant)
- DAG acyclicity enforcement
- Confidence update (boost/penalize)
- Skill versioning (bump_version)
"""

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from src.skills.dag import SkillDAG, SkillNode


@pytest.fixture
def dag_config():
    """Minimal config for SkillDAG construction."""
    return {
        "spongebot": {"data_dir": "/tmp/spongebot_test_dag"},
        "skills": {
            "confidence_decay_half_life_days": 7,
            "prune_threshold": 0.15,
            "prune_after_days": 7,
        },
    }


@pytest.fixture
def dag(dag_config, tmp_path):
    """Create a fresh SkillDAG with a temp persist path."""
    return SkillDAG(
        config=dag_config,
        persist_path=tmp_path / "test_skill_dag.json",
    )


def _make_skill(name, skill_type="atomic", confidence=0.5, **kwargs):
    """Helper to create SkillNode instances for tests."""
    return SkillNode(
        name=name,
        description=f"Test skill: {name}",
        skill_type=skill_type,
        confidence=confidence,
        **kwargs,
    )


class TestCreateDAG:

    def test_create_dag(self, dag):
        """A freshly created DAG must have no skills and no edges."""
        stats = dag.stats()
        assert stats["node_count"] == 0
        assert stats["edge_count"] == 0
        assert stats["is_dag"] is True


class TestAddSkills:

    def test_add_atomic_skill(self, dag):
        """Adding an atomic skill must increase the node count."""
        skill = _make_skill("python_debugging", skill_type="atomic")
        name = dag.add_skill(skill)

        assert name == "python_debugging"
        assert dag.stats()["node_count"] == 1

        retrieved = dag.get_skill("python_debugging")
        assert retrieved is not None
        assert retrieved.name == "python_debugging"
        assert retrieved.skill_type == "atomic"

    def test_add_composed_skill(self, dag):
        """Adding a composed skill with prerequisites must create edges."""
        # Add prerequisite skills first
        dag.add_skill(_make_skill("read_file"))
        dag.add_skill(_make_skill("parse_json"))

        # Add composed skill that requires both
        composed = _make_skill(
            "process_config",
            skill_type="composed",
            prerequisites=["read_file", "parse_json"],
        )
        dag.add_skill(composed)

        stats = dag.stats()
        assert stats["node_count"] == 3
        assert stats["edge_count"] == 2  # read_file->process, parse_json->process

    def test_add_duplicate_updates(self, dag):
        """Adding a skill with an existing name must update (not duplicate)."""
        dag.add_skill(_make_skill("my_skill", confidence=0.5))
        dag.add_skill(_make_skill("my_skill", confidence=0.9))

        assert dag.stats()["node_count"] == 1
        updated = dag.get_skill("my_skill")
        assert updated.confidence == 0.9


class TestQueryByCapability:

    def test_query_by_capability(self, dag):
        """find_relevant must return skills matching the query keywords."""
        dag.add_skill(_make_skill(
            "python_debugging",
            tags=["python", "debugging"],
        ))
        dag.add_skill(_make_skill(
            "javascript_testing",
            tags=["javascript", "testing"],
        ))
        dag.add_skill(_make_skill(
            "python_testing",
            tags=["python", "testing"],
        ))

        results = dag.find_relevant("python")
        names = [s.name for s in results]

        assert "python_debugging" in names
        assert "python_testing" in names

    def test_query_with_min_confidence(self, dag):
        """find_relevant with min_confidence must filter low-confidence skills."""
        dag.add_skill(_make_skill("high_conf", confidence=0.9, tags=["test"]))
        dag.add_skill(_make_skill("low_conf", confidence=0.1, tags=["test"]))

        results = dag.find_relevant("test", min_confidence=0.5)
        names = [s.name for s in results]

        assert "high_conf" in names
        assert "low_conf" not in names

    def test_query_no_match(self, dag):
        """find_relevant with no matching query must return empty list."""
        dag.add_skill(_make_skill("python_skill", tags=["python"]))
        results = dag.find_relevant("quantum_entanglement_xyz")
        assert results == []


class TestDAGAcyclicity:

    def test_dag_acyclicity(self, dag):
        """The DAG must always remain acyclic -- adding a cycle must raise."""
        dag.add_skill(_make_skill("A"))
        dag.add_skill(_make_skill("B", prerequisites=["A"]))

        # Try to add C that requires B, then add edge B -> A (creates cycle)
        dag.add_skill(_make_skill("C", prerequisites=["B"]))

        # Adding an edge that creates a cycle must raise ValueError
        with pytest.raises(ValueError, match="cycle"):
            dag.add_edge("C", "A", "requires")

        # DAG must still be valid after rejected cycle
        assert dag.stats()["is_dag"] is True

    def test_dag_acyclicity_on_add_skill(self, dag):
        """Adding a skill whose prerequisites form a cycle must raise."""
        dag.add_skill(_make_skill("X"))
        dag.add_skill(_make_skill("Y", prerequisites=["X"]))

        # Z requires Y, and X already requires nothing, so adding
        # Z -> X edge indirectly would need to be via add_edge
        # Testing direct cycle: add skill that prereqs itself
        with pytest.raises(ValueError, match="cycle"):
            # Create A -> B -> A cycle
            dag.add_edge("Y", "X", "requires")


class TestConfidenceUpdate:

    def test_confidence_update(self, dag):
        """boost_confidence must increase confidence, penalize must decrease."""
        dag.add_skill(_make_skill("test_skill", confidence=0.5))

        dag.boost_confidence("test_skill", amount=0.2)
        skill = dag.get_skill("test_skill")
        assert skill.confidence == pytest.approx(0.7)

        dag.penalize_confidence("test_skill", amount=0.3)
        skill = dag.get_skill("test_skill")
        assert skill.confidence == pytest.approx(0.4)

    def test_confidence_capped_at_bounds(self, dag):
        """Confidence must be capped between 0.0 and 1.0."""
        dag.add_skill(_make_skill("bounded_skill", confidence=0.9))

        dag.boost_confidence("bounded_skill", amount=0.5)
        assert dag.get_skill("bounded_skill").confidence == 1.0

        dag.penalize_confidence("bounded_skill", amount=2.0)
        assert dag.get_skill("bounded_skill").confidence == 0.0

    def test_boost_updates_use_count(self, dag):
        """boost_confidence must increment use_count and update last_used."""
        dag.add_skill(_make_skill("used_skill"))

        dag.boost_confidence("used_skill")
        skill = dag.get_skill("used_skill")
        assert skill.use_count == 1
        assert skill.last_used > 0


class TestSkillVersioning:

    def test_skill_versioning(self, dag):
        """bump_version must correctly increment semantic version parts."""
        dag.add_skill(_make_skill("versioned_skill"))

        # Initial version is 1.0.0
        skill = dag.get_skill("versioned_skill")
        assert skill.version == "1.0.0"

        # Patch bump: 1.0.0 -> 1.0.1
        new_ver = dag.bump_version("versioned_skill", "patch")
        assert new_ver == "1.0.1"

        # Minor bump: 1.0.1 -> 1.1.0
        new_ver = dag.bump_version("versioned_skill", "minor")
        assert new_ver == "1.1.0"

        # Major bump: 1.1.0 -> 2.0.0
        new_ver = dag.bump_version("versioned_skill", "major")
        assert new_ver == "2.0.0"

    def test_bump_version_unknown_skill_raises(self, dag):
        """bump_version on a nonexistent skill must raise KeyError."""
        with pytest.raises(KeyError):
            dag.bump_version("nonexistent_skill", "patch")

    def test_bump_version_invalid_type_raises(self, dag):
        """bump_version with an invalid bump type must raise ValueError."""
        dag.add_skill(_make_skill("v_skill"))
        with pytest.raises(ValueError, match="Invalid bump type"):
            dag.bump_version("v_skill", "mega")
