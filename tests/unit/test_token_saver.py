from pathlib import Path

"""
Unit tests for src.token_saver._engine -- 7-layer token optimization system.

BLACK-BOX tests only -- testing the TokenSaver public interface,
not internal layer implementations.

Tests cover:
- compress_prompt reduces length
- cache_response and check_cache (store + retrieve)
- cache miss returns None
- get_report format
"""

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from src.token_saver._engine import TokenSaver


@pytest.fixture
def saver(tmp_path):
    """Create a TokenSaver instance with a temp directory for cost tracking."""
    return TokenSaver(data_dir=tmp_path / "token_saver_test")


class TestCompressPrompt:

    def test_compress_prompt_reduces_length(self, saver):
        """compress_prompt must reduce the length of a bloated prompt."""
        bloated_prompt = """
        ===================================
        System Prompt for SpongeBot AI Agent
        ===================================

        You are SpongeBot,    a highly capable    AI assistant.

        -----------------------------------

        Key    capabilities:

            -   Natural language understanding
            -   Code generation    and analysis
            -   Knowledge    absorption

        ===================================
        End of System Prompt
        ===================================
        """

        compressed = saver.compress_prompt(bloated_prompt)

        assert isinstance(compressed, str)
        assert len(compressed) < len(bloated_prompt)
        # Core content should still be present
        assert "SpongeBot" in compressed
        assert "capabilities" in compressed

    def test_compress_prompt_preserves_content(self, saver):
        """compress_prompt must preserve meaningful text content."""
        prompt = "You are a helpful assistant. Answer questions clearly."
        compressed = saver.compress_prompt(prompt)

        assert "helpful assistant" in compressed
        assert "Answer questions" in compressed

    def test_compress_prompt_empty(self, saver):
        """compress_prompt on an empty string must return an empty string."""
        result = saver.compress_prompt("")
        assert result == ""


class TestCacheResponseAndRetrieve:

    def test_cache_response_and_retrieve(self, saver):
        """Caching a response must allow retrieval via check_cache."""
        message = "What is the capital of France?"
        response = "The capital of France is Paris."

        # Store in cache
        saver.cache_response(message, response)

        # Retrieve from cache
        cached = saver.check_cache(message)
        assert cached == response

    def test_cache_different_messages(self, saver):
        """Different messages must have independent cache entries."""
        saver.cache_response("question A", "answer A")
        saver.cache_response("question B", "answer B")

        assert saver.check_cache("question A") == "answer A"
        assert saver.check_cache("question B") == "answer B"


class TestCacheMiss:

    def test_cache_miss_returns_none(self, saver):
        """check_cache for an unknown message must return None."""
        result = saver.check_cache("never-seen-before-query-xyz-12345")
        assert result is None

    def test_cache_miss_on_fresh_saver(self, saver):
        """A fresh TokenSaver must have no cached responses."""
        result = saver.check_cache("any message at all")
        assert result is None


class TestSavingsReport:

    def test_savings_report_format(self, saver):
        """get_report must return a string containing key report sections."""
        # Generate some activity to populate the report
        saver.compress_prompt("   padded   prompt   with   spaces   ")
        saver.cache_response("test query", "test response")
        saver.check_cache("test query")  # cache hit
        saver.check_cache("unknown query")  # cache miss

        report = saver.get_report()

        assert isinstance(report, str)
        assert "Token Saver Report" in report
        assert "Layer Statistics" in report

    def test_savings_report_empty(self, saver):
        """get_report on a fresh saver must still return a valid report string."""
        report = saver.get_report()
        assert isinstance(report, str)
        assert len(report) > 0

    def test_savings_percentage_starts_at_zero(self, saver):
        """savings_percentage must be 0.0 on a fresh saver with no activity."""
        assert saver.savings_percentage == 0.0
