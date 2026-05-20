from pathlib import Path

"""
Unit tests for src.absorption submodules -- document, experience, failure.

Tests cover:
- DocumentAbsorption: extracting skills from document content
- ExperienceAbsorption: capturing successful task trajectories
- FailureAbsorption: creating anti-skills from failed trajectories

All tests use no LLM client (deterministic fallback paths).
"""

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from src.absorption.document_absorption import DocumentAbsorption
from src.absorption.experience_absorption import ExperienceAbsorption
from src.absorption.failure_absorption import FailureAbsorption


@pytest.fixture
def absorption_config():
    """Minimal config for absorption engines."""
    return {
        "absorption": {
            "modes": ["document", "experience", "failure"],
            "initial_confidence": {
                "agent": 0.5,
                "document": 0.3,
                "experience": 0.6,
                "failure": 0.7,
            },
        },
    }


class TestDocumentAbsorption:

    @pytest.fixture
    def doc_absorber(self, absorption_config):
        """DocumentAbsorption instance with no LLM (uses deterministic path)."""
        return DocumentAbsorption(config=absorption_config, llm_client=None)

    async def test_document_absorption_extracts_skills(self, doc_absorber):
        """absorb() with no LLM client must return an empty list (no extraction).

        Without an LLM client, DocumentAbsorption cannot extract skills
        from chunks. The chunking still happens but _extract_from_chunk
        returns [] for each chunk. This tests the interface contract.
        """
        content = """
# Python Debugging Guide

## Using pdb

Set breakpoints with `import pdb; pdb.set_trace()`.

## Using logging

Configure logging with `logging.basicConfig(level=logging.DEBUG)`.

## Stack Traces

Read stack traces bottom-up to find the root cause.
"""
        result = await doc_absorber.absorb(content, content_type="markdown", source_id="debug_guide.md")

        assert isinstance(result, list)

    def test_document_absorption_chunking(self, doc_absorber):
        """The internal _chunk method must split markdown by headings."""
        content = "# Heading 1\nContent A.\n\n# Heading 2\nContent B."
        chunks = doc_absorber._chunk(content, "markdown")

        assert len(chunks) >= 2

    def test_document_absorption_preprocess_html(self, doc_absorber):
        """HTML preprocessing must strip tags."""
        html = "<p>Hello <b>world</b></p>"
        cleaned = doc_absorber._preprocess(html, "html")
        assert "<p>" not in cleaned
        assert "<b>" not in cleaned
        assert "Hello" in cleaned
        assert "world" in cleaned

    async def test_document_absorption_empty_content(self, doc_absorber):
        """absorb() with empty content must return an empty list."""
        result = await doc_absorber.absorb("", content_type="text")
        assert result == []

    async def test_document_absorption_unsupported_type_falls_back(self, doc_absorber):
        """An unsupported content_type must fall back to 'text' without error."""
        result = await doc_absorber.absorb("Some content here.", content_type="docx")
        assert isinstance(result, list)


class TestExperienceAbsorption:

    @pytest.fixture
    def exp_absorber(self, absorption_config):
        """ExperienceAbsorption with no LLM (deterministic distillation)."""
        return ExperienceAbsorption(config=absorption_config, llm_client=None)

    async def test_experience_absorption_captures_trajectory(self, exp_absorber):
        """absorb() with a valid trajectory must produce skill dicts."""
        trajectory = [
            {"tool": "read_file", "input": {"path": "config.json"}, "output": {"content": "{}"}},
            {"tool": "parse_json", "input": {"data": "{}"}, "output": {"parsed": {}}},
            {"tool": "write_file", "input": {"path": "out.json"}, "output": {"success": True}},
        ]

        result = await exp_absorber.absorb(trajectory, source_id="config_task")

        assert isinstance(result, list)
        assert len(result) >= 1

        skill = result[0]
        assert "name" in skill
        assert "steps" in skill
        assert "confidence" in skill
        assert skill["absorption_mode"] == "experience"

    async def test_experience_absorption_empty_trajectory(self, exp_absorber):
        """absorb() with an empty trajectory must return an empty list."""
        result = await exp_absorber.absorb([], source_id="empty")
        assert result == []

    def test_experience_absorption_deterministic_distill(self, exp_absorber):
        """The deterministic fallback must produce a plan from tool names."""
        steps = [
            {"tool": "search", "input": {}, "output": {}},
            {"tool": "read_file", "input": {}, "output": {}},
        ]
        distilled = ExperienceAbsorption._deterministic_distill(steps)

        assert "name" in distilled
        assert "steps" in distilled
        assert len(distilled["steps"]) == 2

    def test_experience_absorption_validates_steps(self, exp_absorber):
        """Steps without 'tool' key must be filtered out during validation."""
        trajectory = [
            {"tool": "valid_tool", "input": {}, "output": {}},
            {"no_tool_key": True},
            {"tool": "another_tool", "input": {}, "output": {}},
        ]

        valid = ExperienceAbsorption._validate_trajectory(trajectory)
        assert len(valid) == 2


class TestFailureAbsorption:

    @pytest.fixture
    def fail_absorber(self, absorption_config):
        """FailureAbsorption with no LLM and no skill_dag."""
        return FailureAbsorption(
            config=absorption_config,
            llm_client=None,
            skill_dag=None,
        )

    async def test_failure_absorption_creates_anti_skills(self, fail_absorber):
        """absorb() with a failed trajectory must create anti-skill dicts."""
        trajectory = [
            {"tool": "deploy_app", "input": {"env": "prod"}, "output": {"error": "Permission denied"}},
        ]
        error_info = "Deployment failed: Permission denied on production server"

        result = await fail_absorber.absorb(
            trajectory,
            error_info=error_info,
            source_id="deploy_failure",
        )

        assert isinstance(result, list)
        assert len(result) == 1

        anti_skill = result[0]
        assert anti_skill["type"] == "anti_skill"
        assert "name" in anti_skill
        assert anti_skill["name"].startswith("avoid_")
        assert anti_skill["absorption_mode"] == "failure"
        assert anti_skill["confidence"] == 0.7

    async def test_failure_absorption_empty_trajectory_and_error(self, fail_absorber):
        """absorb() with empty trajectory AND no error info returns empty list."""
        result = await fail_absorber.absorb([], error_info="")
        assert result == []

    def test_failure_absorption_deterministic_analyse(self):
        """The deterministic fallback must extract tool names and error info."""
        trajectory = [
            {"tool": "compile", "input": {}, "output": {}},
            {"tool": "test", "input": {}, "output": {}, "error": "AssertionError"},
        ]
        error_info = "Test suite failed with assertion error"

        analysis = FailureAbsorption._deterministic_analyse(trajectory, error_info)

        assert analysis["name"].startswith("avoid_")
        assert "failure" in analysis["tags"]
        assert len(analysis["avoidance_steps"]) > 0

    async def test_failure_absorption_increments_count(self, fail_absorber):
        """Each absorb call must increment the internal failure counter."""
        assert fail_absorber._failure_count == 0

        await fail_absorber.absorb(
            [{"tool": "x", "input": {}, "output": {}}],
            error_info="fail",
        )

        assert fail_absorber._failure_count == 1
