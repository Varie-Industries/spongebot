"""
Mode 2 -- Document Absorption.

Parses Markdown, PDF, HTML, and source code files into semantic chunks
and uses Claude to extract actionable skill templates from each chunk.

Initial confidence: 0.3 (extracted from documentation -- needs
real-world validation before trust is earned).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger("spongebot.absorption.document_absorption")

# ------------------------------------------------------------------
# Content type handlers
# ------------------------------------------------------------------

_SUPPORTED_TYPES = {"markdown", "html", "text", "code", "pdf"}

# Markdown heading pattern for semantic splitting
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

# HTML tag pattern for basic stripping
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# ------------------------------------------------------------------
# LLM prompt
# ------------------------------------------------------------------

_SKILL_EXTRACTION_PROMPT = """\
You are SpongeBot's Absorption Engine. Extract actionable skills from \
the following document chunk. A skill is a repeatable procedure that \
can be executed to achieve a goal.

Document type: {content_type}
Chunk ({chunk_index}/{total_chunks}):
---
{chunk}
---

If the chunk contains actionable knowledge, respond with a JSON array \
of skill objects (no markdown fences). Each skill:
[
  {{
    "name": "<snake_case>",
    "description": "<one sentence>",
    "parameters": [{{"name": "<p>", "type": "<t>", "required": true}}],
    "steps": ["<step 1>", ...],
    "prerequisites": ["<req>", ...],
    "tags": ["<tag>", ...]
  }}
]

