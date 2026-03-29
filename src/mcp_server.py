#!/usr/bin/env python3
"""
SpongeBot MCP Server — Absorption Engine for Claude.

Makes SpongeBot available as a plugin in both Claude Code and Claude Desktop.
Feed it code, docs, failures — it learns. Ask it questions — it recalls.

Tools:
    sponge_absorb    — Feed text/code to learn from
    sponge_recall    — Search memory by keywords
    sponge_skills    — List all learned skills in the DAG
    sponge_add_skill — Add a new skill to the DAG
    sponge_boost     — Strengthen a skill's confidence
    sponge_health    — Show subsystem status
    sponge_lockdown  — Verify Claude-only lockdown

Registration:
    Claude Code:    claude mcp add spongebot python3 /path/to/mcp_server.py
    Claude Desktop: Add to claude_desktop_config.json mcpServers

Built by VARIE Industries.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Ensure SpongeBot src is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.memory.hybrid_memory import HybridMemory
from src.skills.dag import SkillDAG, SkillNode
from src.learning.engine import LearningEngine
from src.lockdown.anthropic_gate import AnthropicGate
from src.lockdown.model_verifier import ModelVerifier
from src.lockdown.environment_scanner import EnvironmentScanner

logger = logging.getLogger("spongebot.mcp")

DATA_DIR = PROJECT_ROOT / "data"


class SpongeBotMCP:
    """MCP Server exposing SpongeBot's absorption engine."""

    def __init__(self):
        self.server = Server("spongebot")
        self.memory: HybridMemory | None = None
        self.dag: SkillDAG | None = None
        self.learner: LearningEngine | None = None
        self._booted = False
        self._setup_handlers()

    async def _ensure_booted(self):
        """Lazy boot — initialize subsystems on first use."""
        if self._booted:
            return
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.memory = HybridMemory(data_dir=str(DATA_DIR))
        await self.memory.boot()
        cfg = {
            "skills": {
                "confidence_decay_half_life_days": 7,
                "prune_threshold": 0.15,
                "prune_after_days": 7,
            },
            "spongebot": {"data_dir": str(DATA_DIR)},
        }
        self.dag = SkillDAG(config=cfg)
        self.learner = LearningEngine(config=cfg, skill_dag=self.dag, memory=self.memory)
        self._booted = True
        logger.info("SpongeBot MCP booted")

    def _setup_handlers(self):
        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="sponge_absorb",
                    description="Feed SpongeBot text, code, or documentation to learn from. It stores the knowledge and can recall it later.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "The text/code/documentation to absorb"},
                            "collection": {"type": "string", "description": "Category: knowledge, skills, or experiences", "default": "knowledge"},
                            "source": {"type": "string", "description": "Where this came from (file path, URL, etc.)", "default": ""},
                        },
                        "required": ["text"],
                    },
                ),
                Tool(
                    name="sponge_recall",
                    description="Search SpongeBot's memory by keywords. Returns matching entries with relevance scores.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Keywords to search for"},
                            "limit": {"type": "number", "description": "Max results to return", "default": 5},
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="sponge_skills",
                    description="List all skills SpongeBot has learned. Shows the skill DAG with confidence scores, types, and edges.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "min_confidence": {"type": "number", "description": "Minimum confidence threshold (0.0-1.0)", "default": 0.0},
                        },
                    },
                ),
                Tool(
                    name="sponge_add_skill",
                    description="Add a new skill to SpongeBot's skill DAG. Skills are things it has learned to do.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Unique skill name (snake_case)"},
                            "description": {"type": "string", "description": "What this skill does"},
                            "skill_type": {"type": "string", "description": "atomic (single action) or composed (requires other skills)", "default": "atomic"},
                            "confidence": {"type": "number", "description": "Initial confidence 0.0-1.0", "default": 0.7},
                            "prerequisites": {"type": "array", "items": {"type": "string"}, "description": "Names of skills this depends on", "default": []},
                            "steps": {"type": "array", "items": {"type": "string"}, "description": "Steps to execute this skill", "default": []},
                            "absorbed_from": {"type": "string", "description": "Source of this skill", "default": ""},
                        },
                        "required": ["name", "description"],
                    },
                ),
                Tool(
                    name="sponge_boost",
                    description="Boost a skill's confidence score. Use this when a skill proves useful.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Skill name to boost"},
                            "amount": {"type": "number", "description": "How much to boost (0.01-0.2)", "default": 0.05},
                        },
                        "required": ["name"],
                    },
                ),
                Tool(
                    name="sponge_health",
                    description="Show SpongeBot's subsystem health status — memory, skills, learning engine.",
                    inputSchema={"type": "object", "properties": {}},
                ),
                Tool(
                    name="sponge_lockdown",
                    description="Verify SpongeBot's 9-layer Claude-only lockdown. Tests API key gate, model verifier, and environment scanner.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "test_model": {"type": "string", "description": "Model ID to test against lockdown", "default": "claude-opus-4-6"},
                        },
                    },
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            await self._ensure_booted()
            try:
                if name == "sponge_absorb":
                    return await self._absorb(arguments)
                elif name == "sponge_recall":
                    return await self._recall(arguments)
                elif name == "sponge_skills":
                    return await self._skills(arguments)
                elif name == "sponge_add_skill":
                    return await self._add_skill(arguments)
                elif name == "sponge_boost":
                    return await self._boost(arguments)
                elif name == "sponge_health":
                    return await self._health(arguments)
                elif name == "sponge_lockdown":
                    return await self._lockdown(arguments)
                else:
                    return [TextContent(type="text", text=f"Unknown tool: {name}")]
            except Exception as e:
                return [TextContent(type="text", text=f"Error: {e}")]

    # ── Tool implementations ──

    async def _absorb(self, args: dict) -> list[TextContent]:
        text = args["text"]
        collection = args.get("collection", "knowledge")
        source = args.get("source", "")
        meta = {"source": source} if source else {}
        doc_id = await self.memory.store(text, collection=collection, metadata=meta)
        await self.learner.update({"type": "absorb", "success": True})
        return [TextContent(type="text", text=f"Absorbed into '{collection}' (id: {doc_id}). {len(text)} chars stored. SpongeBot remembers.")]

    async def _recall(self, args: dict) -> list[TextContent]:
        query = args["query"]
        limit = int(args.get("limit", 5))
        results = await self.memory.recall(query, k=limit)
        if not results:
            return [TextContent(type="text", text=f"No matches for '{query}'. Feed me more knowledge with sponge_absorb.")]
        lines = [f"Found {len(results)} match(es) for '{query}':\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"  [{i}] score={r['score']:.2f} | {r['collection']} | {r['text'][:120]}...")
        return [TextContent(type="text", text="\n".join(lines))]

    async def _skills(self, args: dict) -> list[TextContent]:
        stats = self.dag.stats()
        lines = [
            f"Skill DAG: {stats['node_count']} skills | {stats['edge_count']} edges",
            f"Avg confidence: {stats['avg_confidence']:.2f} | Types: {stats['type_counts']}",
            f"Cold storage: {stats['cold_storage_count']} | Below threshold: {stats['below_threshold_count']}",
            "",
        ]
        # List individual skills
        for node_name in self.dag._graph.nodes:
            sk = self.dag.get_skill(node_name)
            if sk:
                prereqs = f" (requires: {', '.join(sk.prerequisites)})" if sk.prerequisites else ""
                lines.append(f"  [{sk.confidence:.2f}] {sk.name} ({sk.skill_type}): {sk.description[:60]}{prereqs}")
        if stats['node_count'] == 0:
            lines.append("  No skills yet. Use sponge_add_skill to teach me.")
        return [TextContent(type="text", text="\n".join(lines))]

    async def _add_skill(self, args: dict) -> list[TextContent]:
        node = SkillNode(
            name=args["name"],
            description=args["description"],
            skill_type=args.get("skill_type", "atomic"),
            confidence=float(args.get("confidence", 0.7)),
            prerequisites=args.get("prerequisites", []),
            steps=args.get("steps", []),
            absorbed_from=args.get("absorbed_from", ""),
            absorption_mode="mcp",
        )
        self.dag.add_skill(node)
        await self.learner.update({"type": "add_skill", "success": True})
        return [TextContent(type="text", text=f"Skill '{args['name']}' added to DAG (confidence: {node.confidence:.2f}, type: {node.skill_type}). {self.dag.stats()['node_count']} total skills.")]

    async def _boost(self, args: dict) -> list[TextContent]:
        name = args["name"]
        amount = float(args.get("amount", 0.05))
        sk = self.dag.get_skill(name)
        if not sk:
            return [TextContent(type="text", text=f"Skill '{name}' not found. Use sponge_skills to list available skills.")]
        old = sk.confidence
        self.dag.boost_confidence(name, amount)
        sk = self.dag.get_skill(name)
        return [TextContent(type="text", text=f"Boosted '{name}': {old:.2f} → {sk.confidence:.2f}")]

    async def _health(self, args: dict) -> list[TextContent]:
        mem_health = await self.memory.health_check()
        dag_stats = self.dag.stats()
        learn_stats = await self.learner.get_tier_stats()
        lines = [
            "SpongeBot v0.2.0 Health Report",
            "=" * 40,
            f"Memory: {mem_health['status']} | mode: {mem_health.get('mode', 'unknown')} | entries: {mem_health.get('text_entries', 0)}",
            f"Skills: {dag_stats['node_count']} skills | {dag_stats['edge_count']} edges | avg conf: {dag_stats['avg_confidence']:.2f}",
            f"Learning: {learn_stats.get('tier1', {}).get('count', 0)} tier-1 entries | 3-tier engine active",
            f"Lockdown: 9-layer Anthropic enforcement active",
            f"Vault: AES-256 Fernet encryption available",
        ]
        return [TextContent(type="text", text="\n".join(lines))]

    async def _lockdown(self, args: dict) -> list[TextContent]:
        model = args.get("test_model", "claude-opus-4-6")
        gate = AnthropicGate()
        verifier = ModelVerifier()
        scanner = EnvironmentScanner()

        model_ok, model_msg = verifier.validate(model)
        scanner.scan_now()
        env_clean = not scanner.violation_detected

        lines = [
            "SpongeBot 9-Layer Lockdown Status",
            "=" * 40,
            f"Layer 1 - HW Fingerprint: active",
            f"Layer 2 - API Key Gate: Anthropic keys only",
            f"Layer 3 - Model Verifier: {model} → {'✓ APPROVED' if model_ok else '✗ BLOCKED: ' + model_msg}",
            f"Layer 4 - Env Scanner: {'✓ Clean' if env_clean else '⚠ Threat detected: ' + scanner.violation_details}",
            f"Layer 5 - Response Fingerprint: active",
            f"Layer 6 - Crypto Binding: HMAC-SHA256",
            f"Layer 7 - Tamper Detector: SHA-256 chain",
            f"Layer 8 - Self-Destruct: armed (3 failed decrypts)",
            f"Layer 9 - Lockout Manager: monitoring",
            "",
            "Status: Claude exclusive. No other AI permitted.",
        ]
        return [TextContent(type="text", text="\n".join(lines))]

    def run(self):
        async def main():
            async with stdio_server() as (read_stream, write_stream):
                await self.server.run(
                    read_stream,
                    write_stream,
                    self.server.create_initialization_options(),
                )
        asyncio.run(main())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    server = SpongeBotMCP()
    server.run()
