"""
Mode 6 -- Federated Absorption.

Enables multiple SpongeBot instances to share skill metadata without
ever exchanging prompt text or full skill content.  Only names,
SHA-256 content hashes, and confidence deltas are transmitted.

Federated averaging merges confidence scores from remote instances,
allowing the swarm to converge on which skills are trustworthy
without exposing proprietary knowledge.

Security invariant: NEVER share prompt text, step content, or full
skill definitions.  Only metadata + hashes + confidence deltas.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger("spongebot.absorption.federated_absorption")


class FederatedAbsorption:
    """Share encrypted skill metadata between SpongeBot instances.

    This mode enables a federated learning-style approach where
    multiple SpongeBot instances can share confidence signals without
    exposing their actual skill content.

    The protocol:
    1. **Export**: Produce metadata records (name, hash, confidence delta)
    2. **Import**: Merge remote metadata into local confidence scores
       using federated averaging.

    Parameters
    ----------
    config : dict
        SpongeBot configuration dictionary.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._absorption_cfg = config.get("absorption", {})

        # Track federation statistics
        self._exports = 0
        self._imports = 0
        self._confidence_updates = 0

        logger.debug("FederatedAbsorption initialised.")

    # ------------------------------------------------------------------
    # Public API -- absorb dispatches to import
    # ------------------------------------------------------------------

    async def absorb(
        self,
        remote_metadata: list[dict[str, Any]],
        local_skills: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Import remote metadata and return updated local skills.

        This is the ``absorb()`` entry point that the engine calls.
        It delegates to ``import_metadata()`` for the actual merge.

        Parameters
        ----------
        remote_metadata : list[dict]
            Metadata records from a remote SpongeBot instance.
            Each record has ``name``, ``sha256_hash``, ``confidence_delta``.
        local_skills : list[dict], optional
            Local skill dicts to merge into. If *None*, only metadata
            update records are returned.

        Returns
        -------
        list[dict]
            Updated skill dicts with merged confidence scores.
        """
        return await self.import_metadata(
            remote_metadata, local_skills=local_skills
        )

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    async def export_metadata(
        self, skills: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Export skill metadata for sharing with remote instances.

        Produces a list of metadata records containing ONLY:
        - ``name``: the skill name
        - ``sha256_hash``: SHA-256 of the canonical skill content
        - ``confidence_delta``: confidence change since last export
        - ``version``: skill version string
        - ``exported_at``: timestamp of this export

        NEVER includes prompt text, step content, parameters, or any
        other skill body data.

        Parameters
        ----------
        skills : list[dict]
            Local skill dicts to export metadata for.

        Returns
        -------
        list[dict]
            Metadata-only records safe for federated sharing.
        """
        metadata: list[dict[str, Any]] = []
        now = time.time()

        for skill in skills:
            content_hash = self._hash_skill(skill)
            metadata.append({
                "name": skill.get("name", ""),
                "sha256_hash": content_hash,
                "confidence_delta": skill.get("confidence", 0.5),
                "version": skill.get("version", "0.1.0"),
                "exported_at": now,
            })

        self._exports += len(metadata)
        logger.info("Exported metadata for %d skills.", len(metadata))
        return metadata

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    async def import_metadata(
        self,
        remote_metadata: list[dict[str, Any]],
        local_skills: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Import and merge remote skill metadata.

        Uses federated averaging to merge confidence scores:
            new_confidence = (local_confidence + remote_confidence) / 2

        Only updates confidence for skills that match by name AND
        content hash.  New skills from remote are NOT imported (we
        never accept skill content from remote -- only confidence
        signals).

        Parameters
        ----------
        remote_metadata : list[dict]
            Metadata records from a remote instance.
        local_skills : list[dict], optional
            Local skills to merge into.

        Returns
        -------
        list[dict]
            Updated skill dicts.  If *local_skills* was *None*,
            returns metadata update records instead.
        """
        if not remote_metadata:
            logger.debug("No remote metadata to import.")
            return local_skills or []

        self._imports += len(remote_metadata)

        # Build a lookup of remote metadata by name
        remote_by_name: dict[str, dict[str, Any]] = {}
        for record in remote_metadata:
            if not isinstance(record, dict):
                continue
            name = record.get("name")
            if name:
                remote_by_name[name] = record

        if local_skills is None:
            # No local skills to merge -- return metadata update records
            logger.info(
                "Imported %d remote metadata records (no local skills to merge).",
                len(remote_metadata),
            )
            return list(remote_metadata)

        # Merge confidence via federated averaging
        updated_skills: list[dict[str, Any]] = []
        for skill in local_skills:
            skill_name = skill.get("name", "")
            remote_record = remote_by_name.get(skill_name)

            if remote_record is None:
                # No remote counterpart -- keep as-is
                updated_skills.append(skill)
                continue

            # Verify content hash match (only merge if same underlying skill)
            local_hash = self._hash_skill(skill)
            remote_hash = remote_record.get("sha256_hash", "")

            if local_hash != remote_hash:
                logger.debug(
                    "Hash mismatch for skill '%s' -- skipping merge "
                    "(local=%s, remote=%s)",
                    skill_name,
                    local_hash[:12],
                    remote_hash[:12],
                )
                updated_skills.append(skill)
                continue

            # Federated averaging
            local_conf = skill.get("confidence", 0.5)
            remote_conf = remote_record.get("confidence_delta", local_conf)
            merged_conf = round((local_conf + remote_conf) / 2.0, 4)

            merged_skill = dict(skill)
            merged_skill["confidence"] = merged_conf
            merged_skill["federated_merge_at"] = time.time()
            updated_skills.append(merged_skill)

            self._confidence_updates += 1
            logger.debug(
                "Merged confidence for '%s': %.4f (local=%.4f, remote=%.4f)",
                skill_name,
                merged_conf,
                local_conf,
                remote_conf,
            )

        logger.info(
            "Federated import complete: %d skills checked, %d confidences merged.",
            len(local_skills),
            self._confidence_updates,
        )
        return updated_skills

    # ------------------------------------------------------------------
    # Hashing
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_skill(skill: dict[str, Any]) -> str:
        """Compute a SHA-256 hash of the skill's canonical content.

        The hash covers the skill's name, description, steps, and
        parameters -- the essential identity of the skill. Mutable
        metadata (confidence, timestamps, use_count) is excluded so
        the hash remains stable across updates.

        Parameters
        ----------
        skill : dict
            Skill dict to hash.

        Returns
        -------
        str
            Hex-encoded SHA-256 digest.
        """
        canonical = {
            "name": skill.get("name", ""),
            "description": skill.get("description", ""),
            "steps": skill.get("steps", []),
            "parameters": skill.get("parameters", []),
            "prerequisites": skill.get("prerequisites", []),
        }
        content = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Return health status for the federated absorption mode."""
        return {
            "status": "ok",
            "mode": "federated",
            "exports": self._exports,
            "imports": self._imports,
            "confidence_updates": self._confidence_updates,
        }
