from pathlib import Path

"""
Unit tests for src.security.audit_chain -- tamper-evident SHA-256 audit chain.

Tests cover:
- Chain creation
- Adding and retrieving entries
- Chain integrity verification
- Tamper detection (modify entry, verify chain is broken)
"""

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from src.security.audit_chain import AuditChain


@pytest.fixture
def chain(tmp_path):
    """Create a fresh AuditChain in a temporary directory."""
    return AuditChain(data_dir=tmp_path / "audit_test")


class TestCreateChain:

    def test_create_chain(self, chain):
        """A newly created chain must have zero entries and GENESIS last_hash."""
        assert chain.length == 0
        assert chain.last_hash == "GENESIS"

    def test_chain_persists_to_directory(self, chain):
        """Chain data directory must be created on construction."""
        assert chain._data_dir.exists()


class TestAddAndRetrieve:

    def test_add_and_retrieve_entries(self, chain):
        """Appending entries must be retrievable via get_log."""
        chain.append("security", "vault_init", "Vault initialized")
        chain.append("skill", "skill_added", "Added Python debugging skill")
        chain.append("system", "boot", "SpongeBot booted")

        assert chain.length == 3

        log = chain.get_log()
        assert len(log) == 3

        # get_log returns most recent first
        assert log[0]["action"] == "boot"
        assert log[1]["action"] == "skill_added"
        assert log[2]["action"] == "vault_init"

    def test_entry_has_correct_sequence(self, chain):
        """Entries must have monotonically increasing sequence numbers."""
        e0 = chain.append("system", "start", "Starting")
        e1 = chain.append("system", "ready", "Ready")

        assert e0.sequence == 0
        assert e1.sequence == 1

    def test_entry_has_hash(self, chain):
        """Appended entries must have a computed entry_hash."""
        entry = chain.append("security", "test", "Test entry")
        assert entry.entry_hash != ""
        assert len(entry.entry_hash) == 64  # SHA-256 hex digest length

    def test_invalid_category_raises(self, chain):
        """Appending with an invalid category must raise ValueError."""
        with pytest.raises(ValueError, match="Invalid category"):
            chain.append("invalid_category", "test", "Should fail")

    def test_filter_by_category(self, chain):
        """get_log with category filter must return only matching entries."""
        chain.append("security", "event_a", "Security event")
        chain.append("skill", "event_b", "Skill event")
        chain.append("security", "event_c", "Another security event")

        security_log = chain.get_log(category="security")
        assert len(security_log) == 2
        for entry in security_log:
            assert entry["category"] == "security"


class TestChainIntegrity:

    def test_chain_integrity_valid(self, chain):
        """A chain with unmodified entries must verify as intact."""
        chain.append("system", "boot", "System boot")
        chain.append("security", "vault_init", "Vault initialized")
        chain.append("skill", "skill_added", "Added skill")
        chain.append("system", "scan", "Environment scan clean")

        is_valid, reason = chain.verify_chain()
        assert is_valid is True
        assert reason == "ok"

    def test_chain_integrity_empty(self, chain):
        """An empty chain must verify as intact."""
        is_valid, reason = chain.verify_chain()
        assert is_valid is True
        assert reason == "ok"


class TestTamperDetection:

    def test_chain_tamper_detection(self, chain):
        """Modifying an entry's detail field must cause verify_chain to fail."""
        chain.append("system", "boot", "System boot")
        chain.append("security", "check", "Security check passed")
        chain.append("skill", "learn", "Learned new skill")

        # Verify chain is valid before tampering
        is_valid, _ = chain.verify_chain()
        assert is_valid is True

        # Tamper with the middle entry's detail
        chain._chain[1].detail = "TAMPERED CONTENT"

        # Verify chain detects the tamper
        is_valid, reason = chain.verify_chain()
        assert is_valid is False
        assert "hash mismatch" in reason.lower() or "mismatch" in reason.lower()

    def test_chain_linkage_tamper(self, chain):
        """Modifying an entry's prev_hash must break chain verification."""
        chain.append("system", "event_1", "First event")
        chain.append("system", "event_2", "Second event")
        chain.append("system", "event_3", "Third event")

        # Tamper with chain linkage
        chain._chain[2].prev_hash = "0000000000000000"

        is_valid, reason = chain.verify_chain()
        assert is_valid is False
