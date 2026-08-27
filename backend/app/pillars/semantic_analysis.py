import json
import os
from pathlib import Path
from typing import List, Optional, Dict, Any

from app.pillars.base import Pillar, PillarResult, Finding
from app.llm.provider import get_provider, LLMProvider, TimeoutError


class SemanticAnalysisPillar(Pillar):
    name = "Semantic Analysis"

    # JSON contract from 02_ARCHITECTURE.md D17
    JSON_SCHEMA = {
        "type": "object",
        "required": ["purpose", "architecture_summary", "modules", "key_dependencies", "findings", "score"],
        "properties": {
            "purpose": {"type": "string"},
            "architecture_summary": {"type": "string"},
            "modules": {"type": "array", "items": {"type": "string"}},
            "key_dependencies": {"type": "array", "items": {"type": "string"}},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["severity", "category", "message"],
                    "properties": {
                        "severity": {"type": "string", "enum": ["info", "low", "medium", "high"]},
                        "category": {"type": "string"},
                        "message": {"type": "string"},
                    },
                },
            },
            "score": {"type": "integer", "minimum": 0, "maximum": 100},
        },
    }

    RETRY_INSTRUCTION = (
        "\n\nIMPORTANT: Your previous response was not valid JSON. "
        "You MUST respond with ONLY the JSON object matching the schema above. "
        "No prose, no markdown, no explanation before or after. Just the JSON."
    )

    def run(self, repo_path: Path, *, timeout_s: int) -> PillarResult:
        provider = get_provider()

        # Build prompt from repo structure (limited to context)
        prompt = self._build_prompt(repo_path)

        # Try once, then retry once with stricter instruction
        for attempt in range(2):
            try:
                response = provider.generate(
                    prompt + (self.RETRY_INSTRUCTION if attempt == 1 else ""),
                    max_tokens=2000,
                    timeout_s=timeout_s,
                )

                parsed = self._parse_and_validate(response)
                return self._result_from_parsed(parsed)

            except TimeoutError:
                return PillarResult(
                    name=self.name,
                    status="failed",
                    tier=1,
                    score=None,
                    summary="Semantic Analysis timed out (LLM request exceeded timeout)",
                    findings=[Finding(severity="high", category="llm_timeout", message="Groq request timed out")],
                )
            except RuntimeError as e:
                if "rate limit" in str(e).lower():
                    return PillarResult(
                        name=self.name,
                        status="failed",
                        tier=1,
                        score=None,
                        summary="Semantic Analysis unavailable: Groq rate limit exceeded",
                        findings=[Finding(severity="high", category="llm_rate_limit", message="Groq rate limit exceeded")],
                    )
                if attempt == 1:
                    # Last attempt failed with non-rate-limit error
                    return PillarResult(
                        name=self.name,
                        status="failed",
                        tier=1,
                        score=None,
                        summary=f"Semantic Analysis failed: {e}",
                        findings=[Finding(severity="high", category="llm_error", message=str(e))],
                    )
                # First attempt failed with non-parse error - retry
                continue
            except (json.JSONDecodeError, ValueError) as e:
                if attempt == 1:
                    # Second parse failure
                    return PillarResult(
                        name=self.name,
                        status="failed",
                        tier=1,
                        score=None,
                        summary="Semantic Analysis failed: LLM output unparseable after retry",
                        findings=[Finding(severity="high", category="llm_output_unparseable", message=f"LLM response could not be parsed as valid JSON: {e}")],
                    )
                # First parse failure - will retry
                continue

        # Should not reach here, but safety fallback
        return PillarResult(
            name=self.name,
            status="failed",
            tier=1,
            score=None,
            summary="Semantic Analysis failed after retries",
            findings=[Finding(severity="high", category="llm_output_unparseable", message="LLM output unparseable after retry")],
        )

    def _build_prompt(self, repo_path: Path) -> str:
        """Build a structured prompt from repo structure, mindful of context limits."""
        # Get file tree (very limited to avoid 413 errors)
        file_tree = self._get_file_tree(repo_path, max_files=10)

        # Read only README - skip other key files to minimize request size
        readme_content = self._read_readme_only(repo_path, max_chars=500)

        # Compact schema description
        schema_desc = (
            '{"purpose": "str", "architecture_summary": "str", "modules": ["str"], '
            '"key_dependencies": ["str"], "findings": [{"severity": "info|low|medium|high", "category": "str", "message": "str"}], "score": 0-100}'
        )

        prompt = f"""Analyze this repository. Respond with ONLY JSON matching:

{schema_desc}

Scoring (0-100): Architecture (0-40), Module boundaries (0-30), Deps (0-30).

FINDINGS RULES (CRITICAL):
- findings[] MUST ONLY contain actual issues, risks, or problems — NOT praise or positive observations
- severity MUST reflect urgency: high = critical issue needing immediate attention, medium = significant issue, low = minor issue, info = informational note
- Positive observations (e.g., "clear boundaries", "good documentation") go in architecture_summary, NOT in findings[]
- If no issues exist, findings[] MUST be empty array

REPO STRUCTURE (top 10):
{file_tree}

README (truncated):
{readme_content}

Respond with ONLY JSON."""

        return prompt

    def _get_file_tree(self, repo_path: Path, max_files: int = 10) -> str:
        """Get a condensed file tree for the prompt."""
        ignore_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", "env", "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache", "tests", "test", "docs", "examples", "scripts"}
        ignore_exts = {".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".bin", ".img", ".iso", ".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".lock"}

        files = []
        for p in repo_path.rglob("*"):
            if p.is_file():
                rel = p.relative_to(repo_path)
                if any(part in ignore_dirs for part in rel.parts):
                    continue
                if p.suffix in ignore_exts:
                    continue
                files.append(str(rel))
                if len(files) >= max_files:
                    break

        return "\n".join(sorted(files)) if files else "(empty repository)"

    def _read_readme_only(self, repo_path: Path, max_chars: int = 500) -> str:
        """Read only README file to minimize request size."""
        readme_files = list(repo_path.glob("README*")) + list(repo_path.glob("readme*"))
        if not readme_files:
            return "(no README found)"
        try:
            text = readme_files[0].read_text(encoding="utf-8", errors="replace")
            if len(text) > max_chars:
                text = text[:max_chars] + "\n... (truncated)"
            return text
        except Exception:
            return "(README read error)"

    def _parse_and_validate(self, response: str) -> Dict[str, Any]:
        """Parse and validate the LLM response against the JSON schema."""
        # Strip any markdown code fences
        cleaned = response.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines)

        parsed = json.loads(cleaned)

        # Validate required fields
        required = ["purpose", "architecture_summary", "modules", "key_dependencies", "findings", "score"]
        for field in required:
            if field not in parsed:
                raise ValueError(f"Missing required field: {field}")

        # Validate types
        if not isinstance(parsed["purpose"], str):
            raise ValueError("'purpose' must be a string")
        if not isinstance(parsed["architecture_summary"], str):
            raise ValueError("'architecture_summary' must be a string")
        if not isinstance(parsed["modules"], list):
            raise ValueError("'modules' must be a list")
        if not isinstance(parsed["key_dependencies"], list):
            raise ValueError("'key_dependencies' must be a list")
        if not isinstance(parsed["findings"], list):
            raise ValueError("'findings' must be a list")
        if not isinstance(parsed["score"], int):
            raise ValueError("'score' must be an integer")

        # Validate findings structure
        for i, finding in enumerate(parsed["findings"]):
            if not isinstance(finding, dict):
                raise ValueError(f"Finding {i} must be an object")
            for req in ["severity", "category", "message"]:
                if req not in finding:
                    raise ValueError(f"Finding {i} missing required field: {req}")
            if finding["severity"] not in ["info", "low", "medium", "high"]:
                raise ValueError(f"Finding {i} severity must be one of: info, low, medium, high")

        # Validate score range
        score = parsed["score"]
        if score < 0 or score > 100:
            raise ValueError(f"Score must be 0-100, got {score}")

        return parsed

    def _result_from_parsed(self, parsed: Dict[str, Any]) -> PillarResult:
        """Convert parsed JSON to PillarResult."""
        findings = [
            Finding(
                severity=f["severity"],
                category=f["category"],
                message=f["message"],
            )
            for f in parsed["findings"]
        ]

        high_count = sum(1 for f in findings if f.severity == "high")
        medium_count = sum(1 for f in findings if f.severity == "medium")
        low_count = sum(1 for f in findings if f.severity == "low")

        summary = (
            f"LLM analysis: {parsed['purpose'][:80]}... "
            f"({high_count} high, {medium_count} medium, {low_count} low findings)"
        )

        return PillarResult(
            name=self.name,
            status="complete",
            tier=1,
            score=parsed["score"],
            summary=summary,
            findings=findings,
        )


# Testable parse function for unit testing the retry logic
def parse_llm_response(response: str, *, allow_retry: bool = True) -> Dict[str, Any]:
    """Parse and validate LLM response. Used for testing the retry logic independently."""
    pillar = SemanticAnalysisPillar()
    return pillar._parse_and_validate(response)


if __name__ == "__main__":
    import sys
    from app.pillars.base import run_pillar_cli

    if len(sys.argv) < 3:
        print("Usage: python -m app.pillars.semantic_analysis --path /path/to/repo [--timeout 90]")
        sys.exit(1)

    path_idx = sys.argv.index("--path") if "--path" in sys.argv else -1
    timeout_idx = sys.argv.index("--timeout") if "--timeout" in sys.argv else -1
    repo_path = sys.argv[path_idx + 1] if path_idx != -1 and path_idx + 1 < len(sys.argv) else sys.argv[1]
    timeout_s = int(sys.argv[timeout_idx + 1]) if timeout_idx != -1 and timeout_idx + 1 < len(sys.argv) else 90

    run_pillar_cli(SemanticAnalysisPillar, repo_path, timeout_s)