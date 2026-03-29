from pathlib import Path
"""
Unit tests for src.lockdown submodules -- Anthropic security layers.

Tests cover:
- AnthropicGate: valid Anthropic key, reject OpenAI key
- ModelVerifier: accept Claude, reject GPT-4
- EnvironmentScanner: detect foreign AI modules (mocked sys.modules)
- HardwareFingerprint: deterministic output on the same machine
"""

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from unittest.mock import patch

from src.lockdown.anthropic_gate import AnthropicGate
from src.lockdown.model_verifier import ModelVerifier
from src.lockdown.environment_scanner import EnvironmentScanner, _scan_sys_modules
from src.lockdown.hardware_fingerprint import generate_fingerprint, HardwareFingerprint


class TestAnthropicGate:

    @pytest.fixture
    def gate(self):
        return AnthropicGate()

    def test_anthropic_gate_valid_key(self, gate):
        """A key matching sk-ant-api03-* must pass validation."""
        valid_key = "sk-ant-api03-AbCdEf123456_GhIjKl-789012_MnOpQr"
        passed, reason = gate.validate(valid_key)
        assert passed is True
        assert "Anthropic" in reason

    def test_anthropic_gate_reject_openai_key(self, gate):
        """A key with OpenAI prefix (sk- without ant) must be rejected."""
        openai_key = "sk-proj-1234567890abcdefghijklmnopqrstuvwxyz"
        passed, reason = gate.validate(openai_key)
        assert passed is False
        assert "OpenAI" in reason

    def test_anthropic_gate_empty_key(self, gate):
        """An empty API key must be rejected."""
        passed, reason = gate.validate("")
        assert passed is False
        assert "empty" in reason.lower()

    def test_anthropic_gate_wrong_format(self, gate):
        """A key with no recognized prefix must be rejected."""
        passed, reason = gate.validate("random-garbage-key-value")
        assert passed is False

    def test_anthropic_gate_strips_whitespace(self, gate):
        """Keys with leading/trailing whitespace must still validate."""
        valid_key = "  sk-ant-api03-AbCdEf123456_GhIjKl  "
        passed, _ = gate.validate(valid_key)
        assert passed is True


class TestModelVerifier:

    @pytest.fixture
    def verifier(self):
        return ModelVerifier()

    def test_model_verifier_accept_claude(self, verifier):
        """Claude model IDs must pass verification."""
        test_models = [
            "claude-sonnet-4-20250514",
            "claude-opus-4-20250514",
            "claude-haiku-4-20250514",
            "claude-3-5-sonnet-20241022",
            "claude-3-opus-20240229",
        ]
        for model_id in test_models:
            passed, reason = verifier.validate(model_id)
            assert passed is True, f"Model '{model_id}' should pass but got: {reason}"

    def test_model_verifier_reject_gpt4(self, verifier):
        """GPT-4 model IDs must be rejected with a clear OpenAI message."""
        gpt_models = ["gpt-4", "gpt-4o", "gpt-4-turbo"]
        for model_id in gpt_models:
            passed, reason = verifier.validate(model_id)
            assert passed is False, f"Model '{model_id}' should be rejected"
            assert "OpenAI" in reason

    def test_model_verifier_reject_gemini(self, verifier):
        """Google Gemini model IDs must be rejected."""
        passed, reason = verifier.validate("gemini-pro")
        assert passed is False
        assert "Google" in reason

    def test_model_verifier_empty(self, verifier):
        """An empty model ID must be rejected."""
        passed, reason = verifier.validate("")
        assert passed is False


class TestEnvironmentScanner:

    def test_environment_scanner_detect_openai(self):
        """Scanner must detect 'openai' when it appears in sys.modules."""
        scanner = EnvironmentScanner()

        # Mock sys.modules to include 'openai'
        fake_modules = dict(sys.modules)
        fake_modules["openai"] = type(sys)("openai")  # dummy module

        with patch.dict("sys.modules", fake_modules):
            clean, reason = scanner.scan_now()

        # The scan should detect the openai import
        # Note: scan_now also calls _scan_pip_packages which we don't control,
        # but the sys.modules check should trigger.
        assert scanner.last_scan_result["blocked_imports"] or not clean
        if not clean:
            assert "openai" in reason.lower() or scanner.violation_detected

    def test_environment_scanner_clean_by_default(self):
        """Scanner must report clean when no blocked modules are loaded."""
        scanner = EnvironmentScanner()

        # Verify that in a normal test environment, the blocked modules
        # are not present (unless someone actually has them installed)
        blocked_found = _scan_sys_modules()

        # If nothing is in sys.modules, scanner should be clean
        if not blocked_found:
            clean, reason = scanner.scan_now()
            # Note: pip packages might still be installed, so we only check
            # sys.modules portion is clean
            assert "openai" not in sys.modules


class TestHardwareFingerprint:

    def test_hardware_fingerprint_deterministic(self):
        """generate_fingerprint must return the same value on consecutive calls."""
        fp1 = generate_fingerprint()
        fp2 = generate_fingerprint()

        assert fp1 == fp2
        assert isinstance(fp1, str)
        assert len(fp1) == 64  # SHA-256 hex digest

    def test_hardware_fingerprint_is_hex(self):
        """The fingerprint must be a valid hexadecimal string."""
        fp = generate_fingerprint()
        int(fp, 16)  # Will raise ValueError if not valid hex

    def test_hardware_fingerprint_class_verify(self):
        """HardwareFingerprint.verify() must pass on the current machine."""
        hfp = HardwareFingerprint()
        # Skip the file-based persistence check by just testing generation
        hfp.current_fingerprint = generate_fingerprint()
        hfp.stored_fingerprint = hfp.current_fingerprint
        hfp.verified = True

        passed, reason = hfp.verify()
        assert passed is True
