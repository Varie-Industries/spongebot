"""
Token Saver — 7-layer token optimization system.

Public API exposes only the Protocol interface and the concrete TokenSaver.
The implementation details in _engine.py are compiled to binary for distribution.
"""

from __future__ import annotations

from src.token_saver._interface import TokenSaverInterface
from src.token_saver._engine import TokenSaver

__all__ = ["TokenSaverInterface", "TokenSaver"]
