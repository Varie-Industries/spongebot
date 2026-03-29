"""
Mode 1 -- Agent Absorption.

Discovers agent capabilities from manifests, MCP server tool lists,
and capability definitions. Uses Claude to transform raw tool schemas
into reusable SpongeBot skill templates.

Initial confidence: 0.5 (untested -- the skill looks right on paper
but hasn't been validated by execution yet).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger("spongebot.absorption.agent_absorption")

# ------------------------------------------------------------------
# LLM prompt for skill extraction from tool schemas
# ------------------------------------------------------------------

_SKILL_EXTRACTION_PROMPT = """\
You are SpongeBot's Absorption Engine. Given the following agent tool \
schema, generate a reusable skill template.

Tool schema:
{schema_json}

Respond with ONLY a JSON object (no markdown fences) containing:
{{
  "name": "<snake_case_skill_name>",
  "description": "<what this skill does, one sentence>",
  "parameters": [
    {{"name": "<param>", "type": "<python_type>", "required": true/false}}
  ],
  "steps": ["<step 1>", "<step 2>", ...],
  "prerequisites": ["<prerequisite_skill_or_resource>", ...],
  "tags": ["<tag1>", "<tag2>"]
}}
"""


class AgentAbsorption:
    """Extract skills from agent capability manifests and tool schemas.

    Supports multiple manifest formats:
    - MCP server tool lists (``{"tools": [...]}`` )
    - Capability manifests (``{"capabilities": [...]}`` )
    - Direct tool definitions (``{"name": ..., "inputSchema": ...}`` )

    Parameters
    ----------
    config : dict
        SpongeBot configuration dictionary.
    llm_client : object, optional
        LLM client for generating skill templates from schemas.
    """

    INITIAL_CONFIDENCE: float = 0.5

    def __init__(self, config: dict[str, Any], llm_client: Any | None = None) -> None:
        self._config = config
        self._absorption_cfg = config.get("absorption", {})
        self._initial_confidence = self._absorption_cfg.get(
            "initial_confidence", {}
        ).get("agent", self.INITIAL_CONFIDENCE)
        self._llm_client = llm_client

        logger.debug(
            "AgentAbsorption initialised (confidence=%.2f, llm=%s)",
            self._initial_confidence,
            self._llm_client is not None,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def absorb(self, agent_manifest: dict[str, Any], **kwargs: Any) -> list[dict[str, Any]]:
        """Extract skills from an agent's capability manifest.

        Parameters
        ----------
        agent_manifest : dict
            Agent manifest containing tool definitions. Expected keys
            include ``"tools"``, ``"capabilities"``, or a single tool
            at the top level.

        Returns
        -------
        list[dict]
            Skill dicts with confidence set to ``INITIAL_CONFIDENCE``.
        """
        source_id = agent_manifest.get("name", agent_manifest.get("id", "unknown_agent"))
        logger.info("Absorbing agent manifest: %s", source_id)

        tools = self._extract_tools(agent_manifest)
        if not tools:
            logger.warning("No tools found in manifest for %s", source_id)
            return []

        logger.info("Found %d tools in manifest for %s", len(tools), source_id)

        skills: list[dict[str, Any]] = []
        for tool in tools:
            skill = await self._tool_to_skill(tool, source_id)
            if skill is not None:
                skills.append(skill)

        logger.info(
            "Absorbed %d skills from agent %s",
            len(skills),
            source_id,
        )
        return skills

    # ------------------------------------------------------------------
    # Tool extraction from various manifest formats
    # ------------------------------------------------------------------

    def _extract_tools(self, manifest: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract a normalised list of tool dicts from *manifest*.

        Handles three common formats:
        1. ``{"tools": [...]}``  -- MCP server style
        2. ``{"capabilities": [...]}`` -- capability manifest style
        3. Single tool at top level with ``"name"`` and ``"inputSchema"``
        """
        # MCP server tool list
        if "tools" in manifest:
            tools = manifest["tools"]
            if isinstance(tools, list):
                return tools

        # Capability manifest
        if "capabilities" in manifest:
            caps = manifest["capabilities"]
            if isinstance(caps, list):
                return caps

        # Single tool at top level
        if "name" in manifest and ("inputSchema" in manifest or "parameters" in manifest):
            return [manifest]

        # Nested under a "manifest" key
        if "manifest" in manifest and isinstance(manifest["manifest"], dict):
            return self._extract_tools(manifest["manifest"])

        logger.debug(
            "Unrecognised manifest format, keys: %s",
            list(manifest.keys()),
        )
        return []

    # ------------------------------------------------------------------
    # Skill generation
    # ------------------------------------------------------------------

    async def _tool_to_skill(
        self,
        tool: dict[str, Any],
        source_id: str,
    ) -> dict[str, Any] | None:
        """Convert a single tool schema into a SpongeBot skill dict.

        Uses the LLM to generate a rich skill template when available.
        Falls back to a deterministic extraction if no LLM client is
        configured.
        """
        tool_name = tool.get("name", "unnamed_tool")

        if self._llm_client is not None:
            skill_body = await self._llm_extract(tool)
        else:
            skill_body = self._deterministic_extract(tool)

        if skill_body is None:
            logger.warning("Failed to extract skill from tool %s", tool_name)
            return None

        now = time.time()
        return {
            "name": skill_body.get("name", tool_name),
            "description": skill_body.get("description", tool.get("description", "")),
            "type": "atomic",
            "parameters": skill_body.get("parameters", self._extract_parameters(tool)),
            "steps": skill_body.get("steps", []),
            "prerequisites": skill_body.get("prerequisites", []),
            "confidence": self._initial_confidence,
            "version": "0.1.0",
            "absorbed_from": source_id,
            "absorption_mode": "agent",
            "created_at": now,
            "last_used": now,
            "use_count": 0,
            "tags": skill_body.get("tags", []),
        }

    async def _llm_extract(self, tool: dict[str, Any]) -> dict[str, Any] | None:
        """Use the LLM to generate a rich skill template from *tool*.

        Returns
        -------
        dict or None
            Parsed skill body from the LLM, or ``None`` on failure.
        """
        schema_json = json.dumps(tool, indent=2, default=str)
        prompt = _SKILL_EXTRACTION_PROMPT.format(schema_json=schema_json)

        try:
            response = await self._llm_client.generate(prompt)  # type: ignore[union-attr]
            text = response if isinstance(response, str) else str(response)
            # Strip markdown code fences if the LLM wrapped it
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[: text.rfind("```")]
            return json.loads(text.strip())
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            logger.warning(
                "LLM skill extraction failed for tool %s: %s",
                tool.get("name", "?"),
                exc,
            )
            return None
        except Exception as exc:
            logger.warning(
                "LLM call failed during agent absorption: %s",
                exc,
            )
            return None

    def _deterministic_extract(self, tool: dict[str, Any]) -> dict[str, Any]:
        """Fallback deterministic skill extraction when no LLM is available."""
        return {
            "name": tool.get("name", "unnamed_tool"),
            "description": tool.get("description", ""),
            "parameters": self._extract_parameters(tool),
            "steps": [f"Invoke tool {tool.get('name', '?')} with given parameters"],
            "prerequisites": [],
            "tags": ["auto-extracted", "agent"],
        }

    @staticmethod
    def _extract_parameters(tool: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract parameter definitions from a tool schema.

        Handles both MCP-style ``inputSchema.properties`` and simple
        ``parameters`` list formats.
        """
        params: list[dict[str, Any]] = []

        # Direct parameters list
        if isinstance(tool.get("parameters"), list):
            return tool["parameters"]

        # MCP inputSchema format
        input_schema = tool.get("inputSchema", tool.get("input_schema", {}))
        if not isinstance(input_schema, dict):
            return params

        properties = input_schema.get("properties", {})
        required_set = set(input_schema.get("required", []))

        for prop_name, prop_def in properties.items():
            if not isinstance(prop_def, dict):
                continue
            params.append({
                "name": prop_name,
                "type": prop_def.get("type", "string"),
                "required": prop_name in required_set,
            })

        return params

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Return health status for the agent absorption mode."""
        return {
            "status": "ok",
            "mode": "agent",
            "initial_confidence": self._initial_confidence,
            "llm_available": self._llm_client is not None,
        }
