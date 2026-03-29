#!/usr/bin/env python3
"""
SpongeBot Comprehensive Smoke Test
===================================
Tests each subsystem with clear PASS/FAIL output.
Uses a temporary directory for all data files and cleans up after.

Run:  source .venv/bin/activate && python3 tests/smoke_test.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_results: list[tuple[str, bool, str]] = []


def record(subsystem: str, passed: bool, detail: str = "") -> None:
    tag = "PASS" if passed else "FAIL"
    msg = f"[{tag}] {subsystem}"
    if detail:
        msg += f"  --  {detail}"
    print(msg)
    _results.append((subsystem, passed, detail))


def run_test(subsystem: str, fn):
    """Run a test function, catching all exceptions."""
    try:
        fn()
        # If fn didn't call record() itself, mark PASS
    except Exception as exc:
        record(subsystem, False, f"Exception: {exc}\n{traceback.format_exc()}")


def run_async_test(subsystem: str, coro_fn):
    """Run an async test function via asyncio."""
    try:
        asyncio.get_event_loop().run_until_complete(coro_fn())
    except Exception as exc:
        record(subsystem, False, f"Exception: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# 1. Config System
# ---------------------------------------------------------------------------

def test_config_system():
    from src.core.config import load_config, get_config, reset_config, DEFAULT_CONFIG

    # Reset singleton to avoid cross-test contamination
    reset_config()

    cfg = load_config()

    # Verify the returned object is a dict
    assert isinstance(cfg, dict), f"Expected dict, got {type(cfg)}"

    # Verify all expected top-level keys exist
    expected_keys = [
        "spongebot", "lockdown", "llm", "memory",
        "absorption", "skills", "learning", "token_saver",
        "security", "api", "branding",
    ]
    missing = [k for k in expected_keys if k not in cfg]
    assert not missing, f"Missing config keys: {missing}"

    # Verify get_config() returns the same singleton
    cfg2 = get_config()
    assert cfg is cfg2, "get_config() should return the same singleton object"

    # Verify nested values from DEFAULT_CONFIG are present
    assert cfg["llm"]["model"] is not None, "llm.model should exist"
    assert cfg["lockdown"]["enabled"] is not None, "lockdown.enabled should exist"
    assert isinstance(cfg["lockdown"]["blocked_providers"], list), "blocked_providers should be a list"

    record("1. Config System", True, f"All {len(expected_keys)} top-level keys present, singleton works")

    # Clean up singleton for other tests
    reset_config()


# ---------------------------------------------------------------------------
# 2. Security Vault
# ---------------------------------------------------------------------------

def test_security_vault(tmp_path: Path):
    from src.security.vault_core import VaultCore

    vault_dir = tmp_path / "vault_test"

    # Use low iteration count for speed in smoke test
    vault = VaultCore(
        data_dir=vault_dir,
        vault_password="smoke-test-password-42",
        pbkdf2_iterations=10_000,
    )

    # 2a. Encrypt / decrypt round-trip (bytes)
    plaintext = b"The Krabby Patty Formula is TOP SECRET"
    ciphertext = vault.encrypt(plaintext)
    assert ciphertext != plaintext, "Ciphertext should differ from plaintext"
    decrypted = vault.decrypt(ciphertext)
    assert decrypted == plaintext, f"Round-trip failed: {decrypted!r} != {plaintext!r}"
    record("2a. Vault encrypt/decrypt (bytes)", True, "Round-trip OK")

    # 2b. Encrypt / decrypt round-trip (text convenience)
    secret_text = "Buu's absorption matrix key: ALPHA-OMEGA-42"
    enc_text = vault.encrypt_text(secret_text)
    assert isinstance(enc_text, str), "encrypt_text should return str"
    dec_text = vault.decrypt_text(enc_text)
    assert dec_text == secret_text, f"Text round-trip failed: {dec_text!r}"
    record("2b. Vault encrypt/decrypt (text)", True, "Text round-trip OK")

    # 2c. Store / retrieve secret
    vault.store_secret("api_key", "sk-ant-api03-TESTKEY123")
    retrieved = vault.retrieve_secret("api_key")
    assert retrieved == "sk-ant-api03-TESTKEY123", f"Secret mismatch: {retrieved!r}"
    record("2c. Vault store/retrieve secret", True, "Secret stored and retrieved")

    # 2d. Verify sentinel
    sentinel_ok = vault.verify_sentinel()
    assert sentinel_ok is True, "Sentinel verification should pass"
    record("2d. Vault sentinel verification", True, "Sentinel intact")

    # 2e. List secrets
    vault.store_secret("db_password", "super-secret-db")
    names = vault.list_secrets()
    assert "api_key" in names, "api_key should be in list_secrets"
    assert "db_password" in names, "db_password should be in list_secrets"
    record("2e. Vault list secrets", True, f"Listed {len(names)} secrets")

    # 2f. Re-open vault with same password (verify persistence)
    vault2 = VaultCore(
        data_dir=vault_dir,
        vault_password="smoke-test-password-42",
        pbkdf2_iterations=10_000,
    )
    assert vault2.retrieve_secret("api_key") == "sk-ant-api03-TESTKEY123", "Persisted secret should survive re-open"
    record("2f. Vault persistence across re-open", True, "Secrets persist on disk")


# ---------------------------------------------------------------------------
# 3. Audit Chain
# ---------------------------------------------------------------------------

def test_audit_chain(tmp_path: Path):
    from src.security.audit_chain import AuditChain

    chain_dir = tmp_path / "audit_test"
    chain = AuditChain(data_dir=chain_dir)

    # 3a. Append entries
    e1 = chain.append("security", "vault_initialized", "Vault created for smoke test")
    assert e1.sequence == 0, f"First entry should be seq 0, got {e1.sequence}"
    assert e1.prev_hash == "GENESIS", f"First prev_hash should be GENESIS, got {e1.prev_hash}"
    assert e1.entry_hash, "entry_hash should be computed"

    e2 = chain.append("skill", "skill_added", "Added test skill: python_basics")
    assert e2.sequence == 1, f"Second entry should be seq 1, got {e2.sequence}"
    assert e2.prev_hash == e1.entry_hash, "Second entry prev_hash should match first entry_hash"

    e3 = chain.append("lockdown", "boot_complete", "All 9 layers green")
    assert chain.length == 3, f"Chain should have 3 entries, got {chain.length}"

    record("3a. Audit chain append", True, f"{chain.length} entries added with correct linking")

    # 3b. Verify chain integrity
    valid, reason = chain.verify_chain()
    assert valid is True, f"Chain should be valid, got: {reason}"
    record("3b. Audit chain integrity verification", True, "Chain verified OK")

    # 3c. Query by category
    log = chain.get_log(category="skill")
    assert len(log) == 1, f"Expected 1 skill entry, got {len(log)}"
    assert log[0]["action"] == "skill_added"
    record("3c. Audit chain query by category", True, "Filtered query works")

    # 3d. Invalid category
    try:
        chain.append("invalid_category", "test", "should fail")
        record("3d. Audit chain invalid category", False, "Should have raised ValueError")
    except ValueError:
        record("3d. Audit chain invalid category rejection", True, "ValueError raised as expected")


# ---------------------------------------------------------------------------
# 4. Lockdown Gate (individual layer testing, not full boot)
# ---------------------------------------------------------------------------

def test_lockdown_gate():
    from src.lockdown.anthropic_gate import AnthropicGate
    from src.lockdown.model_verifier import ModelVerifier

    gate = AnthropicGate()
    verifier = ModelVerifier()

    # 4a. Valid Anthropic key accepted
    ok, reason = gate.validate("sk-ant-api03-ABCDEF1234567890abcdef")
    assert ok is True, f"Valid Anthropic key should pass: {reason}"
    record("4a. Lockdown: valid Anthropic key accepted", True, reason)

    # 4b. OpenAI key rejected
    ok, reason = gate.validate("sk-openai-ABCDEF1234567890")
    assert ok is False, "OpenAI key should be rejected"
    assert "OpenAI" in reason, f"Reason should mention OpenAI: {reason}"
    record("4b. Lockdown: OpenAI key rejected", True, reason)

    # 4c. Empty key rejected
    ok, reason = gate.validate("")
    assert ok is False, "Empty key should be rejected"
    record("4c. Lockdown: empty key rejected", True, reason)

    # 4d. Claude model accepted
    ok, reason = verifier.validate("claude-sonnet-4-20250514")
    assert ok is True, f"Claude model should pass: {reason}"
    record("4d. Lockdown: claude-sonnet-4 model accepted", True, reason)

    # 4e. Claude 3.5 model accepted
    ok, reason = verifier.validate("claude-3-5-sonnet-20241022")
    assert ok is True, f"Claude 3.5 model should pass: {reason}"
    record("4e. Lockdown: claude-3-5-sonnet accepted", True, reason)

    # 4f. GPT-4 rejected
    ok, reason = verifier.validate("gpt-4")
    assert ok is False, "GPT-4 should be rejected"
    assert "OpenAI" in reason, f"Reason should mention OpenAI: {reason}"
    record("4f. Lockdown: gpt-4 model rejected", True, reason)

    # 4g. Gemini rejected
    ok, reason = verifier.validate("gemini-pro")
    assert ok is False, "Gemini should be rejected"
    assert "Google" in reason, f"Reason should mention Google: {reason}"
    record("4g. Lockdown: gemini-pro rejected", True, reason)

    # 4h. Random model rejected
    ok, reason = verifier.validate("my-custom-model")
    assert ok is False, "Unknown model should be rejected"
    record("4h. Lockdown: unknown model rejected", True, reason)


# ---------------------------------------------------------------------------
# 5. Token Saver
# ---------------------------------------------------------------------------

def test_token_saver(tmp_path: Path):
    from src.token_saver._engine import TokenSaver

    saver_dir = tmp_path / "token_saver_test"
    saver = TokenSaver(data_dir=saver_dir)

    # 5a. Compress prompt (L1)
    raw_prompt = """
    ===================================
    You are a helpful assistant.
    ===================================

        Please respond clearly.
        Be concise and accurate.

    -----------------------------------
    """
    compressed = saver.compress_prompt(raw_prompt)
    assert len(compressed) < len(raw_prompt), (
        f"Compressed ({len(compressed)}) should be shorter than original ({len(raw_prompt)})"
    )
    assert "helpful assistant" in compressed, "Core content should survive compression"
    record("5a. TokenSaver compress_prompt (L1)", True, f"{len(raw_prompt)} -> {len(compressed)} chars")

    # 5b. Cache response / check cache round-trip (L2)
    test_msg = "What is the meaning of life?"
    test_resp = "42, according to Douglas Adams."

    # Should miss initially
    cached = saver.check_cache(test_msg)
    assert cached is None, "Cache should miss on first lookup"

    # Store and then hit
    saver.cache_response(test_msg, test_resp)
    cached = saver.check_cache(test_msg)
    assert cached == test_resp, f"Cache should hit: {cached!r}"
    record("5b. TokenSaver cache round-trip (L2)", True, "Store/check/hit cycle works")

    # 5c. KV compression round-trip (L4)
    raw_bytes = b"key1=value1;key2=value2;key3=value3;" * 100
    compressed_kv = saver.compress_kv(raw_bytes)
    assert len(compressed_kv) < len(raw_bytes), "KV compression should reduce size"
    decompressed_kv = saver.decompress_kv(compressed_kv)
    assert decompressed_kv == raw_bytes, "KV decompression should restore original"
    record("5c. TokenSaver KV compress/decompress (L4)", True,
           f"{len(raw_bytes)} -> {len(compressed_kv)} bytes ({len(compressed_kv)/len(raw_bytes)*100:.1f}%)")

    # 5d. Skill distillation (L5)
    skill_data = {
        "name": "python_sort",
        "steps": [
            {"type": "action", "content": "Import list", "thinking": "considering approach"},
            {"type": "action", "content": "Call sorted()", "reasoning": "built-in is fastest"},
            {"type": "intermediate", "content": "Checking edge cases", "debug": True},
            {"type": "result", "content": "Sorted list returned", "output": "[1,2,3]"},
        ],
        "outcome": "success",
        "metadata": {"author": "smoke_test", "created_at": 1700000000, "internal_scratch": "junk"},
    }
    distilled = saver.distill_skill(skill_data)
    assert distilled["name"] == "python_sort", "Name should be preserved"
    assert len(distilled["steps"]) <= len(skill_data["steps"]), "Distilled should have fewer or equal steps"
    assert distilled["outcome"] == "success", "Outcome should be preserved"
    record("5d. TokenSaver skill distillation (L5)", True,
           f"{len(skill_data['steps'])} -> {len(distilled['steps'])} steps")

    # 5e. Conversation window management (L7)
    messages = [
        {"role": "user", "content": f"Message {i}: some content here about topic {i}"}
        for i in range(30)
    ]
    managed = saver.manage_window(messages)
    assert len(managed) < len(messages), "Window should compress older messages"
    assert managed[-1]["content"] == messages[-1]["content"], "Most recent message should be verbatim"
    record("5e. TokenSaver conversation window (L7)", True,
           f"{len(messages)} -> {len(managed)} messages")

    # 5f. Savings report
    report = saver.get_report()
    assert "Token Saver Report" in report, "Report should contain header"
    assert "Layer Statistics" in report, "Report should contain layer stats"
    record("5f. TokenSaver savings report", True, "Report generated successfully")


# ---------------------------------------------------------------------------
# 6. Skill DAG
# ---------------------------------------------------------------------------

def test_skill_dag(tmp_path: Path):
    from src.skills.dag import SkillDAG, SkillNode
    import networkx as nx

    dag_path = tmp_path / "skill_dag_test" / "skill_dag.json"
    config = {
        "skills": {
            "confidence_decay_half_life_days": 7,
            "prune_threshold": 0.15,
            "prune_after_days": 7,
        },
    }
    dag = SkillDAG(config=config, persist_path=dag_path)

    # 6a. Add skills
    s1 = SkillNode(
        name="python_basics",
        description="Basic Python programming",
        skill_type="atomic",
        confidence=0.8,
        tags=["python", "programming", "basics"],
    )
    s2 = SkillNode(
        name="data_analysis",
        description="Data analysis with pandas",
        skill_type="composed",
        confidence=0.6,
        prerequisites=["python_basics"],
        tags=["python", "data", "pandas"],
    )
    s3 = SkillNode(
        name="machine_learning",
        description="Machine learning with scikit-learn",
        skill_type="composed",
        confidence=0.4,
        prerequisites=["data_analysis"],
        tags=["python", "ml", "sklearn"],
    )

    dag.add_skill(s1)
    dag.add_skill(s2)
    dag.add_skill(s3)

    stats = dag.stats()
    assert stats["node_count"] == 3, f"Expected 3 nodes, got {stats['node_count']}"
    assert stats["edge_count"] == 2, f"Expected 2 edges, got {stats['edge_count']}"
    record("6a. SkillDAG add skills", True, f"{stats['node_count']} nodes, {stats['edge_count']} edges")

    # 6b. Query by capability
    results = dag.find_relevant("python programming basics")
    assert len(results) > 0, "Should find at least one skill for 'python programming basics'"
    assert results[0].name == "python_basics", f"Best match should be python_basics, got {results[0].name}"
    record("6b. SkillDAG query by capability", True, f"Found {len(results)} skills")

    # 6c. Verify acyclicity
    assert stats["is_dag"] is True, "Graph should be a DAG"

    # Attempt to create a cycle
    try:
        cycle_skill = SkillNode(
            name="cycle_test",
            description="This would create a cycle",
            skill_type="atomic",
            confidence=0.5,
            prerequisites=["machine_learning"],
        )
        dag.add_skill(cycle_skill)
        # Now try to add edge back to create cycle
        dag.add_edge("cycle_test", "python_basics", "requires")
        record("6c. SkillDAG acyclicity check", False, "Should have rejected cycle")
    except ValueError as exc:
        record("6c. SkillDAG acyclicity check", True, f"Cycle rejected: {str(exc)[:60]}")

    # 6d. Confidence decay
    # Set last_used to a long time ago to trigger decay
    old_skill = dag.get_skill("python_basics")
    old_conf = old_skill.confidence
    old_skill.last_used = old_skill.created_at - (7 * 24 * 3600)  # 7 days ago
    decayed_count = dag.decay_confidence()
    new_skill = dag.get_skill("python_basics")
    assert new_skill.confidence <= old_conf, (
        f"Confidence should decay: {old_conf} -> {new_skill.confidence}"
    )
    record("6d. SkillDAG confidence decay", True,
           f"Decayed {decayed_count} skills, python_basics: {old_conf:.3f} -> {new_skill.confidence:.3f}")

    # 6e. Composition query
    composition = dag.get_composition("machine_learning")
    names = [s.name for s in composition]
    assert "python_basics" in names, "Composition should include python_basics"
    assert "data_analysis" in names, "Composition should include data_analysis"
    assert "machine_learning" in names, "Composition should include machine_learning"
    record("6e. SkillDAG composition query", True, f"Composition: {' -> '.join(names)}")

    # 6f. Persistence
    dag.save()
    assert dag_path.exists(), "DAG should be saved to disk"
    record("6f. SkillDAG persistence", True, f"Saved to {dag_path}")


# ---------------------------------------------------------------------------
# 7. Learning Engine
# ---------------------------------------------------------------------------

def test_learning_engine():
    from src.learning.engine import LearningEngine

    config = {
        "learning": {
            "tier1_promotion_threshold": 3,
            "tier2_promotion_threshold": 3,
        },
    }
    engine = LearningEngine(config=config)

    async def _run():
        # Boot the engine
        await engine.boot()

        # 7a. Add tier-1 learnings
        interaction = {
            "user_input": "How do I sort a list in Python?",
            "response": "Use sorted() or list.sort()",
            "type": "question",
            "intent": "python_sort",
        }
        result1 = await engine.learn(interaction, session_id="smoke_session")
        assert result1["tier"] == 1, f"First interaction should stay in Tier 1, got tier {result1['tier']}"
        assert result1["action"] == "stored", f"Should be 'stored', got '{result1['action']}'"
        record("7a. LearningEngine tier-1 storage", True, f"tier={result1['tier']}, action={result1['action']}")

        # 7b. Add more similar interactions to trigger promotion
        for i in range(2):
            result = await engine.learn(
                {
                    "user_input": f"How do I sort a list in Python? (variant {i})",
                    "response": f"Use sorted() or list.sort() (variant {i})",
                    "type": "question",
                    "intent": "python_sort",
                },
                session_id="smoke_session",
            )

        # After 3 similar, should be promoted to tier 2
        assert result["tier"] == 2, f"Third similar interaction should promote to Tier 2, got tier {result['tier']}"
        record("7b. LearningEngine tier-1->tier-2 promotion", True,
               f"tier={result['tier']}, action={result['action']}")

        # 7c. Verify tier stats
        stats = await engine.get_tier_stats()
        assert stats["tier1"]["interaction_count"] >= 3, f"Tier 1 should have 3+ interactions"
        assert stats["tier2"]["pattern_count"] >= 1, f"Tier 2 should have 1+ patterns"
        record("7c. LearningEngine tier stats", True,
               f"T1 interactions={stats['tier1']['interaction_count']}, T2 patterns={stats['tier2']['pattern_count']}")

        # 7d. Consolidate session
        consolidation = await engine.consolidate_session("smoke_session")
        assert consolidation["interactions_reviewed"] >= 3
        record("7d. LearningEngine session consolidation", True,
               f"Reviewed {consolidation['interactions_reviewed']} interactions")

        # Shutdown
        await engine.shutdown()

    asyncio.get_event_loop().run_until_complete(_run())


# ---------------------------------------------------------------------------
# 8. CLI Splash
# ---------------------------------------------------------------------------

def test_cli_splash():
    from src.cli.splash import SPONGEBOT_SPLASH, BUU_ASCII, random_buu_quote, BUU_QUOTES

    # 8a. SPONGEBOT_SPLASH is non-empty string
    assert isinstance(SPONGEBOT_SPLASH, str), f"SPONGEBOT_SPLASH should be str, got {type(SPONGEBOT_SPLASH)}"
    assert len(SPONGEBOT_SPLASH) > 50, f"SPONGEBOT_SPLASH too short: {len(SPONGEBOT_SPLASH)}"
    # The word SPONGEBOT is rendered as ASCII art (figlet-style), not literal text.
    # Verify via the subtitle that is in plain text.
    assert "Absorption" in SPONGEBOT_SPLASH, "Splash should contain 'Absorption' subtitle"
    assert "Claude" in SPONGEBOT_SPLASH, "Splash should mention Claude"
    record("8a. CLI splash: SPONGEBOT_SPLASH", True, f"{len(SPONGEBOT_SPLASH)} chars")

    # 8b. BUU_ASCII is non-empty string
    assert isinstance(BUU_ASCII, str), f"BUU_ASCII should be str, got {type(BUU_ASCII)}"
    assert len(BUU_ASCII) > 30, f"BUU_ASCII too short: {len(BUU_ASCII)}"
    assert "BUU" in BUU_ASCII.upper(), "BUU_ASCII should contain BUU"
    record("8b. CLI splash: BUU_ASCII", True, f"{len(BUU_ASCII)} chars")

    # 8c. random_buu_quote returns a string from BUU_QUOTES
    quote = random_buu_quote()
    assert isinstance(quote, str), f"random_buu_quote should return str, got {type(quote)}"
    assert len(quote) > 0, "Quote should be non-empty"
    assert quote in BUU_QUOTES, "Quote should come from BUU_QUOTES list"
    record("8c. CLI splash: random_buu_quote", True, f"Got: {quote[:50]}...")


# ---------------------------------------------------------------------------
# 9. Personality Persona
# ---------------------------------------------------------------------------

def test_personality():
    from src.personality.persona import SpongeBotPersona

    persona = SpongeBotPersona()

    # 9a. Generate system prompt (no context)
    prompt = persona.build_system_prompt()
    assert isinstance(prompt, str), f"System prompt should be str, got {type(prompt)}"
    assert len(prompt) > 100, f"Prompt too short: {len(prompt)}"
    assert "SpongeBot" in prompt, "Prompt should mention SpongeBot"
    assert "Claude" in prompt, "Prompt should mention Claude"
    assert "absorb" in prompt.lower(), "Prompt should mention absorption"
    record("9a. Persona: system prompt (no context)", True, f"{len(prompt)} chars")

    # 9b. Generate system prompt with context
    prompt_ctx = persona.build_system_prompt(
        skills_context="Available skills: python_basics (0.8), data_analysis (0.6)",
        memory_context="User previously asked about sorting algorithms.",
        extra_instructions="Respond in JSON format.",
    )
    assert "python_basics" in prompt_ctx, "Prompt should include skills context"
    assert "sorting algorithms" in prompt_ctx, "Prompt should include memory context"
    assert "JSON" in prompt_ctx, "Prompt should include extra instructions"
    record("9b. Persona: system prompt (with context)", True, "All contexts injected")

    # 9c. Format response with mood
    raw_response = "Here is the sorted list: [1, 2, 3]"
    formatted = persona.format_response(raw_response, mood="excited")
    assert "[BUU HAPPY]" in formatted, "Excited mood should have [BUU HAPPY] prefix"
    assert raw_response in formatted, "Original response should be preserved"
    record("9c. Persona: format_response with mood", True, f"Prefix: {formatted.split(chr(10))[0][:40]}")

    # 9d. Neutral mood (no prefix)
    neutral = persona.format_response(raw_response, mood="neutral")
    assert neutral == raw_response, "Neutral mood should return raw text unchanged"
    record("9d. Persona: format_response neutral", True, "No prefix for neutral mood")

    # 9e. Absorption celebration
    celebration = persona.get_absorption_celebration("python_basics")
    assert isinstance(celebration, str), "Celebration should be a string"
    assert "python_basics" in celebration, "Celebration should mention the skill name"
    record("9e. Persona: absorption celebration", True, f"Got: {celebration[:50]}...")

    # 9f. Lockdown refusal
    refusal = persona.lockdown_refusal("openai")
    assert isinstance(refusal, str), "Refusal should be a string"
    assert "openai" in refusal.lower(), "Refusal should mention the provider"
    record("9f. Persona: lockdown refusal", True, f"Got: {refusal[:50]}...")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("  SpongeBot Comprehensive Smoke Test")
    print("=" * 70)
    print()

    # Create temp directory for all data files
    tmp_dir = Path(tempfile.mkdtemp(prefix="spongebot_smoke_"))
    print(f"Temp directory: {tmp_dir}\n")

    try:
        print("--- 1. Config System ---")
        run_test("1. Config System", test_config_system)
        print()

        print("--- 2. Security Vault ---")
        run_test("2. Security Vault", lambda: test_security_vault(tmp_dir))
        print()

        print("--- 3. Audit Chain ---")
        run_test("3. Audit Chain", lambda: test_audit_chain(tmp_dir))
        print()

        print("--- 4. Lockdown Gate ---")
        run_test("4. Lockdown Gate", test_lockdown_gate)
        print()

        print("--- 5. Token Saver ---")
        run_test("5. Token Saver", lambda: test_token_saver(tmp_dir))
        print()

        print("--- 6. Skill DAG ---")
        run_test("6. Skill DAG", lambda: test_skill_dag(tmp_dir))
        print()

        print("--- 7. Learning Engine ---")
        run_test("7. Learning Engine", test_learning_engine)
        print()

        print("--- 8. CLI Splash ---")
        run_test("8. CLI Splash", test_cli_splash)
        print()

        print("--- 9. Personality Persona ---")
        run_test("9. Personality", test_personality)
        print()

    finally:
        # Clean up temp directory
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print(f"Cleaned up temp directory: {tmp_dir}")

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------

    print()
    print("=" * 70)
    print("  SMOKE TEST SUMMARY")
    print("=" * 70)

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    total = len(_results)

    for subsystem, ok, detail in _results:
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}] {subsystem}")

    print()
    print(f"  Total: {total}  |  Passed: {passed}  |  Failed: {failed}")
    print("=" * 70)

    if failed > 0:
        print("\n  SOME TESTS FAILED -- see output above for details.\n")
        sys.exit(1)
    else:
        print("\n  ALL TESTS PASSED\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
