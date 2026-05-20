"""
SpongeBot Self-Destruct Mechanism

Emergency data destruction with safety arming protocol.
Performs 3-pass random overwrite of vault.key, purges ChromaDB
directory, zeros SQLite databases, and optionally logs to a
tamper-proof external location.

The self-destruct sequence requires explicit arming before it
can be triggered, preventing accidental data loss.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class SelfDestructError(Exception):
    """Raised for self-destruct operation failures."""


class SelfDestruct:
    """Emergency self-destruct mechanism for SpongeBot vault data.

    Provides a two-phase destruction protocol:
    1. **Arm**: Enable the self-destruct sequence (sets a timer window).
    2. **Trigger**: Execute destruction within the armed window.

    The arming requirement prevents accidental triggering. The armed
    state expires after ``arm_timeout`` seconds for additional safety.

    Destruction sequence:
    - 3-pass random overwrite of vault.key
    - Purge ChromaDB directory (shutil.rmtree)
    - Zero all SQLite database files
    - Log destruction event to external location (if configured)

    Parameters
    ----------
    data_dir : str | Path
        Base data directory containing vault.key and other sensitive files.
    chromadb_dir : str | Path | None
        Path to ChromaDB directory. If ``None``, defaults to
        ``data_dir / "chromadb"``.
    external_log_path : str | Path | None
        Path to an external tamper-proof log file. If set, destruction
        events are logged here (outside the data directory).
    arm_timeout : float
        Seconds the armed state remains active before auto-disarming
        (default 60).
    overwrite_passes : int
        Number of random-overwrite passes on sensitive files (default 3).
    """

    _SENSITIVE_FILES = ("vault.key", "secrets.vault")
    _SQLITE_EXTENSIONS = (".db", ".sqlite", ".sqlite3")

    def __init__(
        self,
        data_dir: str | Path,
        chromadb_dir: str | Path | None = None,
        external_log_path: str | Path | None = None,
        arm_timeout: float = 60.0,
        overwrite_passes: int = 3,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._chromadb_dir = (
            Path(chromadb_dir) if chromadb_dir else self._data_dir / "chromadb"
        )
        self._external_log_path = (
            Path(external_log_path) if external_log_path else None
        )
        self._arm_timeout = arm_timeout
        self._overwrite_passes = overwrite_passes

        self._lock = threading.Lock()
        self._armed = False
        self._armed_at: float = 0.0
        self._triggered = False
        self._destruction_log: list[str] = []

    # ------------------------------------------------------------------
    # Arming / disarming
    # ------------------------------------------------------------------

    def arm(self, confirmation: str = "CONFIRM_SELF_DESTRUCT") -> bool:
        """Arm the self-destruct sequence.

        Parameters
        ----------
        confirmation : str
            Must be exactly ``"CONFIRM_SELF_DESTRUCT"`` to proceed.

        Returns
        -------
        bool
            ``True`` if armed successfully, ``False`` if confirmation
            string is incorrect.
        """
        if confirmation != "CONFIRM_SELF_DESTRUCT":
            logger.warning(
                "Self-destruct arm attempt with wrong confirmation string"
            )
            return False

        with self._lock:
            self._armed = True
            self._armed_at = time.time()
            logger.warning(
                "SELF-DESTRUCT ARMED. Will auto-disarm in %.0f seconds.",
                self._arm_timeout,
            )
            self._log_event("ARMED: Self-destruct sequence armed")
            return True

    def disarm(self) -> None:
        """Disarm the self-destruct sequence."""
        with self._lock:
            was_armed = self._armed
            self._armed = False
            self._armed_at = 0.0
            if was_armed:
                logger.info("Self-destruct DISARMED")
                self._log_event("DISARMED: Self-destruct sequence disarmed")

    @property
    def is_armed(self) -> bool:
        """Check if the self-destruct sequence is currently armed.

        Returns ``False`` if the arm timeout has expired.

        Returns
        -------
        bool
            ``True`` if armed and within the timeout window.
        """
        with self._lock:
            if not self._armed:
                return False
            elapsed = time.time() - self._armed_at
            if elapsed > self._arm_timeout:
                self._armed = False
                self._armed_at = 0.0
                logger.info("Self-destruct auto-disarmed (timeout expired)")
                self._log_event("AUTO-DISARMED: Arm timeout expired")
                return False
            return True

    # ------------------------------------------------------------------
    # Destruction sequence
    # ------------------------------------------------------------------

    def trigger(self) -> dict[str, list[str]]:
        """Execute the self-destruct sequence.

        Requires the system to be armed (via ``arm``). Performs:

        1. 3-pass random overwrite of sensitive vault files
        2. Purge ChromaDB directory
        3. Zero all SQLite databases in the data directory
        4. Log to external location if configured

        Returns
        -------
        dict[str, list[str]]
            Report of destroyed resources, with keys:
            ``overwritten``, ``purged_dirs``, ``zeroed_dbs``, ``errors``.

        Raises
        ------
        SelfDestructError
            If the system is not armed, the arm timeout expired,
            or the system was already triggered.
        """
        with self._lock:
            if self._triggered:
                raise SelfDestructError(
                    "Self-destruct already triggered in this session"
                )

            if not self._armed:
                raise SelfDestructError(
                    "Self-destruct is not armed. Call arm() first."
                )

            elapsed = time.time() - self._armed_at
            if elapsed > self._arm_timeout:
                self._armed = False
                raise SelfDestructError(
                    f"Arm timeout expired ({elapsed:.1f}s > {self._arm_timeout:.1f}s). "
                    "Re-arm to proceed."
                )

            self._triggered = True
            self._armed = False

        logger.critical("SELF-DESTRUCT TRIGGERED. Beginning destruction sequence.")
        self._log_event("TRIGGERED: Self-destruct sequence initiated")

        report: dict[str, list[str]] = {
            "overwritten": [],
            "purged_dirs": [],
            "zeroed_dbs": [],
            "errors": [],
        }

        # Phase 1: Overwrite sensitive files
        self._phase_overwrite_files(report)

        # Phase 2: Purge ChromaDB
        self._phase_purge_chromadb(report)

        # Phase 3: Zero SQLite databases
        self._phase_zero_databases(report)

        # Phase 4: Log to external location
        self._phase_external_log(report)

        logger.critical(
            "SELF-DESTRUCT COMPLETE. Overwritten: %d files, "
            "Purged: %d dirs, Zeroed: %d dbs, Errors: %d",
            len(report["overwritten"]),
            len(report["purged_dirs"]),
            len(report["zeroed_dbs"]),
            len(report["errors"]),
        )

        return report

    # ------------------------------------------------------------------
    # Destruction phases
    # ------------------------------------------------------------------

    def _phase_overwrite_files(self, report: dict[str, list[str]]) -> None:
        """Phase 1: 3-pass random overwrite of sensitive vault files."""
        for filename in self._SENSITIVE_FILES:
            file_path = self._data_dir / filename
            if not file_path.exists():
                continue
            try:
                self._secure_overwrite(file_path)
                report["overwritten"].append(str(file_path))
                self._log_event(f"OVERWRITTEN: {file_path}")
            except OSError as exc:
                msg = f"Failed to overwrite {file_path}: {exc}"
                report["errors"].append(msg)
                logger.error(msg)

        # Also overwrite any .vault files found recursively
        if self._data_dir.exists():
            for vault_file in self._data_dir.rglob("*.vault"):
                if str(vault_file) not in report["overwritten"]:
                    try:
                        self._secure_overwrite(vault_file)
                        report["overwritten"].append(str(vault_file))
                        self._log_event(f"OVERWRITTEN: {vault_file}")
                    except OSError as exc:
                        msg = f"Failed to overwrite {vault_file}: {exc}"
                        report["errors"].append(msg)
                        logger.error(msg)

    def _phase_purge_chromadb(self, report: dict[str, list[str]]) -> None:
        """Phase 2: Purge the ChromaDB directory."""
        if self._chromadb_dir.exists() and self._chromadb_dir.is_dir():
            try:
                shutil.rmtree(self._chromadb_dir)
                report["purged_dirs"].append(str(self._chromadb_dir))
                self._log_event(f"PURGED: {self._chromadb_dir}")
            except OSError as exc:
                msg = f"Failed to purge ChromaDB at {self._chromadb_dir}: {exc}"
                report["errors"].append(msg)
                logger.error(msg)

    def _phase_zero_databases(self, report: dict[str, list[str]]) -> None:
        """Phase 3: Zero all SQLite database files in the data directory."""
        if not self._data_dir.exists():
            return
        for ext in self._SQLITE_EXTENSIONS:
            for db_path in self._data_dir.rglob(f"*{ext}"):
                try:
                    self._zero_file(db_path)
                    report["zeroed_dbs"].append(str(db_path))
                    self._log_event(f"ZEROED: {db_path}")
                except OSError as exc:
                    msg = f"Failed to zero {db_path}: {exc}"
                    report["errors"].append(msg)
                    logger.error(msg)

    def _phase_external_log(self, report: dict[str, list[str]]) -> None:
        """Phase 4: Write destruction log to external tamper-proof location."""
        if self._external_log_path is None:
            return
        try:
            self._external_log_path.parent.mkdir(parents=True, exist_ok=True)
            log_content = "\n".join(self._destruction_log)
            with open(self._external_log_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"SELF-DESTRUCT LOG - {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
                f.write(f"{'='*60}\n")
                f.write(log_content)
                f.write("\n\nDestruction Report:\n")
                for key, items in report.items():
                    f.write(f"  {key}: {len(items)} items\n")
                    for item in items:
                        f.write(f"    - {item}\n")
                f.write(f"{'='*60}\n")
            self._log_event(f"EXTERNAL LOG: Written to {self._external_log_path}")
        except OSError as exc:
            msg = f"Failed to write external log: {exc}"
            report["errors"].append(msg)
            logger.error(msg)

    # ------------------------------------------------------------------
    # Low-level secure operations
    # ------------------------------------------------------------------

    def _secure_overwrite(self, file_path: Path) -> None:
        """Overwrite a file with random data for ``_overwrite_passes`` passes,
        then delete it.

        Parameters
        ----------
        file_path : Path
            File to securely destroy.
        """
        file_size = file_path.stat().st_size
        if file_size == 0:
            file_path.unlink()
            return

        for pass_num in range(self._overwrite_passes):
            with open(file_path, "wb") as f:
                f.write(os.urandom(file_size))
                f.flush()
                os.fsync(f.fileno())
            logger.debug(
                "Overwrite pass %d/%d on %s",
                pass_num + 1,
                self._overwrite_passes,
                file_path,
            )

        # Final deletion
        file_path.unlink()

    def _zero_file(self, file_path: Path) -> None:
        """Overwrite a file with zeros, then truncate to zero length.

        Parameters
        ----------
        file_path : Path
            File to zero out.
        """
        file_size = file_path.stat().st_size
        if file_size == 0:
            return

        with open(file_path, "wb") as f:
            f.write(b"\x00" * file_size)
            f.flush()
            os.fsync(f.fileno())

        # Truncate to zero
        with open(file_path, "wb") as f:
            f.truncate(0)
            f.flush()
            os.fsync(f.fileno())

    # ------------------------------------------------------------------
    # Internal logging
    # ------------------------------------------------------------------

    def _log_event(self, message: str) -> None:
        """Append a timestamped message to the internal destruction log."""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        self._destruction_log.append(f"[{timestamp}] {message}")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def was_triggered(self) -> bool:
        """Check if self-destruct has been triggered in this session."""
        return self._triggered

    @property
    def destruction_log(self) -> list[str]:
        """Return the internal destruction log (in-memory only)."""
        return list(self._destruction_log)

    @property
    def status(self) -> dict[str, object]:
        """Return current self-destruct status.

        Returns
        -------
        dict
            Keys: armed, triggered, arm_remaining_seconds, overwrite_passes.
        """
        with self._lock:
            remaining = 0.0
            if self._armed:
                remaining = max(
                    0.0,
                    self._arm_timeout - (time.time() - self._armed_at),
                )
            return {
                "armed": self._armed and remaining > 0,
                "triggered": self._triggered,
                "arm_remaining_seconds": round(remaining, 1),
                "overwrite_passes": self._overwrite_passes,
            }
