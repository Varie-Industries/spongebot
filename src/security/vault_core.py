"""
SpongeBot Security Vault Core

AES-256 Fernet vault with PBKDF2-HMAC-SHA256 key derivation.
Absorbed from IT_NEXUS SecurityCore with hardened parameters:
- 1,000,000 PBKDF2 iterations (vs IT_NEXUS 600,000)
- 32-byte salt (vs IT_NEXUS 16-byte)
- SpongeBot-specific sentinel verification
"""

from __future__ import annotations

import base64
import json
import os
import stat
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class VaultError(Exception):
    """Raised for vault encryption, decryption, or integrity errors."""


class VaultCore:
    """AES-256 Fernet vault with PBKDF2-HMAC-SHA256 key derivation.

    Provides encrypted storage for SpongeBot secrets, skills, and
    absorbed knowledge. The vault key is derived from a master password
    using PBKDF2 with 1M iterations and a 32-byte random salt.

    On first run, a vault.key file is created containing the salt and
    an encrypted sentinel value. On subsequent runs, the sentinel is
    decrypted and verified to confirm the master password is correct.

    Parameters
    ----------
    data_dir : str | Path
        Directory for persistent vault data (vault.key, secrets store).
    vault_password : str
        Master password for PBKDF2 key derivation.
    pbkdf2_iterations : int
        Number of PBKDF2 iterations (default 1,000,000).
    """

    _SALT_LEN = 32
    _SENTINEL = b"SPONGEBOT_VAULT_OK"
    _VAULT_KEY_FILE = "vault.key"
    _SECRETS_FILE = "secrets.vault"
    _PBKDF2_ITERATIONS_DEFAULT = 1_000_000

    def __init__(
        self,
        data_dir: str | Path,
        vault_password: str,
        pbkdf2_iterations: int = _PBKDF2_ITERATIONS_DEFAULT,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._vault_password = vault_password.encode("utf-8")
        self._pbkdf2_iterations = pbkdf2_iterations

        self._lock = threading.Lock()
        self._secrets_cache: dict[str, str] = {}

        # Derive key and initialise Fernet
        self._fernet = self._init_vault()

        # Load persisted secrets into cache
        self._load_secrets()

    # ------------------------------------------------------------------
    # Key derivation
    # ------------------------------------------------------------------

    def _derive_key(self, salt: bytes) -> bytes:
        """Derive a 32-byte Fernet-compatible key from the master password.

        Uses PBKDF2-HMAC-SHA256 with the configured iteration count.

        Parameters
        ----------
        salt : bytes
            Random salt (must be ``_SALT_LEN`` bytes).

        Returns
        -------
        bytes
            URL-safe base64-encoded 32-byte key suitable for Fernet.
        """
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=self._pbkdf2_iterations,
            backend=default_backend(),
        )
        raw = kdf.derive(self._vault_password)
        return base64.urlsafe_b64encode(raw)

    # ------------------------------------------------------------------
    # Vault initialisation
    # ------------------------------------------------------------------

    def _init_vault(self) -> Fernet:
        """Load or create the vault key file, returning a configured Fernet.

        The key file stores ``salt || encrypted_sentinel`` so we can verify
        the master password on subsequent loads. File permissions are set
        to 0o600 (owner read/write only).

        Returns
        -------
        Fernet
            Configured Fernet instance.

        Raises
        ------
        VaultError
            If the master password is wrong or the key file is corrupted.
        """
        key_path = self._data_dir / self._VAULT_KEY_FILE

        if key_path.exists():
            raw = key_path.read_bytes()
            if len(raw) < self._SALT_LEN:
                raise VaultError(
                    "Vault key file is too short - corrupted or truncated"
                )
            salt = raw[: self._SALT_LEN]
            encrypted_sentinel = raw[self._SALT_LEN :]
            derived = self._derive_key(salt)
            fernet = Fernet(derived)
            try:
                decrypted = fernet.decrypt(encrypted_sentinel)
                if decrypted != self._SENTINEL:
                    raise VaultError(
                        "Vault sentinel mismatch - corrupted key file"
                    )
            except InvalidToken as exc:
                raise VaultError(
                    "Invalid vault password or corrupted key file"
                ) from exc
            return fernet

        # First run: create new vault key file
        salt = os.urandom(self._SALT_LEN)
        derived = self._derive_key(salt)
        fernet = Fernet(derived)
        encrypted_sentinel = fernet.encrypt(self._SENTINEL)
        key_path.write_bytes(salt + encrypted_sentinel)

        # Restrict permissions: owner read/write only (0o600)
        key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

        return fernet

    # ------------------------------------------------------------------
    # Core encrypt / decrypt
    # ------------------------------------------------------------------

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt arbitrary bytes with the vault Fernet key.

        Parameters
        ----------
        plaintext : bytes
            Data to encrypt.

        Returns
        -------
        bytes
            Fernet ciphertext (URL-safe base64).
        """
        return self._fernet.encrypt(plaintext)

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt Fernet ciphertext previously produced by ``encrypt``.

        Parameters
        ----------
        ciphertext : bytes
            Fernet token to decrypt.

        Returns
        -------
        bytes
            Original plaintext.

        Raises
        ------
        VaultError
            If the token is invalid or tampered with.
        """
        try:
            return self._fernet.decrypt(ciphertext)
        except InvalidToken as exc:
            raise VaultError(
                "Decryption failed - invalid or tampered token"
            ) from exc

    # ------------------------------------------------------------------
    # Text convenience wrappers
    # ------------------------------------------------------------------

    def encrypt_text(self, text: str) -> str:
        """Encrypt a UTF-8 string, returning a base64-encoded ciphertext string.

        Parameters
        ----------
        text : str
            Plaintext string to encrypt.

        Returns
        -------
        str
            Base64-encoded Fernet ciphertext.
        """
        ct = self.encrypt(text.encode("utf-8"))
        return base64.urlsafe_b64encode(ct).decode("ascii")

    def decrypt_text(self, encoded: str) -> str:
        """Decrypt a base64-encoded ciphertext string back to plaintext.

        Parameters
        ----------
        encoded : str
            Base64-encoded Fernet ciphertext produced by ``encrypt_text``.

        Returns
        -------
        str
            Original plaintext string.
        """
        ct = base64.urlsafe_b64decode(encoded.encode("ascii"))
        return self.decrypt(ct).decode("utf-8")

    # ------------------------------------------------------------------
    # Named secrets store
    # ------------------------------------------------------------------

    def store_secret(self, name: str, value: str) -> None:
        """Store a named secret in the encrypted vault.

        The secret is encrypted and persisted to disk. Existing secrets
        with the same name are overwritten.

        Parameters
        ----------
        name : str
            Unique identifier for the secret.
        value : str
            Secret value to store.
        """
        with self._lock:
            self._secrets_cache[name] = value
            self._persist_secrets()

    def retrieve_secret(self, name: str) -> str | None:
        """Retrieve a named secret from the vault.

        Parameters
        ----------
        name : str
            Identifier of the secret to retrieve.

        Returns
        -------
        str or None
            The secret value, or ``None`` if not found.
        """
        with self._lock:
            return self._secrets_cache.get(name)

    def delete_secret(self, name: str) -> bool:
        """Delete a named secret from the vault.

        Parameters
        ----------
        name : str
            Identifier of the secret to delete.

        Returns
        -------
        bool
            ``True`` if the secret existed and was deleted, ``False`` otherwise.
        """
        with self._lock:
            if name in self._secrets_cache:
                del self._secrets_cache[name]
                self._persist_secrets()
                return True
            return False

    def list_secrets(self) -> list[str]:
        """List all stored secret names (values are NOT returned).

        Returns
        -------
        list[str]
            Names of all stored secrets.
        """
        with self._lock:
            return list(self._secrets_cache.keys())

    def _persist_secrets(self) -> None:
        """Encrypt and write the secrets cache to disk."""
        payload = json.dumps(self._secrets_cache).encode("utf-8")
        encrypted = self.encrypt(payload)
        secrets_path = self._data_dir / self._SECRETS_FILE
        secrets_path.write_bytes(encrypted)
        secrets_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def _load_secrets(self) -> None:
        """Load persisted secrets from disk into the in-memory cache."""
        secrets_path = self._data_dir / self._SECRETS_FILE
        if not secrets_path.exists():
            return
        try:
            encrypted = secrets_path.read_bytes()
            payload = self.decrypt(encrypted)
            self._secrets_cache = json.loads(payload.decode("utf-8"))
        except (VaultError, json.JSONDecodeError):
            # If secrets file is corrupted, start fresh but don't crash
            self._secrets_cache = {}

    # ------------------------------------------------------------------
    # Vault metadata
    # ------------------------------------------------------------------

    @property
    def data_dir(self) -> Path:
        """Return the vault data directory path."""
        return self._data_dir

    @property
    def key_path(self) -> Path:
        """Return the path to the vault key file."""
        return self._data_dir / self._VAULT_KEY_FILE

    @property
    def is_initialized(self) -> bool:
        """Check whether the vault key file exists on disk."""
        return (self._data_dir / self._VAULT_KEY_FILE).exists()

    def verify_sentinel(self) -> bool:
        """Re-verify the vault sentinel to confirm integrity.

        Returns
        -------
        bool
            ``True`` if the sentinel decrypts and matches, ``False`` otherwise.
        """
        key_path = self._data_dir / self._VAULT_KEY_FILE
        if not key_path.exists():
            return False
        try:
            raw = key_path.read_bytes()
            encrypted_sentinel = raw[self._SALT_LEN :]
            decrypted = self._fernet.decrypt(encrypted_sentinel)
            return decrypted == self._SENTINEL
        except (InvalidToken, Exception):
            return False

    # ------------------------------------------------------------------
    # Config-based construction
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> VaultCore:
        """Construct a VaultCore from a SpongeBot configuration dict.

        Expected config structure::

            {
                "security": {
                    "vault_password": "...",
                    "pbkdf2_iterations": 1000000
                },
                "core": {
                    "data_dir": "data"
                }
            }

        Falls back to the ``SPONGEBOT_VAULT_PASSWORD`` environment
        variable if ``vault_password`` is not in the config.

        Parameters
        ----------
        config : dict
            SpongeBot configuration dictionary.

        Returns
        -------
        VaultCore
            Configured vault instance.
        """
        sec_cfg = config.get("security", {})
        data_dir = (
            Path(config.get("core", {}).get("data_dir", "data")) / "security"
        )
        vault_password = sec_cfg.get("vault_password", "") or os.environ.get(
            "SPONGEBOT_VAULT_PASSWORD", "changeme"
        )
        pbkdf2_iterations = sec_cfg.get(
            "pbkdf2_iterations", cls._PBKDF2_ITERATIONS_DEFAULT
        )
        return cls(
            data_dir=data_dir,
            vault_password=vault_password,
            pbkdf2_iterations=pbkdf2_iterations,
        )
