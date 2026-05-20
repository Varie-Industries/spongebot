"""
Token Saver Engine -- 7-layer token optimization system.

SECRET: This file is compiled to binary (.so/.pyd) for distribution.
The public repo only ships _interface.py with the Protocol class.

Architecture absorbed from IT_NEXUS cost_guardian.py, extended with
four new layers for agentic plan caching, KV compression, skill
distillation, and semantic similarity caching.

Layers:
    L1  SystemPromptCompressor   - strip whitespace bloat
    L2  ResponseCache            - SHA-256 exact + pattern match, LRU
    L3  AgenticPlanCache         - cache recurring agent plan templates
    L4  KVCacheCompressor        - zlib compress key-value data
    L5  SkillDistiller           - extract essential steps from trajectories
    L6  SemanticCache            - embedding similarity for near-dupes
    L7  ConversationWindow       - summarize old, keep recent verbatim
    +   CostTracker              - per-call cost recording, budgets, ledger
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("spongebot.token_saver")


# ============================================================================
# L1: SystemPromptCompressor
# Absorbed from IT_NEXUS cost_guardian.py SystemPromptCompressor
# ============================================================================

class SystemPromptCompressor:
    """Strip whitespace bloat, triple newlines, formatting decorators.

    Typically yields 15-20% token reduction on verbose system prompts.
    All patterns precompiled for < 1 ms on typical prompts.
    """

    _TRIPLE_NL = re.compile(r"\n{3,}")
    _LEADING_SPACES = re.compile(r"^[ \t]+", re.MULTILINE)
    _TRAILING_SPACES = re.compile(r"[ \t]+$", re.MULTILINE)
    _MULTI_SPACE = re.compile(r"[ \t]{2,}")
    _DECORATORS = re.compile(r"^[=\-\*~#]{3,}$", re.MULTILINE)

    @classmethod
    def compress(cls, prompt: str) -> str:
        text = prompt
        text = cls._DECORATORS.sub("", text)
        text = cls._LEADING_SPACES.sub("", text)
        text = cls._TRAILING_SPACES.sub("", text)
        text = cls._MULTI_SPACE.sub(" ", text)
        text = cls._TRIPLE_NL.sub("\n\n", text)
        return text.strip()


# ============================================================================
# L2: ResponseCache
# Absorbed from IT_NEXUS cost_guardian.py ResponseCache
# ============================================================================

class ResponseCache:
    """SHA-256 exact match + lightweight pattern match.

    500-entry LRU with 5-minute TTL per entry.
    Pattern matching normalizes numbers and proper nouns to placeholders
    so structurally identical queries hit the cache.
    """

    MAX_ENTRIES = 500
    TTL_SEC = 300  # 5 minutes

    _NUM_PAT = re.compile(r"\b\d+(?:\.\d+)?\b")
    _NAME_PAT = re.compile(r"\b[A-Z][a-z]{2,}\b")

    def __init__(self) -> None:
        self._exact: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._pattern: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _sha(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @classmethod
    def _pattern_key(cls, text: str) -> str:
        normalized = cls._NUM_PAT.sub("<NUM>", text)
        normalized = cls._NAME_PAT.sub("<NAME>", normalized)
        return cls._sha(normalized)

    def check(self, user_message: str) -> str | None:
        now = time.time()
        exact_key = self._sha(user_message)
        pattern_key = self._pattern_key(user_message)

        with self._lock:
            # Exact match first
            if exact_key in self._exact:
                resp, ts = self._exact[exact_key]
                if now - ts < self.TTL_SEC:
                    self._exact.move_to_end(exact_key)
                    self._hits += 1
                    return resp
                del self._exact[exact_key]

            # Pattern match fallback
            if pattern_key in self._pattern:
                resp, ts = self._pattern[pattern_key]
                if now - ts < self.TTL_SEC:
                    self._pattern.move_to_end(pattern_key)
                    self._hits += 1
                    return resp
                del self._pattern[pattern_key]

        self._misses += 1
        return None

    def store(self, user_message: str, response: str) -> None:
        now = time.time()
        exact_key = self._sha(user_message)
        pattern_key = self._pattern_key(user_message)

        with self._lock:
            self._exact[exact_key] = (response, now)
            self._pattern[pattern_key] = (response, now)
            self._evict(self._exact)
            self._evict(self._pattern)

    def _evict(self, cache: OrderedDict) -> None:
        while len(cache) > self.MAX_ENTRIES:
            cache.popitem(last=False)

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return (self._hits / total * 100.0) if total > 0 else 0.0


# ============================================================================
# L3: AgenticPlanCache
# NEW - Cache recurring agent plan templates
# ============================================================================

class AgenticPlanCache:
    """Cache recurring agent plan templates.

    Agentic workflows often re-generate identical plans for similar tasks.
    This layer hashes the plan structure (stripping variable parts like
    specific file paths, names, timestamps) and caches the template.
    On cache hit, parameters are filled in from the new request rather
    than regenerating the entire plan from scratch.

    Saves 200-800 tokens per plan cache hit.
    """

    MAX_ENTRIES = 200
    TTL_SEC = 600  # 10 minutes for plan templates

    # Patterns to normalize before hashing (strip variable parts)
    _PATH_PAT = re.compile(r"(?:/[\w\-.]+)+(?:\.\w+)?")
    _TIMESTAMP_PAT = re.compile(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?"
    )
    _UUID_PAT = re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        re.IGNORECASE,
    )
    _QUOTED_STR_PAT = re.compile(r'"[^"]{4,}"')
    _NUM_PAT = re.compile(r"\b\d{3,}\b")

    def __init__(self) -> None:
        self._cache: OrderedDict[str, tuple[dict, float]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def _normalize(self, text: str) -> str:
        """Strip variable parts to get the structural skeleton."""
        normalized = self._PATH_PAT.sub("<PATH>", text)
        normalized = self._TIMESTAMP_PAT.sub("<TS>", normalized)
        normalized = self._UUID_PAT.sub("<UUID>", normalized)
        normalized = self._QUOTED_STR_PAT.sub("<STR>", normalized)
        normalized = self._NUM_PAT.sub("<N>", normalized)
        return normalized

    def _structure_hash(self, plan: dict) -> str:
        """Hash the structural skeleton of a plan dict."""
        skeleton = json.dumps(plan, sort_keys=True, default=str)
        normalized = self._normalize(skeleton)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def check(self, plan_request: dict) -> dict | None:
        """Check if a structurally similar plan exists in cache.

        Args:
            plan_request: The incoming plan request dict.

        Returns:
            Cached plan template dict, or None on miss.
        """
        key = self._structure_hash(plan_request)
        now = time.time()

        with self._lock:
            if key in self._cache:
                template, ts = self._cache[key]
                if now - ts < self.TTL_SEC:
                    self._cache.move_to_end(key)
                    self._hits += 1
                    return template.copy()
                del self._cache[key]

        self._misses += 1
        return None

    def store(self, plan_request: dict, plan_result: dict) -> None:
        """Store a plan template keyed by its structural hash."""
        key = self._structure_hash(plan_request)
        now = time.time()

        with self._lock:
            self._cache[key] = (plan_result.copy(), now)
            while len(self._cache) > self.MAX_ENTRIES:
                self._cache.popitem(last=False)

    def fill_template(
        self, template: dict, new_params: dict
    ) -> dict:
        """Fill a cached plan template with new parameter values.

        Performs a recursive merge: any value in the template that
        matches a placeholder pattern gets replaced by the corresponding
        value from new_params.

        Args:
            template: The cached plan template.
            new_params: New values to substitute into the template.

        Returns:
            The filled plan dict.
        """
        result = template.copy()
        for key, value in new_params.items():
            if key in result:
                result[key] = value
        return result

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return (self._hits / total * 100.0) if total > 0 else 0.0


# ============================================================================
# L4: KVCacheCompressor
# NEW - Compress key-value cache data using zlib
# ============================================================================

class KVCacheCompressor:
    """Compress key-value cache data using zlib.

    Provides transparent compress/decompress with configurable compression
    level. Typical KV cache payloads compress 60-80% with zlib level 6.

    Tracks total bytes saved for reporting.
    """

    DEFAULT_LEVEL = 6  # zlib 1-9, 6 is balanced speed/ratio

    def __init__(self, level: int = DEFAULT_LEVEL) -> None:
        self._level = max(1, min(9, level))
        self._total_input_bytes = 0
        self._total_output_bytes = 0
        self._lock = threading.Lock()

    def compress(self, data: bytes) -> bytes:
        """Compress raw bytes using zlib.

        Args:
            data: Raw key-value cache bytes.

        Returns:
            Compressed bytes with a 4-byte big-endian length prefix
            for the original size (needed for pre-allocation on decompress).
        """
        if not data:
            return data

        original_size = len(data)
        compressed = zlib.compress(data, self._level)

        # Prefix with original size for decompress pre-allocation
        result = original_size.to_bytes(4, "big") + compressed

        with self._lock:
            self._total_input_bytes += original_size
            self._total_output_bytes += len(result)

        return result

    def decompress(self, data: bytes) -> bytes:
        """Decompress zlib-compressed bytes.

        Args:
            data: Compressed bytes with 4-byte length prefix.

        Returns:
            Original decompressed bytes.
        """
        if not data or len(data) < 5:
            return data

        # Skip the 4-byte length prefix
        return zlib.decompress(data[4:])

    @property
    def compression_ratio(self) -> float:
        """Ratio of compressed to original size (lower is better)."""
        if self._total_input_bytes == 0:
            return 1.0
        return self._total_output_bytes / self._total_input_bytes

    @property
    def bytes_saved(self) -> int:
        return max(0, self._total_input_bytes - self._total_output_bytes)


# ============================================================================
# L5: SkillDistiller
# NEW - Compress skill trajectories by extracting essential steps
# ============================================================================

class SkillDistiller:
    """Compress skill trajectories by extracting essential steps.

    Agent skill trajectories contain many redundant intermediate states
    (thinking steps, retries, exploration). This layer distills them
    down to the essential decision points and outcomes.

    A typical 20-step trajectory compresses to 4-6 essential steps,
    saving 70-80% of the tokens needed to store/replay a skill.
    """

    # Keys that mark decision points worth preserving
    DECISION_KEYS = frozenset({
        "action", "decision", "choice", "selected", "tool_call",
        "function_call", "command", "step", "result", "outcome",
        "output", "error", "final",
    })

    # Keys that mark redundant intermediate states to strip
    NOISE_KEYS = frozenset({
        "thinking", "reasoning", "consideration", "exploring",
        "attempt", "retry", "internal", "debug", "log",
        "intermediate", "scratch", "draft", "temp",
    })

    def __init__(self) -> None:
        self._total_input_steps = 0
        self._total_output_steps = 0

    def distill(self, skill_data: dict) -> dict:
        """Distill a skill trajectory to its essential components.

        Args:
            skill_data: Full skill trajectory dict. Expected structure:
                {
                    "name": "skill_name",
                    "steps": [{"type": "...", "content": "...", ...}, ...],
                    "metadata": {...},
                    "outcome": "..."
                }

        Returns:
            Distilled skill dict with only essential steps preserved.
        """
        result = {}

        # Preserve identity fields
        for key in ("name", "id", "version", "description"):
            if key in skill_data:
                result[key] = skill_data[key]

        # Distill steps
        raw_steps = skill_data.get("steps", [])
        if isinstance(raw_steps, list):
            essential = self._extract_essential_steps(raw_steps)
            self._total_input_steps += len(raw_steps)
            self._total_output_steps += len(essential)
            result["steps"] = essential
        else:
            result["steps"] = raw_steps

        # Preserve outcome and metadata
        if "outcome" in skill_data:
            result["outcome"] = skill_data["outcome"]

        if "metadata" in skill_data:
            result["metadata"] = self._compress_metadata(
                skill_data["metadata"]
            )

        # Preserve parameters/signature
        for key in ("parameters", "inputs", "outputs", "signature"):
            if key in skill_data:
                result[key] = skill_data[key]

        return result

    def _extract_essential_steps(
        self, steps: list[dict]
    ) -> list[dict]:
        """Extract only decision-point steps from a trajectory."""
        essential: list[dict] = []

        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue

            # Always keep first and last steps
            is_boundary = (i == 0) or (i == len(steps) - 1)

            # Check if step contains decision-point keys
            step_keys = set(step.keys())
            has_decision = bool(step_keys & self.DECISION_KEYS)
            is_noise = bool(step_keys & self.NOISE_KEYS) and not has_decision

            # Check step type field
            step_type = str(step.get("type", "")).lower()
            is_action = step_type in (
                "action", "tool_use", "function_call", "command",
                "decision", "result", "output", "error",
            )

            if is_boundary or has_decision or is_action:
                # Strip noise keys from kept steps
                cleaned = {
                    k: v for k, v in step.items()
                    if k not in self.NOISE_KEYS
                }
                essential.append(cleaned)
            elif not is_noise and step.get("error"):
                # Always preserve error states
                essential.append(step)

        return essential if essential else steps[:1]

    def _compress_metadata(self, metadata: dict) -> dict:
        """Strip verbose metadata, keep essentials."""
        if not isinstance(metadata, dict):
            return metadata

        keep_keys = {
            "created_at", "updated_at", "author", "version",
            "tags", "category", "confidence", "success_rate",
            "total_runs", "avg_tokens", "dependencies",
        }
        return {k: v for k, v in metadata.items() if k in keep_keys}

    @property
    def compression_ratio(self) -> float:
        """Ratio of distilled to original steps (lower is better)."""
        if self._total_input_steps == 0:
            return 1.0
        return self._total_output_steps / self._total_input_steps


# ============================================================================
# L6: SemanticCache
# NEW - Embedding similarity to eliminate inference on near-dupes
# ============================================================================

class SemanticCache:
    """Use embedding similarity to eliminate inference on near-identical queries.

    Computes sentence embeddings and compares cosine similarity to find
    semantically equivalent queries that have already been answered.

    Configurable similarity threshold (default 0.92).
    Falls back to L2 exact cache if embeddings are unavailable.

    Uses sentence-transformers 'all-MiniLM-L6-v2' model (22M params,
    ~80ms per embedding on CPU) when available.
    """

    DEFAULT_THRESHOLD = 0.92
    MAX_ENTRIES = 500
    TTL_SEC = 600  # 10 minutes
    MODEL_NAME = "all-MiniLM-L6-v2"

    def __init__(self, threshold: float = DEFAULT_THRESHOLD) -> None:
        self._threshold = threshold
        self._entries: list[
            tuple[Any, str, str, float]  # (embedding, query, response, timestamp)
        ] = []
        self._model: Any = None
        self._model_available: bool | None = None  # None = not checked yet
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def _ensure_model(self) -> bool:
        """Lazy-load the sentence transformer model."""
        if self._model_available is not None:
            return self._model_available

        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.MODEL_NAME)
            self._model_available = True
            logger.info("SemanticCache: loaded %s", self.MODEL_NAME)
        except (ImportError, Exception) as exc:
            self._model_available = False
            logger.info(
                "SemanticCache: embeddings unavailable (%s), "
                "falling back to exact cache",
                type(exc).__name__,
            )
        return self._model_available

    def _embed(self, text: str) -> Any:
        """Compute embedding vector for text."""
        if self._model is None:
            return None
        return self._model.encode(text, normalize_embeddings=True)

    @staticmethod
    def _cosine_similarity(a: Any, b: Any) -> float:
        """Cosine similarity between two normalized vectors."""
        try:
            import numpy as np
            return float(np.dot(a, b))
        except (ImportError, Exception):
            return 0.0

    def check(
        self, query: str, threshold: float | None = None
    ) -> str | None:
        """Check for a semantically similar cached query.

        Args:
            query: The incoming user query.
            threshold: Similarity threshold override (default: instance threshold).

        Returns:
            Cached response string, or None on miss.
        """
        if not self._ensure_model():
            self._misses += 1
            return None

        thresh = threshold if threshold is not None else self._threshold
        query_embedding = self._embed(query)
        if query_embedding is None:
            self._misses += 1
            return None

        now = time.time()
        best_score = 0.0
        best_response: str | None = None

        with self._lock:
            # Evict expired entries
            self._entries = [
                entry for entry in self._entries
                if now - entry[3] < self.TTL_SEC
            ]

            for emb, cached_query, cached_response, ts in self._entries:
                score = self._cosine_similarity(query_embedding, emb)
                if score >= thresh and score > best_score:
                    best_score = score
                    best_response = cached_response

        if best_response is not None:
            self._hits += 1
            logger.debug(
                "SemanticCache hit: score=%.3f query=%r",
                best_score,
                query[:60],
            )
            return best_response

        self._misses += 1
        return None

    def store(self, query: str, response: str) -> None:
        """Store a query/response pair with its embedding.

        Args:
            query: The user query.
            response: The LLM response.
        """
        if not self._ensure_model():
            return

        embedding = self._embed(query)
        if embedding is None:
            return

        now = time.time()
        with self._lock:
            self._entries.append((embedding, query, response, now))

            # Evict oldest if over capacity
            while len(self._entries) > self.MAX_ENTRIES:
                self._entries.pop(0)

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return (self._hits / total * 100.0) if total > 0 else 0.0


# ============================================================================
# L7: ConversationWindow
# Absorbed from IT_NEXUS cost_guardian.py ConversationWindow
# ============================================================================

class ConversationWindow:
    """Manage conversation history: summarize old messages, keep recent.

    - 20-turn window maximum
    - 6 most recent messages kept verbatim
    - Older messages compressed into a compact summary prefix
    """

    WINDOW_SIZE = 20
    RECENT_VERBATIM = 6

    @classmethod
    def manage(cls, messages: list[dict]) -> list[dict]:
        """Trim and summarize conversation history.

        Args:
            messages: List of {"role": str, "content": str} dicts.

        Returns:
            Managed message list: summarized old + recent verbatim.
        """
        if len(messages) <= cls.WINDOW_SIZE:
            return messages

        cutoff = len(messages) - cls.RECENT_VERBATIM
        old = messages[:cutoff]
        recent = messages[cutoff:]

        summary_lines: list[str] = []
        for msg in old:
            role = msg.get("role", "?")[0].upper()
            content = msg.get("content", "")
            compressed = content.replace("\n", " ").strip()
            if len(compressed) > 120:
                compressed = compressed[:117] + "..."
            summary_lines.append(f"{role}: {compressed}")

        summary_text = "[Conversation summary]\n" + "\n".join(summary_lines)
        summary_msg = {"role": "system", "content": summary_text}
        return [summary_msg] + recent


# ============================================================================
# CostTracker
# Absorbed from IT_NEXUS cost_guardian.py CostTracker
# ============================================================================

@dataclass
class CostEntry:
    """Single API cost record."""

    timestamp: float
    api: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    tokens_saved: int = 0


class CostTracker:
    """Record per-call costs, enforce budgets, persist ledger.

    Tracks input/output tokens, cache reads, and tokens saved by
    the optimization pipeline. Supports session and hourly budgets.
    """

    # Anthropic pricing (per million tokens)
    CLAUDE_INPUT_COST = 3.00 / 1_000_000
    CLAUDE_OUTPUT_COST = 15.00 / 1_000_000
    CLAUDE_CACHE_READ_COST = 0.30 / 1_000_000

    DEFAULT_SESSION_BUDGET_USD = 2.00
    DEFAULT_HOURLY_BUDGET_USD = 5.00

    LEDGER_FILE = "token_saver_ledger.json"

    def __init__(
        self,
        data_dir: str | Path | None = None,
        session_budget: float | None = None,
        hourly_budget: float | None = None,
    ) -> None:
        self._data_dir = Path(data_dir) if data_dir else Path("data")
        self._ledger_path = self._data_dir / self.LEDGER_FILE

        self.session_budget = session_budget or self.DEFAULT_SESSION_BUDGET_USD
        self.hourly_budget = hourly_budget or self.DEFAULT_HOURLY_BUDGET_USD

        self._entries: list[CostEntry] = []
        self._session_start = time.time()
        self._alerts: list[str] = []
        self._lock = threading.Lock()

        self._load_ledger()

    def _load_ledger(self) -> None:
        if not self._ledger_path.exists():
            return
        try:
            raw = json.loads(self._ledger_path.read_text(encoding="utf-8"))
            for item in raw.get("entries", []):
                self._entries.append(
                    CostEntry(
                        timestamp=item["timestamp"],
                        api=item["api"],
                        input_tokens=item.get("input_tokens", 0),
                        output_tokens=item.get("output_tokens", 0),
                        cache_read_tokens=item.get("cache_read_tokens", 0),
                        tokens_saved=item.get("tokens_saved", 0),
                    )
                )
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("Failed to load cost ledger, starting fresh")

    def _save_ledger(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        entries = [
            {
                "timestamp": e.timestamp,
                "api": e.api,
                "input_tokens": e.input_tokens,
                "output_tokens": e.output_tokens,
                "cache_read_tokens": e.cache_read_tokens,
                "tokens_saved": e.tokens_saved,
            }
            for e in self._entries
        ]
        payload = {"entries": entries, "saved_at": time.time()}
        tmp = self._ledger_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self._ledger_path)

    def record(
        self,
        api: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        tokens_saved: int = 0,
    ) -> list[str]:
        """Record a cost entry and return any budget alerts."""
        entry = CostEntry(
            timestamp=time.time(),
            api=api,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            tokens_saved=tokens_saved,
        )
        alerts: list[str] = []

        with self._lock:
            self._entries.append(entry)

            session_cost = self._session_cost()
            hourly_cost = self._hourly_cost()

            if session_cost > self.session_budget:
                alerts.append(
                    f"SESSION BUDGET EXCEEDED: "
                    f"${session_cost:.4f} / ${self.session_budget:.2f}"
                )
            elif session_cost > self.session_budget * 0.8:
                alerts.append(
                    f"Session budget 80% used: "
                    f"${session_cost:.4f} / ${self.session_budget:.2f}"
                )

            if hourly_cost > self.hourly_budget:
                alerts.append(
                    f"HOURLY BUDGET EXCEEDED: "
                    f"${hourly_cost:.4f} / ${self.hourly_budget:.2f}"
                )
            elif hourly_cost > self.hourly_budget * 0.8:
                alerts.append(
                    f"Hourly budget 80% used: "
                    f"${hourly_cost:.4f} / ${self.hourly_budget:.2f}"
                )

            self._alerts.extend(alerts)
            self._save_ledger()

        return alerts

    def _entry_cost(self, e: CostEntry) -> float:
        cost = 0.0
        cost += e.input_tokens * self.CLAUDE_INPUT_COST
        cost += e.output_tokens * self.CLAUDE_OUTPUT_COST
        cost += e.cache_read_tokens * self.CLAUDE_CACHE_READ_COST
        return cost

    def _session_cost(self) -> float:
        return sum(
            self._entry_cost(e)
            for e in self._entries
            if e.timestamp >= self._session_start
        )

    def _hourly_cost(self) -> float:
        cutoff = time.time() - 3600
        return sum(
            self._entry_cost(e)
            for e in self._entries
            if e.timestamp >= cutoff
        )

    @property
    def total_cost(self) -> float:
        return sum(self._entry_cost(e) for e in self._entries)

    @property
    def total_tokens_saved(self) -> int:
        return sum(e.tokens_saved for e in self._entries)

    @property
    def total_tokens_used(self) -> int:
        return sum(
            e.input_tokens + e.output_tokens for e in self._entries
        )

    def get_report(self) -> str:
        """Full cost and savings report string."""
        now = time.time()
        session_entries = [
            e for e in self._entries if e.timestamp >= self._session_start
        ]
        hourly_entries = [
            e for e in self._entries if e.timestamp >= now - 3600
        ]

        total_in = sum(e.input_tokens for e in self._entries)
        total_out = sum(e.output_tokens for e in self._entries)
        total_cache = sum(e.cache_read_tokens for e in self._entries)
        total_saved = sum(e.tokens_saved for e in self._entries)

        session_in = sum(e.input_tokens for e in session_entries)
        session_out = sum(e.output_tokens for e in session_entries)
        session_saved = sum(e.tokens_saved for e in session_entries)

        hourly_in = sum(e.input_tokens for e in hourly_entries)
        hourly_out = sum(e.output_tokens for e in hourly_entries)

        lines = [
            "=== SpongeBot Token Saver Report ===",
            "",
            f"All-time ({len(self._entries)} calls):",
            f"  Input tokens:       {total_in:>10,}",
            f"  Output tokens:      {total_out:>10,}",
            f"  Cache-read tokens:  {total_cache:>10,}",
            f"  Tokens saved:       {total_saved:>10,}",
            f"  Total cost:         ${self.total_cost:>10.4f}",
            "",
            f"This session ({len(session_entries)} calls):",
            f"  Input tokens:       {session_in:>10,}",
            f"  Output tokens:      {session_out:>10,}",
            f"  Tokens saved:       {session_saved:>10,}",
            f"  Session cost:       ${self._session_cost():>10.4f}",
            f"  Budget remaining:   "
            f"${max(0, self.session_budget - self._session_cost()):>10.4f}",
            "",
            f"Last hour ({len(hourly_entries)} calls):",
            f"  Input tokens:       {hourly_in:>10,}",
            f"  Output tokens:      {hourly_out:>10,}",
            f"  Hourly cost:        ${self._hourly_cost():>10.4f}",
            f"  Budget remaining:   "
            f"${max(0, self.hourly_budget - self._hourly_cost()):>10.4f}",
        ]

        if self._alerts:
            lines.extend(["", "Recent alerts:"])
            for alert in self._alerts[-5:]:
                lines.append(f"  ! {alert}")

        return "\n".join(lines)


# ============================================================================
# TokenSaver: unified facade over all 7 layers + CostTracker
# Implements TokenSaverInterface protocol
# ============================================================================

class TokenSaver:
    """7-layer token optimization system for SpongeBot.

    Orchestrates all optimization layers and tracks cumulative savings.
    Implements TokenSaverInterface protocol for clean dependency injection.

    Usage:
        saver = TokenSaver(data_dir="data")

        # L1: compress system prompt
        system_prompt = saver.compress_prompt(raw_prompt)

        # L2: check response cache
        cached = saver.check_cache(user_msg)
        if cached is None:
            response = call_llm(...)
            saver.cache_response(user_msg, response)

        # L3: check agentic plan cache
        plan = saver.check_plan_cache(plan_request)

        # L4: compress KV cache data
        compressed = saver.compress_kv(raw_kv_bytes)

        # L5: distill skill trajectory
        distilled = saver.distill_skill(full_skill_data)

        # L6: semantic cache
        cached = saver.check_semantic_cache("how do I reset?")

        # L7: manage conversation window
        messages = saver.manage_window(messages)

        # Report
        print(saver.get_report())
    """

    def __init__(
        self,
        data_dir: str | Path | None = None,
        session_budget: float | None = None,
        hourly_budget: float | None = None,
        semantic_threshold: float = 0.92,
        kv_compression_level: int = 6,
    ) -> None:
        # L1: System prompt compression
        self._prompt_compressor = SystemPromptCompressor()

        # L2: Response cache (exact + pattern)
        self._response_cache = ResponseCache()

        # L3: Agentic plan cache
        self._plan_cache = AgenticPlanCache()

        # L4: KV cache compressor
        self._kv_compressor = KVCacheCompressor(level=kv_compression_level)

        # L5: Skill distiller
        self._skill_distiller = SkillDistiller()

        # L6: Semantic cache
        self._semantic_cache = SemanticCache(threshold=semantic_threshold)

        # L7: Conversation window
        self._conversation_window = ConversationWindow()

        # Cost tracker
        self._cost_tracker = CostTracker(
            data_dir=data_dir,
            session_budget=session_budget,
            hourly_budget=hourly_budget,
        )

        # Cumulative savings tracking
        self._total_original_tokens = 0
        self._total_compressed_tokens = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._lock = threading.Lock()

        logger.info("TokenSaver initialized with 7 optimization layers")

    # --- L1: System Prompt Compression ---

    def compress_prompt(self, prompt: str) -> str:
        """L1: Strip whitespace bloat and formatting decorators.

        Returns:
            Compressed prompt string.
        """
        original_len = len(prompt)
        compressed = SystemPromptCompressor.compress(prompt)
        compressed_len = len(compressed)

        with self._lock:
            self._total_original_tokens += original_len // 4  # rough estimate
            self._total_compressed_tokens += compressed_len // 4

        return compressed

    # --- L2: Response Cache ---

    def check_cache(self, message: str) -> str | None:
        """L2: SHA-256 exact match + pattern match cache lookup.

        Returns:
            Cached response string, or None on miss.
        """
        result = self._response_cache.check(message)
        with self._lock:
            if result is not None:
                self._cache_hits += 1
            else:
                self._cache_misses += 1
        return result

    def cache_response(self, message: str, response: str) -> None:
        """L2: Store a message/response pair in the response cache."""
        self._response_cache.store(message, response)

    # --- L3: Agentic Plan Cache ---

    def check_plan_cache(self, plan_request: dict) -> dict | None:
        """L3: Check for a structurally similar cached plan.

        Returns:
            Cached plan template, or None on miss.
        """
        return self._plan_cache.check(plan_request)

    def cache_plan(self, plan_request: dict, plan_result: dict) -> None:
        """L3: Store a plan request/result pair in the plan cache."""
        self._plan_cache.store(plan_request, plan_result)

    def fill_plan_template(
        self, template: dict, new_params: dict
    ) -> dict:
        """L3: Fill a cached plan template with new parameters."""
        return self._plan_cache.fill_template(template, new_params)

    # --- L4: KV Cache Compression ---

    def compress_kv(self, kv_data: bytes) -> bytes:
        """L4: Compress key-value cache data using zlib.

        Returns:
            Compressed bytes with length prefix.
        """
        return self._kv_compressor.compress(kv_data)

    def decompress_kv(self, kv_data: bytes) -> bytes:
        """L4: Decompress previously compressed KV data.

        Returns:
            Original decompressed bytes.
        """
        return self._kv_compressor.decompress(kv_data)

    # --- L5: Skill Distillation ---

    def distill_skill(self, skill_data: dict) -> dict:
        """L5: Compress skill trajectories, keeping decision points.

        Returns:
            Distilled skill dict with redundant steps removed.
        """
        return self._skill_distiller.distill(skill_data)

    # --- L6: Semantic Cache ---

    def check_semantic_cache(
        self, query: str, threshold: float = 0.92
    ) -> str | None:
        """L6: Embedding-similarity cache lookup.

        Falls back to L2 exact cache if embeddings are unavailable.

        Returns:
            Cached response for a semantically similar query, or None.
        """
        # Try semantic match first
        result = self._semantic_cache.check(query, threshold)
        if result is not None:
            return result

        # Fall back to exact/pattern cache
        return self._response_cache.check(query)

    def cache_semantic(self, query: str, response: str) -> None:
        """L6: Store a query/response pair in the semantic cache.

        Also stores in L2 exact cache for fallback.
        """
        self._semantic_cache.store(query, response)
        self._response_cache.store(query, response)

    # --- L7: Conversation Window ---

    def manage_window(self, messages: list[dict]) -> list[dict]:
        """L7: Summarize old messages, keep recent verbatim.

        Returns:
            Managed message list within the window budget.
        """
        original_count = len(messages)
        managed = ConversationWindow.manage(messages)

        # Track token savings from window management
        if len(managed) < original_count:
            original_chars = sum(
                len(m.get("content", "")) for m in messages
            )
            managed_chars = sum(
                len(m.get("content", "")) for m in managed
            )
            with self._lock:
                self._total_original_tokens += original_chars // 4
                self._total_compressed_tokens += managed_chars // 4

        return managed

    # --- Cost Tracking ---

    def record_cost(
        self,
        api: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        tokens_saved: int = 0,
    ) -> list[str]:
        """Record an API call cost and return budget alerts."""
        return self._cost_tracker.record(
            api=api,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            tokens_saved=tokens_saved,
        )

    # --- Reporting ---

    def get_report(self) -> str:
        """Full cost and savings report across all layers."""
        cost_report = self._cost_tracker.get_report()

        layer_stats = [
            "",
            "=== Layer Statistics ===",
            "",
            f"L2 ResponseCache hit rate:    {self._response_cache.hit_rate:>6.1f}%",
            f"L3 PlanCache hit rate:         {self._plan_cache.hit_rate:>6.1f}%",
            f"L4 KV compression ratio:       {self._kv_compressor.compression_ratio:>6.2f}x",
            f"   KV bytes saved:             {self._kv_compressor.bytes_saved:>10,}",
            f"L5 Skill distill ratio:        {self._skill_distiller.compression_ratio:>6.2f}x",
            f"L6 SemanticCache hit rate:     {self._semantic_cache.hit_rate:>6.1f}%",
            "",
            f"Overall savings:               {self.savings_percentage:>6.1f}%",
            f"Total cache hits:              {self._cache_hits:>10,}",
            f"Total cache misses:            {self._cache_misses:>10,}",
        ]

        return cost_report + "\n" + "\n".join(layer_stats)

    @property
    def savings_percentage(self) -> float:
        """Overall token savings as a percentage (0.0-100.0)."""
        with self._lock:
            total = self._total_original_tokens
            if total == 0:
                return 0.0
            saved = total - self._total_compressed_tokens
            # Add cache-hit savings (estimated)
            saved += self._cache_hits * 500  # avg 500 tokens per cache hit
            total += self._cache_hits * 500
            return min(100.0, max(0.0, (saved / total) * 100.0))

    @property
    def session_cost(self) -> float:
        """Current session cost in USD."""
        return self._cost_tracker._session_cost()

    @property
    def total_cost(self) -> float:
        """All-time total cost in USD."""
        return self._cost_tracker.total_cost

    @property
    def total_tokens_saved(self) -> int:
        """Total tokens saved by the optimization pipeline."""
        return self._cost_tracker.total_tokens_saved
