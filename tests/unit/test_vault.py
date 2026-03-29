from pathlib import Path
"""
Unit tests for src.security.vault_core -- AES-256 Fernet vault.

Tests cover:
- Create vault and encrypt/decrypt bytes
- Encrypt/decrypt text convenience wrappers
- Store and retrieve named secrets
- List secret names
- Sentinel verification
- Wrong password rejection
"""

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from src.security.vault_core import VaultCore, VaultError


@pytest.fixture
def vault(tmp_path):
    """Create a fresh VaultCore with fast PBKDF2 iterations."""
    return VaultCore(
        data_dir=tmp_path / "vault_test",
        vault_password="correct-horse-battery-staple",
        pbkdf2_iterations=1000,
    )


class TestVaultEncryptDecrypt:

    def test_vault_create_and_encrypt_decrypt_bytes(self, vault):
        """Encrypting then decrypting arbitrary bytes must return the original."""
        plaintext = b"SpongeBot secret data \x00\xff\x80"
        ciphertext = vault.encrypt(plaintext)

        # Ciphertext must differ from plaintext
        assert ciphertext != plaintext

        decrypted = vault.decrypt(ciphertext)
        assert decrypted == plaintext

    def test_vault_encrypt_decrypt_text(self, vault):
        """encrypt_text / decrypt_text must round-trip UTF-8 strings."""
        original = "Majin Buu absorbs all knowledge! Unicode: "
        encrypted = vault.encrypt_text(original)

        # Encrypted form is a string (base64), not the original
        assert isinstance(encrypted, str)
        assert encrypted != original

        decrypted = vault.decrypt_text(encrypted)
        assert decrypted == original


class TestVaultSecrets:

    def test_vault_store_retrieve_secret(self, vault):
        """Storing a named secret must allow retrieval by the same name."""
        vault.store_secret("api_key", "sk-ant-api03-ABCDEF")
        retrieved = vault.retrieve_secret("api_key")
        assert retrieved == "sk-ant-api03-ABCDEF"

    def test_vault_retrieve_missing_returns_none(self, vault):
        """Retrieving a secret that was never stored must return None."""
        assert vault.retrieve_secret("nonexistent") is None

    def test_vault_list_secrets(self, vault):
        """list_secrets must return names of all stored secrets."""
        vault.store_secret("key_a", "value_a")
        vault.store_secret("key_b", "value_b")
        vault.store_secret("key_c", "value_c")

        names = vault.list_secrets()
        assert set(names) == {"key_a", "key_b", "key_c"}

    def test_vault_list_secrets_empty(self, vault):
        """list_secrets on a fresh vault must return an empty list."""
        assert vault.list_secrets() == []


class TestVaultSentinel:

    def test_vault_sentinel_verification(self, vault):
        """verify_sentinel must return True on a correctly initialized vault."""
        assert vault.verify_sentinel() is True

    def test_vault_is_initialized(self, vault):
        """is_initialized must be True after construction."""
        assert vault.is_initialized is True


class TestVaultWrongPassword:

    def test_vault_wrong_password_fails(self, tmp_path):
        """Re-opening a vault with a different password must raise VaultError."""
        vault_dir = tmp_path / "wrong_pw_test"

        # Create vault with password A
        VaultCore(
            data_dir=vault_dir,
            vault_password="password-alpha",
            pbkdf2_iterations=1000,
        )

        # Try to open the same vault with password B
        with pytest.raises(VaultError, match="Invalid vault password"):
            VaultCore(
                data_dir=vault_dir,
                vault_password="password-beta",
                pbkdf2_iterations=1000,
            )
