"""
Skill DAG -- networkx.DiGraph-based directed acyclic graph of absorbed skills.

Every capability SpongeBot learns is stored as a SkillNode in this DAG.  Edges
encode relationships: ``requires``, ``composes``, ``conflicts_with``, and
``anti_skill``.  Confidence decays with a 7-day half-life so unused skills
gradually fade, and pruning archives anything below threshold for 7+ days.

Absorbed from IT_NEXUS cortex.py patterns: config-driven construction,
async boot/shutdown lifecycle, graceful JSON persistence.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx

logger = logging.getLogger("spongebot.skills.dag")

# ---------------------------------------------------------------------------
# SkillNode dataclass
# ---------------------------------------------------------------------------


@dataclass
class SkillNode:
    """A single skill vertex in the DAG."""

    name: str
    description: str
    skill_type: str  # "atomic", "composed", "goal", "anti_skill"
    parameters: list[dict[str, Any]] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    confidence: float = 0.5
    version: str = "1.0.0"
    absorbed_from: str = ""
    absorption_mode: str = ""
    created_at: float = field(default_factory=time.time)
    last_used: float = 0.0
    use_count: int = 0
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict for JSON persistence."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillNode:
        """Reconstruct a SkillNode from a serialised dict."""
        # Filter to only known fields so stale keys don't blow up
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


# ---------------------------------------------------------------------------
# Edge type constants
# ---------------------------------------------------------------------------

_VALID_EDGE_TYPES = frozenset({"requires", "composes", "conflicts_with", "anti_skill"})


# ---------------------------------------------------------------------------
# SkillDAG
# ---------------------------------------------------------------------------


class SkillDAG:
    """Directed Acyclic Graph of absorbed skills with confidence decay and pruning.

    Parameters
    ----------
    config : dict
        Full SpongeBot configuration.  The ``skills`` section is used:
        - ``confidence_decay_half_life_days`` (default 7)
        - ``prune_threshold`` (default 0.15)
        - ``prune_after_days`` (default 7)
    persist_path : str | Path | None
        Explicit path for the JSON persistence file.  If *None* the DAG
        derives a path from ``config["spongebot"]["data_dir"]``.
    """

    def __init__(
        self,
        config: dict[str, Any],
        persist_path: str | Path | None = None,
    ) -> None:
        skills_cfg = config.get("skills", {})

        half_life_days: float = skills_cfg.get("confidence_decay_half_life_days", 7)
        self._half_life: float = half_life_days * 24 * 3600  # seconds
        self._prune_threshold: float = skills_cfg.get("prune_threshold", 0.15)
        self._prune_after_seconds: float = skills_cfg.get("prune_after_days", 7) * 24 * 3600

        # Resolve persistence path
        if persist_path is not None:
            self._persist_path: Path = Path(persist_path)
        else:
            data_dir = config.get("spongebot", {}).get("data_dir", "data")
            self._persist_path = Path(data_dir) / "skill_dag.json"

        self._graph: nx.DiGraph = nx.DiGraph()
        self._cold_storage: list[dict[str, Any]] = []  # Archived pruned skills

        # Track when each skill first dipped below threshold (for prune timer)
        self._below_threshold_since: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def boot(self) -> None:
        """Load persisted DAG from disk."""
        self._load()
        logger.info(
            "SkillDAG booted: %d skills, %d edges, %d archived",
            self._graph.number_of_nodes(),
            self._graph.number_of_edges(),
            len(self._cold_storage),
        )

    async def shutdown(self) -> None:
        """Persist DAG to disk on shutdown."""
        self.save()
        logger.info("SkillDAG saved and shut down.")

    async def health_check(self) -> dict[str, Any]:
        """Return DAG health metrics."""
        return {
            "status": "ok",
            "component": "skills",
            "skill_count": self._graph.number_of_nodes(),
            "edge_count": self._graph.number_of_edges(),
            "cold_storage_count": len(self._cold_storage),
            "is_dag": nx.is_directed_acyclic_graph(self._graph),
        }

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_skill(self, skill: SkillNode) -> str:
        """Add a skill node to the DAG.

        Prerequisite edges are created automatically.  Raises ``ValueError``
        if adding the skill would create a cycle.

        Returns the node name (used as the unique ID).
        """
        name = skill.name

        if name in self._graph:
            logger.warning("Skill '%s' already exists -- updating instead.", name)
            self._graph.nodes[name]["data"] = skill
            return name

        # Tentatively add node + prerequisite edges
        self._graph.add_node(name, data=skill)

        for prereq in skill.prerequisites:
            if prereq in self._graph:
                self._graph.add_edge(prereq, name, edge_type="requires")

        # Validate acyclicity
        if not nx.is_directed_acyclic_graph(self._graph):
            # Roll back
            self._graph.remove_node(name)
            raise ValueError(
                f"Adding skill '{name}' with prerequisites {skill.prerequisites} "
                "would create a cycle in the DAG."
            )

        logger.info("Added skill '%s' (type=%s, confidence=%.2f).", name, skill.skill_type, skill.confidence)
        return name

    def get_skill(self, name: str) -> SkillNode | None:
        """Retrieve a skill by name, or *None* if not found."""
        node_data = self._graph.nodes.get(name)
        if node_data is None:
            return None
        return node_data.get("data")

    def update_skill(self, name: str, **updates: Any) -> None:
        """Update fields on an existing skill.

        Raises ``KeyError`` if the skill does not exist.
        """
        skill = self.get_skill(name)
        if skill is None:
            raise KeyError(f"Skill '{name}' not found in the DAG.")

        for key, value in updates.items():
            if hasattr(skill, key):
                setattr(skill, key, value)
            else:
                logger.warning("Ignoring unknown skill field '%s'.", key)

        self._graph.nodes[name]["data"] = skill
        logger.debug("Updated skill '%s': %s", name, list(updates.keys()))

    def remove_skill(self, name: str) -> None:
        """Remove a skill and all its edges from the DAG.

        Raises ``KeyError`` if the skill does not exist.
        """
        if name not in self._graph:
            raise KeyError(f"Skill '{name}' not found in the DAG.")

        self._graph.remove_node(name)
        self._below_threshold_since.pop(name, None)
        logger.info("Removed skill '%s'.", name)

    # ------------------------------------------------------------------
    # Edge management
    # ------------------------------------------------------------------

    def add_edge(self, from_skill: str, to_skill: str, edge_type: str) -> None:
        """Add a typed directed edge between two skills.

        Parameters
        ----------
        from_skill : str
            Source skill name.
        to_skill : str
            Target skill name.
        edge_type : str
            One of ``"requires"``, ``"composes"``, ``"conflicts_with"``,
            ``"anti_skill"``.

        Raises
        ------
        KeyError
            If either skill does not exist in the DAG.
        ValueError
            If ``edge_type`` is invalid or if the edge would create a cycle
            (for directional edge types ``requires`` and ``composes``).
        """
        if edge_type not in _VALID_EDGE_TYPES:
            raise ValueError(
                f"Invalid edge_type '{edge_type}'. "
                f"Must be one of {sorted(_VALID_EDGE_TYPES)}."
            )

        if from_skill not in self._graph:
            raise KeyError(f"Source skill '{from_skill}' not found in the DAG.")
        if to_skill not in self._graph:
            raise KeyError(f"Target skill '{to_skill}' not found in the DAG.")

        # Add edge tentatively
        self._graph.add_edge(from_skill, to_skill, edge_type=edge_type)

        # Only directional edges can create cycles
        if edge_type in ("requires", "composes"):
            if not nx.is_directed_acyclic_graph(self._graph):
                self._graph.remove_edge(from_skill, to_skill)
                raise ValueError(
                    f"Edge '{from_skill}' -> '{to_skill}' (type={edge_type}) "
                    "would create a cycle."
                )

        logger.debug("Added edge '%s' -> '%s' (type=%s).", from_skill, to_skill, edge_type)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def find_relevant(
        self,
        query: str,
        tags: list[str] | None = None,
        min_confidence: float = 0.0,
    ) -> list[SkillNode]:
        """Find skills relevant to *query* using keyword + tag + confidence filters.

        Matches are ranked by a simple relevance score: keyword overlap count
        in name, description, steps, and tags.
        """
        query_words = set(query.lower().split())
        results: list[tuple[int, SkillNode]] = []

        for _, node_data in self._graph.nodes(data=True):
            skill: SkillNode = node_data["data"]

            # Confidence gate
            if skill.confidence < min_confidence:
                continue

            # Tag filter
            if tags:
                if not set(tags) & set(skill.tags):
                    continue

            # Keyword relevance score
            searchable = " ".join([
                skill.name,
                skill.description,
                " ".join(skill.steps),
                " ".join(skill.tags),
            ]).lower()
            searchable_words = set(searchable.split())
            overlap = len(query_words & searchable_words)

            if overlap > 0:
                results.append((overlap, skill))

        # Sort by relevance descending, then by confidence descending
        results.sort(key=lambda pair: (pair[0], pair[1].confidence), reverse=True)
        return [skill for _, skill in results]

    def get_composition(self, goal_skill: str) -> list[SkillNode]:
        """Return all skills needed to compose *goal_skill* in topological order.

        Follows ``requires`` and ``composes`` edges backwards to collect the
        full dependency tree, then returns them in execution order (topological
        sort of the subgraph).

        Raises ``KeyError`` if *goal_skill* does not exist.
        """
        if goal_skill not in self._graph:
            raise KeyError(f"Skill '{goal_skill}' not found in the DAG.")

        # Collect all ancestors (transitively)
        ancestors = nx.ancestors(self._graph, goal_skill)
        ancestors.add(goal_skill)

        subgraph = self._graph.subgraph(ancestors)
        ordered_names = list(nx.topological_sort(subgraph))

        return [
            self._graph.nodes[name]["data"]
            for name in ordered_names
            if "data" in self._graph.nodes[name]
        ]

    def get_conflicts(self, skill_name: str) -> list[str]:
        """Return names of skills that conflict with *skill_name*.

        Looks for ``conflicts_with`` edges in both directions.
        """
        if skill_name not in self._graph:
            return []

        conflicts: list[str] = []
        # Outgoing conflicts_with edges
        for _, target, data in self._graph.out_edges(skill_name, data=True):
            if data.get("edge_type") == "conflicts_with":
                conflicts.append(target)
        # Incoming conflicts_with edges
        for source, _, data in self._graph.in_edges(skill_name, data=True):
            if data.get("edge_type") == "conflicts_with":
                conflicts.append(source)

        return conflicts

    def get_anti_skills(self, skill_name: str) -> list[SkillNode]:
        """Return anti-skills related to *skill_name*.

        Follows ``anti_skill`` edges in both directions.
        """
        if skill_name not in self._graph:
            return []

        anti_names: set[str] = set()
        for _, target, data in self._graph.out_edges(skill_name, data=True):
            if data.get("edge_type") == "anti_skill":
                anti_names.add(target)
        for source, _, data in self._graph.in_edges(skill_name, data=True):
            if data.get("edge_type") == "anti_skill":
                anti_names.add(source)

        return [
            self._graph.nodes[n]["data"]
            for n in anti_names
            if n in self._graph and "data" in self._graph.nodes[n]
        ]

    # ------------------------------------------------------------------
    # Confidence management
    # ------------------------------------------------------------------

    def decay_confidence(self) -> int:
        """Apply exponential decay to all skills based on time since last use.

        Uses a 7-day half-life: ``confidence *= 2^(-elapsed / half_life)``.
        Only decays skills that have been used at least once (``last_used > 0``).

        Returns the count of skills whose confidence was reduced.
        """
        now = time.time()
        decayed_count = 0

        for _, node_data in self._graph.nodes(data=True):
            skill: SkillNode = node_data["data"]

            if skill.last_used <= 0:
                # Never used -- decay from creation time instead
                reference = skill.created_at
            else:
                reference = skill.last_used

            elapsed = now - reference
            if elapsed <= 0:
                continue

            decay_factor = math.pow(2, -elapsed / self._half_life)
            new_confidence = skill.confidence * decay_factor

            if new_confidence < skill.confidence:
                skill.confidence = max(0.0, new_confidence)
                decayed_count += 1

                # Track how long skill has been below threshold
                if skill.confidence < self._prune_threshold:
                    if skill.name not in self._below_threshold_since:
                        self._below_threshold_since[skill.name] = now
                else:
                    self._below_threshold_since.pop(skill.name, None)

        if decayed_count > 0:
            logger.info("Decayed confidence on %d skills.", decayed_count)

        return decayed_count

    def boost_confidence(self, name: str, amount: float = 0.1) -> None:
        """Increase confidence after successful use.

        Also updates ``last_used`` and increments ``use_count``.
        """
        skill = self.get_skill(name)
        if skill is None:
            logger.warning("Cannot boost unknown skill '%s'.", name)
            return

        skill.confidence = min(1.0, skill.confidence + amount)
        skill.last_used = time.time()
        skill.use_count += 1

        # No longer below threshold
        self._below_threshold_since.pop(name, None)

        logger.debug(
            "Boosted skill '%s' to confidence=%.3f (uses=%d).",
            name, skill.confidence, skill.use_count,
        )

    def penalize_confidence(self, name: str, amount: float = 0.1) -> None:
        """Decrease confidence after failure or anti-skill match."""
        skill = self.get_skill(name)
        if skill is None:
            logger.warning("Cannot penalize unknown skill '%s'.", name)
            return

        skill.confidence = max(0.0, skill.confidence - amount)

        if skill.confidence < self._prune_threshold:
            if name not in self._below_threshold_since:
                self._below_threshold_since[name] = time.time()

        logger.debug("Penalized skill '%s' to confidence=%.3f.", name, skill.confidence)

    # ------------------------------------------------------------------
    # Pruning
    # ------------------------------------------------------------------

    def prune(self) -> list[dict[str, Any]]:
        """Archive skills below threshold for 7+ consecutive days.

        Pruned skills are moved to cold storage (in-memory list, also
        persisted).  Returns the list of pruned skill dicts.
        """
        now = time.time()
        to_prune: list[str] = []

        for name, since in list(self._below_threshold_since.items()):
            if name not in self._graph:
                self._below_threshold_since.pop(name, None)
                continue

            elapsed = now - since
            if elapsed >= self._prune_after_seconds:
                to_prune.append(name)

        pruned: list[dict[str, Any]] = []
        for name in to_prune:
            skill = self.get_skill(name)
            if skill is not None:
                archived = skill.to_dict()
                archived["pruned_at"] = now
                self._cold_storage.append(archived)
                pruned.append(archived)

            self._graph.remove_node(name)
            self._below_threshold_since.pop(name, None)
            logger.info("Pruned skill '%s' to cold storage.", name)

        if pruned:
            logger.info("Pruned %d skills to cold storage.", len(pruned))

        return pruned

    # ------------------------------------------------------------------
    # Versioning
    # ------------------------------------------------------------------

    def bump_version(self, name: str, bump: str = "patch") -> str:
        """Bump the semantic version of a skill.

        Parameters
        ----------
        name : str
            Skill name.
        bump : str
            ``"major"``, ``"minor"``, or ``"patch"``.

        Returns
        -------
        str
            The new version string.
        """
        skill = self.get_skill(name)
        if skill is None:
            raise KeyError(f"Skill '{name}' not found in the DAG.")

        parts = skill.version.split(".")
        if len(parts) != 3:
            parts = ["1", "0", "0"]

        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

        if bump == "major":
            major += 1
            minor = 0
            patch = 0
        elif bump == "minor":
            minor += 1
            patch = 0
        elif bump == "patch":
            patch += 1
        else:
            raise ValueError(f"Invalid bump type '{bump}'. Use 'major', 'minor', or 'patch'.")

        new_version = f"{major}.{minor}.{patch}"
        skill.version = new_version
        logger.debug("Bumped skill '%s' to version %s.", name, new_version)
        return new_version

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Persist the DAG and cold storage to a JSON file."""
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)

        nodes: list[dict[str, Any]] = []
        for name, node_data in self._graph.nodes(data=True):
            skill: SkillNode = node_data["data"]
            nodes.append(skill.to_dict())

        edges: list[dict[str, str]] = []
        for source, target, edge_data in self._graph.edges(data=True):
            edges.append({
                "from": source,
                "to": target,
                "edge_type": edge_data.get("edge_type", "requires"),
            })

        payload = {
            "version": "1.0.0",
            "saved_at": time.time(),
            "nodes": nodes,
            "edges": edges,
            "cold_storage": self._cold_storage,
            "below_threshold_since": self._below_threshold_since,
        }

        try:
            with open(self._persist_path, "w") as fh:
                json.dump(payload, fh, indent=2)
            logger.debug("Saved DAG to %s (%d nodes, %d edges).", self._persist_path, len(nodes), len(edges))
        except OSError as exc:
            logger.error("Failed to save DAG to %s: %s", self._persist_path, exc)

    def _load(self) -> None:
        """Load the DAG from the persistence file, if it exists."""
        if not self._persist_path.exists():
            logger.debug("No persisted DAG at %s -- starting fresh.", self._persist_path)
            return

        try:
            with open(self._persist_path, "r") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Failed to load DAG from %s: %s -- starting fresh.", self._persist_path, exc)
            return

        # Reconstruct nodes
        for node_dict in payload.get("nodes", []):
            try:
                skill = SkillNode.from_dict(node_dict)
                self._graph.add_node(skill.name, data=skill)
            except Exception as exc:
                logger.warning("Skipping malformed skill node: %s", exc)

        # Reconstruct edges
        for edge_dict in payload.get("edges", []):
            src = edge_dict.get("from", "")
            tgt = edge_dict.get("to", "")
            etype = edge_dict.get("edge_type", "requires")
            if src in self._graph and tgt in self._graph:
                self._graph.add_edge(src, tgt, edge_type=etype)
            else:
                logger.warning(
                    "Skipping edge '%s' -> '%s' -- endpoint missing.", src, tgt,
                )

        # Restore cold storage
        self._cold_storage = payload.get("cold_storage", [])

        # Restore threshold tracking
        raw_below = payload.get("below_threshold_since", {})
        self._below_threshold_since = {
            str(k): float(v) for k, v in raw_below.items()
        }

        logger.info(
            "Loaded DAG from %s: %d nodes, %d edges, %d archived.",
            self._persist_path,
            self._graph.number_of_nodes(),
            self._graph.number_of_edges(),
            len(self._cold_storage),
        )

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return summary statistics about the DAG."""
        confidences: list[float] = []
        type_counts: dict[str, int] = {}
        total_uses = 0

        for _, node_data in self._graph.nodes(data=True):
            skill: SkillNode = node_data["data"]
            confidences.append(skill.confidence)
            type_counts[skill.skill_type] = type_counts.get(skill.skill_type, 0) + 1
            total_uses += skill.use_count

        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        edge_type_counts: dict[str, int] = {}
        for _, _, edge_data in self._graph.edges(data=True):
            etype = edge_data.get("edge_type", "unknown")
            edge_type_counts[etype] = edge_type_counts.get(etype, 0) + 1

        return {
            "node_count": self._graph.number_of_nodes(),
            "edge_count": self._graph.number_of_edges(),
            "avg_confidence": round(avg_confidence, 4),
            "min_confidence": round(min(confidences), 4) if confidences else 0.0,
            "max_confidence": round(max(confidences), 4) if confidences else 0.0,
            "total_uses": total_uses,
            "type_counts": type_counts,
            "edge_type_counts": edge_type_counts,
            "cold_storage_count": len(self._cold_storage),
            "below_threshold_count": len(self._below_threshold_since),
            "is_dag": nx.is_directed_acyclic_graph(self._graph),
        }

    # ------------------------------------------------------------------
    # Convenience used by SpongeBot orchestrator pipeline
    # ------------------------------------------------------------------

    async def find_skills(
        self, user_input: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Pipeline-compatible query method called by SpongeBot.process().

        Returns a list of dicts (not SkillNode objects) for easy serialisation.
        """
        skills = self.find_relevant(
            user_input,
            tags=kwargs.get("tags"),
            min_confidence=kwargs.get("min_confidence", 0.2),
        )
        return [s.to_dict() for s in skills[:10]]
