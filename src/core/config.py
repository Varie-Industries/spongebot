"""
SpongeBot Configuration System.

Absorbs the _deep_merge and load_config patterns from IT_NEXUS cortex.py,
adapted for SpongeBot's multi-profile, env-var-overlay architecture.

Usage:
    from src.core.config import load_config, get_config

    # Load with auto-detected profile
    cfg = load_config()

    # Load a specific profile
    cfg = load_config(profile="prod")

    # Load from a custom YAML path
    cfg = load_config(path="/path/to/custom.yaml")

    # Singleton access after first load
    cfg = get_config()
"""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("spongebot.config")

# ---------------------------------------------------------------------------
# Project root detection
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "spongebot.yaml"

# ---------------------------------------------------------------------------
# Profile-specific overrides
# ---------------------------------------------------------------------------
_PROFILE_OVERRIDES: dict[str, dict[str, Any]] = {
    "dev": {
        "spongebot": {
            "log_level": "DEBUG",
        },
        "token_saver": {
            "cache_size": 100,
        },
        "api": {
            "cors_origins": ["*"],
        },
        "branding": {
            "show_splash": True,
        },
    },
    "prod": {
        "spongebot": {
            "log_level": "WARNING",
        },
        "token_saver": {
            "cache_size": 2000,
            "cache_ttl_seconds": 600,
        },
        "api": {
            "cors_origins": [],
        },
        "branding": {
            "show_splash": False,
        },
        "security": {
            "pbkdf2_iterations": 2_000_000,
        },
    },
    "ipad": {
        "spongebot": {
            "log_level": "INFO",
        },
        "llm": {
            "max_tokens": 2048,
        },
        "token_saver": {
            "cache_size": 250,
            "cache_ttl_seconds": 180,
        },
        "api": {
            "host": "127.0.0.1",
            "port": 8420,
            "cors_origins": ["*"],
        },
        "branding": {
            "show_splash": True,
        },
    },
}

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: dict[str, Any] = {
    "spongebot": {
        "profile": "dev",
        "log_level": "INFO",
        "data_dir": "data",
    },
    "llm": {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 4096,
        "temperature": 0.7,
        "api_key": "",  # Falls back to ANTHROPIC_API_KEY env var
    },
    "memory": {
        "chromadb_path": "data/chromadb",
        "sqlite_path": "data/spongebot.db",
        "embedding_model": "all-MiniLM-L6-v2",
        "collections": ["skills", "experiences", "knowledge"],
    },
    "absorption": {
        "modes": [
            "agent",
            "document",
            "experience",
            "failure",
            "evolutionary",
            "federated",
        ],
        "initial_confidence": {
            "agent": 0.5,
            "document": 0.3,
            "experience": 0.6,
            "failure": 0.7,
        },
        "evolutionary_generations": 5,
        "evolutionary_min_fitness": 7,
    },
    "skills": {
        "confidence_decay_half_life_days": 7,
        "prune_threshold": 0.15,
        "prune_after_days": 7,
    },
    "learning": {
        "tier1_promotion_threshold": 3,
        "tier2_promotion_threshold": 3,
    },
    "token_saver": {
        "enabled": True,
        "cache_size": 500,
        "cache_ttl_seconds": 300,
        "semantic_cache_threshold": 0.92,
    },
    "security": {
        "vault_password": "",  # Falls back to SPONGEBOT_VAULT_PASSWORD env var
        "pbkdf2_iterations": 1_000_000,
    },
    "api": {
        "host": "0.0.0.0",
        "port": 8420,
        "cors_origins": ["*"],
    },
    "branding": {
        "theme": "spongebot",
        "show_splash": True,
        "buu_mode": True,
    },
}

# ---------------------------------------------------------------------------
# Environment variable mapping
# ---------------------------------------------------------------------------
# Each entry: (env_var_name, config_section, config_key, type_cast)
_ENV_VAR_MAP: list[tuple[str, str, str, type]] = [
    ("SPONGEBOT_PROFILE", "spongebot", "profile", str),
    ("SPONGEBOT_LOG_LEVEL", "spongebot", "log_level", str),
    ("SPONGEBOT_DATA_DIR", "spongebot", "data_dir", str),
    # LLM
    ("ANTHROPIC_API_KEY", "llm", "api_key", str),
    ("SPONGEBOT_LLM_MODEL", "llm", "model", str),
    ("SPONGEBOT_LLM_MAX_TOKENS", "llm", "max_tokens", int),
    ("SPONGEBOT_LLM_TEMPERATURE", "llm", "temperature", float),
    # Memory
    ("SPONGEBOT_CHROMADB_PATH", "memory", "chromadb_path", str),
    ("SPONGEBOT_SQLITE_PATH", "memory", "sqlite_path", str),
    ("SPONGEBOT_EMBEDDING_MODEL", "memory", "embedding_model", str),
    # Skills
    ("SPONGEBOT_SKILL_DECAY_DAYS", "skills", "confidence_decay_half_life_days", int),
    ("SPONGEBOT_SKILL_PRUNE_THRESHOLD", "skills", "prune_threshold", float),
    # Token saver
    ("SPONGEBOT_TOKEN_SAVER_ENABLED", "token_saver", "enabled", bool),
    ("SPONGEBOT_CACHE_SIZE", "token_saver", "cache_size", int),
    ("SPONGEBOT_CACHE_TTL", "token_saver", "cache_ttl_seconds", int),
    ("SPONGEBOT_SEMANTIC_THRESHOLD", "token_saver", "semantic_cache_threshold", float),
    # Security
    ("SPONGEBOT_VAULT_PASSWORD", "security", "vault_password", str),
    ("SPONGEBOT_PBKDF2_ITERATIONS", "security", "pbkdf2_iterations", int),
    # API
    ("SPONGEBOT_API_HOST", "api", "host", str),
    ("SPONGEBOT_API_PORT", "api", "port", int),
    # Branding
    ("SPONGEBOT_THEME", "branding", "theme", str),
    ("SPONGEBOT_SHOW_SPLASH", "branding", "show_splash", bool),
    ("SPONGEBOT_BUU_MODE", "branding", "buu_mode", bool),
]

