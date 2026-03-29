"""
Shared pytest fixtures for SpongeBot unit tests.

Provides reusable fixtures for configuration, vault, temporary directories,
and other common test dependencies.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Provide a temporary data directory for tests that need disk storage."""
    data_dir = tmp_path / "spongebot_test_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def sample_config():
    """Load the default SpongeBot configuration (reset singleton each time)."""
    from src.core.config import load_config, reset_config

    reset_config()
    cfg = load_config()
    yield cfg
    reset_config()


@pytest.fixture
def vault_instance(tmp_data_dir):
    """Create a VaultCore instance with a test password in a temp directory.

    Uses low PBKDF2 iterations (1000) to keep tests fast.
    """
    from src.security.vault_core import VaultCore

    return VaultCore(
        data_dir=tmp_data_dir / "vault",
        vault_password="test-password-12345",
        pbkdf2_iterations=1000,  # Fast for testing
    )
