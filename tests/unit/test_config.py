from pathlib import Path

"""
Unit tests for src.core.config -- SpongeBot configuration system.

Tests cover:
- load_config returns a dict
- Default config contains all expected sections
- get_config singleton behavior
"""

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from src.core.config import DEFAULT_CONFIG, get_config, load_config, reset_config


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure the config singleton is reset before and after each test."""
    reset_config()
    yield
    reset_config()


class TestLoadConfig:

    def test_load_config_returns_dict(self):
        """load_config() must return a plain dict."""
        cfg = load_config()
        assert isinstance(cfg, dict)

    def test_default_config_has_all_sections(self):
        """Default config must contain all required top-level sections."""
        cfg = load_config()

        required_sections = [
            "spongebot",  # core section (named "spongebot" in config)
            "security",
            "token_saver",
            "memory",
            "absorption",
            "skills",
            "learning",
            "llm",
        ]

        for section in required_sections:
            assert section in cfg, (
                f"Missing required config section: '{section}'. "
                f"Available sections: {list(cfg.keys())}"
            )

    def test_get_config_singleton(self):
        """get_config() must return the same object as load_config() produced."""
        cfg1 = load_config()
        cfg2 = get_config()

        # Must be the exact same dict object (singleton identity)
        assert cfg1 is cfg2

    def test_get_config_raises_before_load(self):
        """get_config() must raise RuntimeError if load_config() was never called."""
        with pytest.raises(RuntimeError, match="Configuration not loaded"):
            get_config()

    def test_load_config_with_profile(self):
        """load_config(profile=...) must apply profile overrides."""
        cfg = load_config(profile="prod")
        assert cfg["spongebot"]["profile"] == "prod"

    def test_default_config_immutability(self):
        """load_config must not mutate DEFAULT_CONFIG."""
        import copy

        original = copy.deepcopy(DEFAULT_CONFIG)
        load_config()
        assert original == DEFAULT_CONFIG
