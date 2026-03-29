"""
Layer 3 -- Environment Scanner.

Periodically scans ``sys.modules`` and ``pip list`` output for blocked
SDK imports.  If a foreign AI provider SDK is detected the layer
triggers an immediate lockout.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from typing import Callable, Optional

logger = logging.getLogger("spongebot.lockdown.environment_scanner")

# Blocked top-level module names (imported via ``import <name>``)
BLOCKED_MODULES: frozenset[str] = frozenset(
    [
        "openai",
        "google.generativeai",
        "google.ai",
        "google.ai.generativelanguage",
        "mistralai",
        "cohere",
        "replicate",
        "together",
        "groq",
        "fireworks",
        "anyscale",
        "deepseek",
    ]
)

# Blocked pip package names (may differ from module names)
BLOCKED_PACKAGES: frozenset[str] = frozenset(
    [
        "openai",
        "google-generativeai",
        "google-ai-generativelanguage",
        "mistralai",
        "cohere",
        "replicate",
        "together",
        "groq",
        "fireworks-ai",
        "anyscale",
        "deepseek-sdk",
        "deepseek",
    ]
)


def _scan_sys_modules() -> list[str]:
    """Return list of blocked module names currently in sys.modules."""
    found: list[str] = []
    for blocked in BLOCKED_MODULES:
        # Check both exact match and sub-modules
        for loaded in sys.modules:
            if loaded == blocked or loaded.startswith(blocked + "."):
                found.append(loaded)
    return sorted(set(found))


def _scan_pip_packages() -> list[str]:
    """Return list of blocked packages installed via pip."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=columns"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        installed: set[str] = set()
        for line in result.stdout.splitlines()[2:]:  # skip header lines
            parts = line.split()
            if parts:
                installed.add(parts[0].lower())

        return sorted(BLOCKED_PACKAGES & installed)
    except Exception as exc:
        logger.warning("pip scan failed: %s", exc)
        return []


class EnvironmentScanner:
    """Layer 3: continuous environment scanning for foreign AI SDKs."""

    LAYER_NAME = "environment_scanner"
    LAYER_INDEX = 3

    def __init__(
        self,
        scan_interval: float = 30.0,
        on_violation: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.scan_interval = scan_interval
        self.on_violation = on_violation
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False
        self.last_scan_result: dict = {}
        self.violation_detected = False
        self.violation_details: list[str] = []

    # ----------------------------------------------------------
    # Synchronous one-shot scan
    # ----------------------------------------------------------

    def scan_now(self) -> tuple[bool, str]:
        """Run a single scan.  Returns (clean, reason)."""
        blocked_imports = _scan_sys_modules()
        blocked_packages = _scan_pip_packages()

        self.last_scan_result = {
            "blocked_imports": blocked_imports,
            "blocked_packages": blocked_packages,
        }

        if blocked_imports or blocked_packages:
            all_found = blocked_imports + blocked_packages
            self.violation_detected = True
            self.violation_details = all_found
            msg = (
                f"Foreign AI SDK(s) detected: {', '.join(all_found)}. "
                "Non-Anthropic providers are forbidden."
            )
            logger.critical(msg)
            return False, msg

        logger.debug("Environment scan clean.")
        return True, "No foreign AI SDKs detected."

    # ----------------------------------------------------------
    # Async background scanner
    # ----------------------------------------------------------

    async def start_background_scan(self) -> None:
        """Launch recurring background scan as an asyncio task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._scan_loop())
        logger.info(
            "Background environment scanner started (interval=%.1fs).",
            self.scan_interval,
        )

    async def stop_background_scan(self) -> None:
        """Cancel the background scan task."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Background environment scanner stopped.")

    async def _scan_loop(self) -> None:
        """Internal loop -- runs in background."""
        while self._running:
            clean, reason = self.scan_now()
            if not clean and self.on_violation:
                self.on_violation(reason)
            await asyncio.sleep(self.scan_interval)

    # ----------------------------------------------------------
    # Status
    # ----------------------------------------------------------

    def status(self) -> dict:
        return {
            "layer": self.LAYER_INDEX,
            "name": self.LAYER_NAME,
            "running": self._running,
            "scan_interval": self.scan_interval,
            "violation_detected": self.violation_detected,
            "violation_details": self.violation_details,
            "last_scan": self.last_scan_result,
        }