# ---------------------------------------------------------------------------
# Singleton holder
# ---------------------------------------------------------------------------
_loaded_config: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict.

    - Dict values are merged recursively.
    - All other types in *override* replace the corresponding *base* value.
    - Neither *base* nor *override* is mutated.

    Absorbed from IT_NEXUS ``cortex.py`` pattern.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _cast_env_value(raw: str, target_type: type) -> Any:
    """Cast a raw environment variable string to the expected Python type."""
    if target_type is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if target_type is int:
        return int(raw)
    if target_type is float:
        return float(raw)
    return raw


def _apply_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    """Layer environment variable overrides onto *config* in place and return it.

    Uses ``_ENV_VAR_MAP`` for explicit mappings. Each env var that is set in
    the process environment will override the corresponding config value.
    """
    for env_name, section, key, type_cast in _ENV_VAR_MAP:
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        try:
            value = _cast_env_value(raw, type_cast)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "Ignoring env var %s=%r (cast to %s failed: %s)",
                env_name,
                raw,
                type_cast.__name__,
                exc,
            )
            continue
        config.setdefault(section, {})[key] = value
        logger.debug("Env override: %s.%s = %r (from %s)", section, key, value, env_name)
    return config


def _apply_profile(config: dict[str, Any]) -> dict[str, Any]:
    """Apply profile-specific overrides if a known profile is set."""
    profile = config.get("spongebot", {}).get("profile", "dev")
    overrides = _PROFILE_OVERRIDES.get(profile)
    if overrides is not None:
        config = _deep_merge(config, overrides)
        logger.debug("Applied profile overrides: %s", profile)
    elif profile != "dev":
        logger.warning("Unknown profile %r -- no profile overrides applied", profile)
    return config


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config(
    path: str | None = None,
    *,
    profile: str | None = None,
) -> dict[str, Any]:
    """Load SpongeBot configuration with layered override strategy.

    Resolution order (later layers win):
        1. ``DEFAULT_CONFIG`` -- hardcoded baseline
        2. YAML file -- ``config/spongebot.yaml`` or *path*
        3. Profile overrides -- ``dev`` | ``prod`` | ``ipad``
        4. Environment variables -- ``SPONGEBOT_*`` prefix

    Parameters
    ----------
    path:
        Explicit path to a YAML config file. When *None* the default
        location ``<project_root>/config/spongebot.yaml`` is used.
    profile:
        Force a specific profile. When *None* the profile is read from
        the YAML file, ``SPONGEBOT_PROFILE`` env var, or defaults to
        ``"dev"``.

    Returns
    -------
    dict[str, Any]
        Fully-resolved configuration dictionary.
    """
    global _loaded_config

    # 1. Start from defaults (deep copy to avoid mutation)
    config: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)

    # 2. Merge YAML file
    config_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    if config_path.exists():
        try:
            import yaml
        except ImportError:
            logger.warning(
                "PyYAML not installed -- cannot load %s. "
                "Install with: pip install pyyaml",
                config_path,
            )
        else:
            with open(config_path, "r") as fh:
                user_config = yaml.safe_load(fh) or {}
            config = _deep_merge(config, user_config)
            logger.info("Loaded config from %s", config_path)
    else:
        logger.info("No config file at %s -- using defaults", config_path)

    # 3. Apply profile overrides
    #    Caller profile argument > env var > YAML value > default ("dev")
    if profile is not None:
        config.setdefault("spongebot", {})["profile"] = profile
    elif os.environ.get("SPONGEBOT_PROFILE"):
        config.setdefault("spongebot", {})["profile"] = os.environ["SPONGEBOT_PROFILE"]
    config = _apply_profile(config)

    # 4. Environment variable overrides (highest priority)
    config = _apply_env_overrides(config)

    # Store as singleton for get_config()
    _loaded_config = config
    return config


def get_config() -> dict[str, Any]:
    """Return the previously-loaded config singleton.

    Raises ``RuntimeError`` if ``load_config()`` has not been called yet.
    """
    if _loaded_config is None:
        raise RuntimeError(
            "Configuration not loaded. Call load_config() before get_config()."
        )
    return _loaded_config


def reset_config() -> None:
    """Clear the singleton so the next ``load_config()`` starts fresh.

    Primarily useful in tests.
    """
    global _loaded_config
    _loaded_config = None


def project_root() -> Path:
    """Return the detected project root path."""
    return _PROJECT_ROOT


def data_dir(config: dict[str, Any] | None = None) -> Path:
    """Return the resolved data directory as an absolute ``Path``.

    If *config* is *None*, the singleton config is used.
    """
    cfg = config or get_config()
    raw = cfg.get("spongebot", {}).get("data_dir", "data")
    p = Path(raw)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p
