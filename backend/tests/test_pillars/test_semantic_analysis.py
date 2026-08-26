"""Tests for Semantic Analysis pillar - specifically the one-retry-then-fail parse logic (D17)."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from app.pillars.semantic_analysis import SemanticAnalysisPillar, parse_llm_response
from app.pillars.base import PillarResult, Finding
from app.llm.provider import TimeoutError


class TestSemanticAnalysisParseLogic:
    """Test the _parse_and_validate method and retry logic independently."""

    def test_valid_json_response_parses_correctly(self):
        """A valid JSON response should parse and return a PillarResult."""
        valid_response = json.dumps({
            "purpose": "A web framework for building APIs",
            "architecture_summary": "Modular design with clear separation of routing, middleware, and handlers",
            "modules": ["routing", "middleware", "handlers", "validation"],
            "key_dependencies": ["starlette", "pydantic"],
            "findings": [
                {"severity": "medium", "category": "architecture", "message": "Middleware chain could be more explicit"}
            ],
            "score": 85
        })

        pillar = SemanticAnalysisPillar()
        parsed = pillar._parse_and_validate(valid_response)

        assert parsed["purpose"] == "A web framework for building APIs"
        assert parsed["score"] == 85
        assert len(parsed["findings"]) == 1
        assert parsed["findings"][0]["severity"] == "medium"

    def test_malformed_json_first_failure_raises(self):
        """First parse failure should raise JSONDecodeError (triggers retry)."""
        malformed = "This is not JSON at all, just prose explanation."

        pillar = SemanticAnalysisPillar()
        with pytest.raises(json.JSONDecodeError):
            pillar._parse_and_validate(malformed)

    def test_missing_required_field_raises(self):
        """Missing required field should raise ValueError (triggers retry)."""
        incomplete = json.dumps({
            "purpose": "Test",
            "architecture_summary": "Test",
            "modules": [],
            "key_dependencies": [],
            "findings": [],
            # score missing
        })

        pillar = SemanticAnalysisPillar()
        with pytest.raises(ValueError, match="Missing required field: score"):
            pillar._parse_and_validate(incomplete)

    def test_invalid_severity_raises(self):
        """Invalid severity should raise ValueError (triggers retry)."""
        bad_severity = json.dumps({
            "purpose": "Test",
            "architecture_summary": "Test",
            "modules": [],
            "key_dependencies": [],
            "findings": [{"severity": "critical", "category": "test", "message": "test"}],
            "score": 50
        })

        pillar = SemanticAnalysisPillar()
        with pytest.raises(ValueError, match="severity must be one of"):
            pillar._parse_and_validate(bad_severity)

    def test_score_out_of_range_raises(self):
        """Score outside 0-100 should raise ValueError (triggers retry)."""
        bad_score = json.dumps({
            "purpose": "Test",
            "architecture_summary": "Test",
            "modules": [],
            "key_dependencies": [],
            "findings": [],
            "score": 150
        })

        pillar = SemanticAnalysisPillar()
        with pytest.raises(ValueError, match="Score must be 0-100"):
            pillar._parse_and_validate(bad_score)


class TestSemanticAnalysisRetryLogic:
    """Test the full run() method retry-then-fail behavior with mocked LLM."""

    def test_first_parse_failure_retries_then_succeeds(self):
        """First response malformed, second valid -> should succeed on retry."""
        pillar = SemanticAnalysisPillar()

        # Mock provider that fails first, succeeds second
        mock_provider = Mock()
        call_count = [0]

        def mock_generate(prompt, *, max_tokens, timeout_s):
            call_count[0] += 1
            if call_count[0] == 1:
                return "Not JSON at all, just text"
            return json.dumps({
                "purpose": "Test repo",
                "architecture_summary": "Simple",
                "modules": ["main"],
                "key_dependencies": [],
                "findings": [],
                "score": 75
            })

        mock_provider.generate = mock_generate

        with patch("app.pillars.semantic_analysis.get_provider", return_value=mock_provider):
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                result = pillar.run(Path(tmpdir), timeout_s=90)

        assert result.status == "complete"
        assert result.score == 75
        assert call_count[0] == 2  # Called twice (initial + retry)

    def test_both_attempts_fail_marks_failed_with_reason(self):
        """Both responses malformed -> status=failed, reason=llm_output_unparseable."""
        pillar = SemanticAnalysisPillar()

        mock_provider = Mock()
        mock_provider.generate = Mock(return_value="Not JSON at all")

        with patch("app.pillars.semantic_analysis.get_provider", return_value=mock_provider):
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                result = pillar.run(Path(tmpdir), timeout_s=90)

        assert result.status == "failed"
        assert result.tier == 1
        assert result.score is None
        assert "llm_output_unparseable" in result.summary.lower() or any(
            f.category == "llm_output_unparseable" for f in result.findings
        )

    def test_timeout_on_first_attempt_marks_failed(self):
        """Timeout on first attempt -> status=failed with timeout reason (no retry on timeout)."""
        pillar = SemanticAnalysisPillar()

        mock_provider = Mock()
        mock_provider.generate = Mock(side_effect=TimeoutError("Groq request timed out"))

        with patch("app.pillars.semantic_analysis.get_provider", return_value=mock_provider):
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                result = pillar.run(Path(tmpdir), timeout_s=90)

        assert result.status == "failed"
        assert any(f.category == "llm_timeout" for f in result.findings)

    def test_rate_limit_marks_failed(self):
        """Rate limit error -> status=failed with rate_limit reason."""
        pillar = SemanticAnalysisPillar()

        mock_provider = Mock()
        mock_provider.generate = Mock(side_effect=RuntimeError("Groq rate limit exceeded"))

        with patch("app.pillars.semantic_analysis.get_provider", return_value=mock_provider):
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                result = pillar.run(Path(tmpdir), timeout_s=90)

        assert result.status == "failed"
        assert any(f.category == "llm_rate_limit" for f in result.findings)

    def test_other_runtime_error_on_second_attempt_marks_failed(self):
        """Non-parse, non-timeout, non-rate-limit error on second attempt -> failed."""
        pillar = SemanticAnalysisPillar()

        mock_provider = Mock()
        call_count = [0]

        def mock_generate(prompt, *, max_tokens, timeout_s):
            call_count[0] += 1
            if call_count[0] == 1:
                return "Not JSON"  # Parse failure -> retry
            raise RuntimeError("Network error")

        mock_provider.generate = mock_generate

        with patch("app.pillars.semantic_analysis.get_provider", return_value=mock_provider):
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                result = pillar.run(Path(tmpdir), timeout_s=90)

        assert result.status == "failed"
        assert call_count[0] == 2
        assert any(f.category == "llm_error" for f in result.findings)


import json
from pathlib import Path