If the chunk contains no actionable skills (e.g. preamble, license, \
table of contents), respond with an empty array: []
"""


class DocumentAbsorption:
    """Extract skills from document content by chunking and LLM analysis.

    Supports Markdown, HTML, plain text, and source code. Documents are
    split into semantic chunks (respecting headings and logical breaks)
    and each chunk is analysed for actionable skill templates.

    Parameters
    ----------
    config : dict
        SpongeBot configuration dictionary.
    llm_client : object, optional
        LLM client for skill extraction from chunks.
    """

    INITIAL_CONFIDENCE: float = 0.3
    MAX_CHUNK_SIZE: int = 4000

    def __init__(self, config: dict[str, Any], llm_client: Any | None = None) -> None:
        self._config = config
        self._absorption_cfg = config.get("absorption", {})
        self._initial_confidence = self._absorption_cfg.get(
            "initial_confidence", {}
        ).get("document", self.INITIAL_CONFIDENCE)
        self._llm_client = llm_client

        logger.debug(
            "DocumentAbsorption initialised (confidence=%.2f, chunk_size=%d)",
            self._initial_confidence,
            self.MAX_CHUNK_SIZE,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def absorb(
        self,
        content: str,
        content_type: str = "markdown",
        source_id: str | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Extract skills from document content.

        Parameters
        ----------
        content : str
            Raw document text.
        content_type : str
            One of ``"markdown"``, ``"html"``, ``"text"``, ``"code"``,
            ``"pdf"``.  Determines the chunking strategy.
        source_id : str, optional
            Identifier for the source document (e.g. filename).

        Returns
        -------
        list[dict]
            Skill dicts with confidence set to ``INITIAL_CONFIDENCE``.
        """
        if content_type not in _SUPPORTED_TYPES:
            logger.warning(
                "Unsupported content type %r, falling back to 'text'",
                content_type,
            )
            content_type = "text"

        source_label = source_id or f"doc_{content_type}"
        logger.info(
            "Absorbing document: %s (type=%s, length=%d)",
            source_label,
            content_type,
            len(content),
        )

        # Preprocess based on content type
        clean_content = self._preprocess(content, content_type)

        # Chunk into semantic sections
        chunks = self._chunk(clean_content, content_type)
        logger.info(
            "Split document into %d chunks (max %d chars each)",
            len(chunks),
            self.MAX_CHUNK_SIZE,
        )

        if not chunks:
            logger.warning("No content chunks produced from document %s", source_label)
            return []

        # Extract skills from each chunk
        all_skills: list[dict[str, Any]] = []
        for idx, chunk in enumerate(chunks, start=1):
            extracted = await self._extract_from_chunk(
                chunk, content_type, idx, len(chunks), source_label,
            )
            all_skills.extend(extracted)

        # Deduplicate by name
        seen_names: set[str] = set()
        unique_skills: list[dict[str, Any]] = []
        for skill in all_skills:
            if skill["name"] not in seen_names:
                seen_names.add(skill["name"])
                unique_skills.append(skill)

        logger.info(
            "Absorbed %d unique skills from document %s (%d before dedup)",
            len(unique_skills),
            source_label,
            len(all_skills),
        )
        return unique_skills

    # ------------------------------------------------------------------
    # Preprocessing
    # ------------------------------------------------------------------

    def _preprocess(self, content: str, content_type: str) -> str:
        """Clean content based on type before chunking."""
        if content_type == "html":
            # Strip HTML tags for a text representation
            return _HTML_TAG_RE.sub("", content)
        # Markdown, text, code, pdf: use as-is
        return content

    # ------------------------------------------------------------------
    # Chunking strategies
    # ------------------------------------------------------------------

    def _chunk(self, content: str, content_type: str) -> list[str]:
        """Split *content* into semantically meaningful chunks.

        Strategy varies by content type:
        - **markdown**: Split on headings
        - **code**: Split on class/function definitions or blank line groups
        - **text/html/pdf**: Split on paragraph breaks, respecting max size
        """
        if content_type == "markdown":
            return self._chunk_markdown(content)
        if content_type == "code":
            return self._chunk_code(content)
        return self._chunk_plain(content)

    def _chunk_markdown(self, content: str) -> list[str]:
        """Split markdown on headings, then enforce max chunk size."""
        sections: list[str] = []
        matches = list(_MD_HEADING_RE.finditer(content))

        if not matches:
            return self._chunk_plain(content)

        # Text before the first heading
        preamble = content[: matches[0].start()].strip()
        if preamble:
            sections.append(preamble)

        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            section = content[start:end].strip()
            if section:
                sections.append(section)

        # Enforce max size on each section
        chunks: list[str] = []
        for section in sections:
            if len(section) <= self.MAX_CHUNK_SIZE:
                chunks.append(section)
            else:
                chunks.extend(self._split_oversized(section))
        return chunks

    def _chunk_code(self, content: str) -> list[str]:
        """Split source code on class/function boundaries."""
        # Split on lines that start with 'class ', 'def ', 'async def ',
        # 'function ', or 'export ' (covers Python, JS/TS patterns)
        boundary_re = re.compile(
            r"^(?:class |def |async def |function |export )", re.MULTILINE
        )
        matches = list(boundary_re.finditer(content))

        if not matches:
            return self._chunk_plain(content)

        chunks: list[str] = []
        preamble = content[: matches[0].start()].strip()
        if preamble:
            chunks.append(preamble)

        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            block = content[start:end].strip()
            if block:
                if len(block) <= self.MAX_CHUNK_SIZE:
                    chunks.append(block)
                else:
                    chunks.extend(self._split_oversized(block))
        return chunks

    def _chunk_plain(self, content: str) -> list[str]:
        """Split plain text on double newlines, enforcing max chunk size."""
        paragraphs = re.split(r"\n{2,}", content)
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if current_len + len(para) + 2 > self.MAX_CHUNK_SIZE and current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            current.append(para)
            current_len += len(para) + 2

        if current:
            chunks.append("\n\n".join(current))
        return chunks

    def _split_oversized(self, text: str) -> list[str]:
        """Split an oversized section into MAX_CHUNK_SIZE pieces at line breaks."""
        lines = text.split("\n")
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for line in lines:
            if current_len + len(line) + 1 > self.MAX_CHUNK_SIZE and current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            current.append(line)
            current_len += len(line) + 1

        if current:
            chunks.append("\n".join(current))
        return chunks

    # ------------------------------------------------------------------
    # Skill extraction
    # ------------------------------------------------------------------

    async def _extract_from_chunk(
        self,
        chunk: str,
        content_type: str,
        chunk_index: int,
        total_chunks: int,
        source_id: str,
    ) -> list[dict[str, Any]]:
        """Extract skill dicts from a single chunk using the LLM.

        Falls back to an empty list if no LLM is available or the
        LLM fails to produce valid JSON.
        """
        if self._llm_client is None:
            logger.debug("No LLM client -- skipping chunk %d/%d", chunk_index, total_chunks)
            return []

        prompt = _SKILL_EXTRACTION_PROMPT.format(
            content_type=content_type,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            chunk=chunk,
        )

        try:
            response = await self._llm_client.generate(prompt)
            text = response if isinstance(response, str) else str(response)
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[: text.rfind("```")]
            raw_skills = json.loads(text.strip())
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            logger.warning(
                "Chunk %d/%d: LLM JSON parse failed: %s",
                chunk_index,
                total_chunks,
                exc,
            )
            return []
        except Exception as exc:
            logger.warning(
                "Chunk %d/%d: LLM call failed: %s",
                chunk_index,
                total_chunks,
                exc,
            )
            return []

        if not isinstance(raw_skills, list):
            raw_skills = [raw_skills] if isinstance(raw_skills, dict) else []

        now = time.time()
        skills: list[dict[str, Any]] = []
        for raw in raw_skills:
            if not isinstance(raw, dict) or "name" not in raw:
                continue
            skills.append({
                "name": raw["name"],
                "description": raw.get("description", ""),
                "type": "atomic",
                "parameters": raw.get("parameters", []),
                "steps": raw.get("steps", []),
                "prerequisites": raw.get("prerequisites", []),
                "confidence": self._initial_confidence,
                "version": "0.1.0",
                "absorbed_from": source_id,
                "absorption_mode": "document",
                "created_at": now,
                "last_used": now,
                "use_count": 0,
                "tags": raw.get("tags", ["document"]),
            })
        return skills

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Return health status for the document absorption mode."""
        return {
            "status": "ok",
            "mode": "document",
            "initial_confidence": self._initial_confidence,
            "max_chunk_size": self.MAX_CHUNK_SIZE,
            "supported_types": sorted(_SUPPORTED_TYPES),
            "llm_available": self._llm_client is not None,
        }
