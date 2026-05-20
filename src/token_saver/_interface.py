"""Token Saver Public Interface -- the Krabby Patty Secret Formula's menu.

Only this Protocol class is public. The implementation is compiled to binary.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TokenSaverInterface(Protocol):
    """Protocol defining the token saver contract.

    All seven layers of the token optimization pipeline are accessible
    through these methods. Callers depend on this interface only; the
    concrete implementation lives in _engine.py (compiled to .so/.pyd).

    Layers:
        L1  SystemPromptCompressor   - compress_prompt()
        L2  ResponseCache            - check_cache() / cache_response()
        L3  AgenticPlanCache         - (internal, used by compress_prompt)
        L4  KVCacheCompressor        - compress_kv()
        L5  SkillDistiller           - distill_skill()
        L6  SemanticCache            - check_semantic_cache()
        L7  ConversationWindow       - manage_window()
    """

    def compress_prompt(self, prompt: str) -> str:
        """L1: Strip whitespace bloat, formatting decorators, triple newlines."""
        ...

    def check_cache(self, message: str) -> str | None:
        """L2: SHA-256 exact match + pattern match cache lookup."""
        ...

    def cache_response(self, message: str, response: str) -> None:
        """L2: Store a message/response pair in the response cache."""
        ...

    def compress_kv(self, kv_data: bytes) -> bytes:
        """L4: Compress key-value cache data using zlib."""
        ...

    def distill_skill(self, skill_data: dict) -> dict:
        """L5: Compress skill trajectories, keeping only decision points."""
        ...

    def check_semantic_cache(
        self, query: str, threshold: float = 0.92
    ) -> str | None:
        """L6: Embedding-similarity cache lookup for near-identical queries."""
        ...

    def manage_window(self, messages: list[dict]) -> list[dict]:
        """L7: Manage conversation window with summarization of old turns."""
        ...

    def get_report(self) -> str:
        """Return a human-readable cost/savings report."""
        ...

    @property
    def savings_percentage(self) -> float:
        """Overall token savings as a percentage (0.0-100.0)."""
        ...
