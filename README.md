# SpongeBot

**Feed it anything. Claude remembers everything.**

[![CI](https://github.com/Varie-Industries/spongebot/actions/workflows/ci.yml/badge.svg)](https://github.com/Varie-Industries/spongebot/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-107%20passed-2ea44f.svg)](tests/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/protocol-MCP-7c3aed.svg)](https://modelcontextprotocol.io)
[![Type Checked: mypy strict](https://img.shields.io/badge/mypy-strict-2ea44f.svg)](https://mypy.readthedocs.io)
[![Linted: ruff](https://img.shields.io/badge/lint-ruff-fa9d3b.svg)](https://github.com/astral-sh/ruff)
[![License: Proprietary](https://img.shields.io/badge/license-Proprietary-lightgrey.svg)](LICENSE)

SpongeBot is a persistent absorption engine for Claude, exposed as a Model Context Protocol server. Point it at code, docs, websites, agent manifests, or your own task trajectories. It indexes everything into a local skill graph that any Claude session can query. The longer you use it, the more capable Claude becomes inside your domain.

It runs locally. It is Anthropic-exclusive by design. There is no telemetry, no third-party storage, no network calls outside the Claude API.

---

## What it actually does

| You do this | SpongeBot does this | The next Claude session can |
| :--- | :--- | :--- |
| Paste a docs page, a codebase, an API spec | Parses it into actionable skill templates and stores them in the DAG | Recall exact patterns by keyword and apply them |
| Run a successful workflow and tell SpongeBot it worked | Distills the trajectory into a reusable plan with a confidence score | Replay that plan when a similar task appears |
| Hit a failure and tell SpongeBot what broke | Generates an anti-skill marking the path that did not work | Avoid the same dead end automatically |
| Import another agent's capability manifest | Extracts the agent's skills into the same graph | Use those skills as if they were native |
| Let two SpongeBot instances share encrypted metadata | Merges remote skills without exposing raw content | Compound knowledge across machines, privately |
| Walk away for a week | Decays unused skill confidence on a 7-day half-life | Surface only what is still likely to work |

The result is that Claude stops starting from zero. Every project you ship feeds the next one. Patterns that survive get stronger. Patterns that fail get pruned.

---

## The six absorption modes

Pass a `mode` to `sponge_absorb` to tag what kind of content you're feeding in. The MCP surface stores all six the same way — into keyword-recall memory with the mode tag attached — and the mode-specific handlers in `src/absorption/` are available as a Python library for programmatic callers.

| Mode | Source type | Intended handler (`src/absorption/`) |
| :--- | :--- | :--- |
| `document` | Docs, code, READMEs, API specs | Skill templates extracted from prose and signatures |
| `agent` | Other agents' capability manifests | The agent's published skills, importable as your own |
| `experience` | Successful task trajectories | A reusable plan with the steps that worked |
| `failure` | Failed trajectories | An anti-skill marking the path to avoid |
| `evolutionary` | Existing skills + a fitness signal | New skill variants bred via genetic operations |
| `federated` | Another SpongeBot instance, over an encrypted channel | Skill metadata only, never raw content |

Routing each mode through its dedicated handler is on the v0.3.0 roadmap.

---

## The six MCP tools

| Tool | What it does |
| :--- | :--- |
| `sponge_absorb` | Feed text or code. Pick an absorption mode and a collection. |
| `sponge_recall` | Keyword search across stored content. Returns top-K by relevance. |
| `sponge_skills` | List the current skill graph. Filter by minimum confidence. |
| `sponge_add_skill` | Insert a skill node directly with prerequisites and steps. |
| `sponge_boost` | Raise a skill's confidence after a successful use. |
| `sponge_health` | Subsystem status: memory, skill graph, learning engine, vault. |

---

## Install

Requires Python 3.11 or newer.

```bash
git clone https://github.com/Varie-Industries/spongebot.git
cd spongebot
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Register with Claude

**Claude Code:**

```bash
claude mcp add spongebot python3 /absolute/path/to/spongebot/src/mcp_server.py
```

**Claude Desktop** — edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "spongebot": {
      "command": "python3",
      "args": ["/absolute/path/to/spongebot/src/mcp_server.py"]
    }
  }
}
```

Restart Claude. SpongeBot appears in the available tools list.

---

## A worked example

Inside Claude, after registration:

```
Use sponge_absorb to remember this pattern, mode "document":
"To verify a StoreKit 2 transaction server-side: parse the JWS,
fetch Apple's public key by kid, verify ES256 signature, then
match the bundle ID and product ID before granting entitlement."

Use sponge_absorb to remember this failure, mode "failure":
"Calling StoreKit verifyTransaction without checking the
bundle ID let a replay attack through during testing."

Now use sponge_recall for "StoreKit JWS verification".
```

Claude pulls back both entries with relevance scores. The success pattern stays high-confidence. The failure pattern blocks the same mistake from being suggested again.

---

## Architecture

```
src/
├── mcp_server.py          # MCP entry point. Seven tools, async stdio loop.
├── core/                  # Configuration, runtime context, registries.
├── absorption/
│   ├── engine.py          # Orchestrator. Routes sources to the right mode.
│   ├── document_absorption.py
│   ├── agent_absorption.py
│   ├── experience_absorption.py
│   ├── failure_absorption.py
│   ├── evolutionary_absorption.py
│   └── federated_absorption.py
├── memory/
│   ├── hybrid_memory.py   # SQLite-backed store with keyword recall.
│   └── sqlite_store.py
├── skills/
│   └── dag.py             # NetworkX DAG. SkillNode dataclass, decay, prune.
├── learning/
│   └── engine.py          # Three-tier learning engine. Tracks events.
├── security/
│   ├── vault_core.py      # AES-256 Fernet encrypted vault.
│   ├── self_destruct.py   # Failed-decrypt counter and quick wipe.
│   └── audit_chain.py     # SHA-256 chained audit log.
├── llm/                   # Claude API client wrappers.
├── cli/                   # Optional standalone CLI.
├── personality/           # Tone shaping.
└── config/                # YAML configuration files.
```

**Stack:** Python 3.11, `mcp`, `anthropic`, `networkx`, `cryptography` (AES-256 Fernet), `pyyaml`, `click`, `rich`. Six runtime dependencies.

**Storage:** SQLite for memory and skill graph state. No external services.

---

## Anthropic-only by design

SpongeBot targets the Anthropic Claude API exclusively. There is no abstraction layer for other providers and no plan to add one. Your absorbed knowledge stays in your local SQLite store and only travels to the model you chose.

---

## Development

```bash
make install      # install with dev dependencies
make test         # pytest with coverage
make lint         # ruff check
make typecheck    # mypy strict
make all          # lint + typecheck + test
```

Tests live under `tests/{unit,integration,e2e}` with `conftest.py` for shared fixtures. `tests/smoke_test.py` exercises the full absorb-recall-skill loop end to end.

---

## Status

`v0.2.0`. The MCP server, absorption engine, skill DAG, and memory layer are functional. Treat this as a working prototype, not a hardened release. The federated mode is single-peer only.

---

## License

Proprietary. See [LICENSE](LICENSE). For commercial licensing, contact VARIE Industries.

---
