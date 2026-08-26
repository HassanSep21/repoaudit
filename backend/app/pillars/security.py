import subprocess
import json
import os
import re
from pathlib import Path
from typing import List, Optional

from app.pillars.base import Pillar, PillarResult, Finding


class SecurityPillar(Pillar):
    name = "Security"

    def run(self, repo_path: Path, *, timeout_s: int) -> PillarResult:
        python_files = list(repo_path.rglob("*.py"))
        js_ts_files = list(repo_path.rglob("*.js")) + list(repo_path.rglob("*.ts")) + \
                       list(repo_path.rglob("*.jsx")) + list(repo_path.rglob("*.tsx"))
        
        is_tier1_python = len(python_files) > 0
        is_tier1_js_ts = len(js_ts_files) > 0

        if not is_tier1_python and not is_tier1_js_ts:
            return self._run_tier2_cloc(repo_path, timeout_s)

        findings = []
        tier = 1

        if is_tier1_python:
            findings.extend(self._run_bandit(repo_path, timeout_s))
            findings.extend(self._run_semgrep(repo_path, timeout_s, "python"))

        if is_tier1_js_ts:
            findings.extend(self._run_npm_audit(repo_path, timeout_s))
            findings.extend(self._run_semgrep(repo_path, timeout_s, "javascript"))

        findings.extend(self._run_trivy(repo_path, timeout_s))

        score = self._calculate_score(findings)

        lang_parts = []
        if is_tier1_python:
            lang_parts.append("Python")
        if is_tier1_js_ts:
            lang_parts.append("JavaScript/TypeScript")

        high_count = sum(1 for f in findings if f.severity == "high")
        medium_count = sum(1 for f in findings if f.severity == "medium")
        low_count = sum(1 for f in findings if f.severity == "low")

        summary = f"Tier-1 analysis ({', '.join(lang_parts)}): {high_count} high, {medium_count} medium, {low_count} low findings"

        return PillarResult(
            name=self.name,
            status="complete",
            tier=tier,
            score=score,
            summary=summary,
            findings=findings,
        )

    def _run_bandit(self, repo_path: Path, timeout_s: int) -> List[Finding]:
        findings = []
        try:
            # Use -q to suppress INFO logs that corrupt JSON output
            result = subprocess.run(
                ["bandit", "-r", "-f", "json", "-q", str(repo_path)],
                capture_output=True, text=True, timeout=timeout_s
            )
            if result.stdout and result.stdout.strip():
                try:
                    data = json.loads(result.stdout)
                    for issue in data.get("results", []):
                        raw_message = issue.get("issue_text", "Bandit issue")
                        file_path = issue.get("filename")
                        line = issue.get("line_number")
                        test_id = issue.get("test_id")
                        
                        # Skip low-value noise (by test_id or message pattern)
                        if self._is_bandit_noise(raw_message, file_path, test_id):
                            continue
                        
                        redacted_message = self._redact_secrets(raw_message)
                        findings.append(Finding(
                            severity=self._map_bandit_severity(issue.get("issue_severity", "")),
                            category="bandit",
                            message=redacted_message,
                            file_path=file_path,
                            line=line,
                        ))
                    
                    # Cap at reasonable limit with notice (Rule 7: specific findings, but avoid flood)
                    if len(findings) > 100:
                        excess = len(findings) - 100
                        findings = findings[:100]
                        findings.append(Finding(
                            severity="info",
                            category="truncated",
                            message=f"Truncated: {excess} additional findings omitted (use pillar CLI for full output)",
                            file_path=None,
                            line=None,
                        ))
                except json.JSONDecodeError:
                    if result.stderr:
                        findings.append(Finding(severity="medium", category="tool_error", message=f"Bandit error: {result.stderr[:200]}"))
                    else:
                        findings.append(Finding(severity="medium", category="tool_error", message="Bandit produced invalid JSON output"))
            elif result.stderr:
                findings.append(Finding(severity="medium", category="tool_error", message=f"Bandit error: {result.stderr[:200]}"))
        except subprocess.TimeoutExpired:
            findings.append(Finding(severity="high", category="tool_timeout", message="Bandit timed out"))
        except Exception as e:
            findings.append(Finding(severity="medium", category="tool_error", message=f"Bandit failed: {e}"))
        return findings

    def _is_bandit_noise(self, message: str, file_path: str | None, test_id: str | None = None) -> bool:
        """Filter out low-value Bandit findings that flood the output."""
        if not file_path:
            return False
        
        is_test_file = "test" in file_path or "Test" in file_path
        
        # Skip by test_id ONLY in test files - these are noisy in test contexts
        noisy_in_tests = {"B603", "B404", "B607", "B604", "B110", "B311", "B108"}
        if test_id in noisy_in_tests and is_test_file:
            return True
        
        # Skip "assert detected" in test files - not a security issue
        if "assert detected" in message.lower() and is_test_file:
            return True
        
        # Skip "standard pseudo-random generators" in test files
        if "pseudo-random" in message.lower() and is_test_file:
            return True
        
        # Skip unused import warnings (style, not security)
        if "imported but unused" in message.lower():
            return True
        
        return False

    def _run_semgrep(self, repo_path: Path, timeout_s: int, language: str) -> List[Finding]:
        findings = []
        try:
            config = "p/security-audit" if language == "python" else "p/security-audit"
            result = subprocess.run(
                ["semgrep", "scan", "--config", config, "--json", "--quiet", str(repo_path)],
                capture_output=True, text=True, timeout=timeout_s,
                env={**os.environ, "SEMGREP_ENABLE_VERSION_CHECK": "0", "SEMGREP_SEND_METRICS": "off"}
            )
            if result.stdout:
                data = json.loads(result.stdout)
                for result_item in data.get("results", []):
                    raw_message = result_item.get("extra", {}).get("message", "Semgrep finding")
                    redacted_message = self._redact_secrets(raw_message)
                    
                    findings.append(Finding(
                        severity=self._map_semgrep_severity(result_item.get("extra", {}).get("severity", "")),
                        category="semgrep",
                        message=redacted_message,
                        file_path=result_item.get("path"),
                        line=result_item.get("start", {}).get("line"),
                    ))
        except subprocess.TimeoutExpired:
            findings.append(Finding(severity="high", category="tool_timeout", message="Semgrep timed out"))
        except Exception as e:
            findings.append(Finding(severity="medium", category="tool_error", message=f"Semgrep failed: {e}"))
        return findings

    def _run_npm_audit(self, repo_path: Path, timeout_s: int) -> List[Finding]:
        findings = []
        package_json = repo_path / "package.json"
        if not package_json.exists():
            return findings
        
        try:
            result = subprocess.run(
                ["npm", "audit", "--json"],
                capture_output=True, text=True, timeout=timeout_s,
                cwd=str(repo_path)
            )
            if result.stdout:
                data = json.loads(result.stdout)
                for vuln in data.get("vulnerabilities", {}).values():
                    severity = vuln.get("severity", "low")
                    findings.append(Finding(
                        severity=severity,
                        category="npm_audit",
                        message=f"Vulnerable dependency: {vuln.get('name', 'unknown')} - {vuln.get('title', 'No title')}",
                        file_path="package.json",
                        line=None,
                    ))
        except subprocess.TimeoutExpired:
            findings.append(Finding(severity="high", category="tool_timeout", message="npm audit timed out"))
        except Exception:
            pass
        return findings

    def _run_trivy(self, repo_path: Path, timeout_s: int) -> List[Finding]:
        findings = []
        try:
            result = subprocess.run(
                ["trivy", "fs", "--format", "json", "--quiet", str(repo_path)],
                capture_output=True, text=True, timeout=timeout_s
            )
            if result.stdout:
                data = json.loads(result.stdout)
                for target in data.get("Results", []):
                    for vuln in target.get("Vulnerabilities", []):
                        severity = vuln.get("Severity", "LOW").lower()
                        findings.append(Finding(
                            severity=severity,
                            category="trivy",
                            message=f"Vulnerable dependency: {vuln.get('PkgName', 'unknown')} ({vuln.get('VulnerabilityID', 'unknown')})",
                            file_path=target.get("Target"),
                            line=None,
                        ))
        except subprocess.TimeoutExpired:
            findings.append(Finding(severity="high", category="tool_timeout", message="Trivy timed out"))
        except Exception:
            pass
        return findings

    def _run_tier2_cloc(self, repo_path: Path, timeout_s: int) -> PillarResult:
        findings = []
        try:
            result = subprocess.run(
                ["cloc", "--json", str(repo_path)],
                capture_output=True, text=True, timeout=timeout_s
            )
            if result.stdout:
                data = json.loads(result.stdout)
                total_lines = data.get("SUM", {}).get("code", 0)
                if total_lines > 100000:
                    findings.append(Finding(
                        severity="low",
                        category="size",
                        message=f"Large codebase ({total_lines:,} lines) - security review recommended",
                        file_path=None,
                        line=None,
                    ))
        except Exception:
            pass

        base_score = 60
        score = max(0, base_score - len(findings) * 5)

        return PillarResult(
            name=self.name,
            status="partial",
            tier=2,
            score=score,
            summary="Tier-2 (best-effort) analysis: basic cloc metrics only. No Tier-1 language detected.",
            findings=findings,
        )

    def _map_bandit_severity(self, severity: str) -> str:
        return {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}.get(severity.upper(), "low")

    def _map_semgrep_severity(self, severity: str) -> str:
        return {"ERROR": "high", "WARNING": "medium", "INFO": "low"}.get(severity.upper(), "low")

    def _redact_secrets(self, message: str) -> str:
        patterns = [
            (r'(["\'])?(api[_-]?key|secret[_-]?key|access[_-]?token|password|passwd)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_\-]{16,})["\']?', r'\1\2=***REDACTED***'),
            (r'(["\'])?(aws[_-]?access[_-]?key[_-]?id|aws[_-]?secret[_-]?access[_-]?key)["\']?\s*[:=]\s*["\']?([A-Z0-9/+=]{16,})["\']?', r'\1\2=***REDACTED***'),
            (r'(["\'])?(github[_-]?token|gh[_-]?token)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_]{16,})["\']?', r'\1\2=***REDACTED***'),
            (r'(["\'])?(slack[_-]?token|slack[_-]?webhook)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9/+=]{20,})["\']?', r'\1\2=***REDACTED***'),
            (r'(["\'])?(private[_-]?key|ssh[_-]?key)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9/+=]{32,})["\']?', r'\1\2=***REDACTED***'),
            (r'(["\'])?(database[_-]?url|db[_-]?url|postgres://|mysql://|mongodb://)[^"\'\s]{16,}', r'\1\2***REDACTED***'),
            (r'(["\']?)([a-zA-Z0-9_\-]{32,})(["\']?)', lambda m: m.group(1) + m.group(2)[:4] + "***" + m.group(2)[-4:] + m.group(3) if len(m.group(2)) > 10 else m.group(0)),
            (r'(hardcoded\s+(?:password|secret|api[_-]?key|token)[:=]\s*["\'])([a-zA-Z0-9/+=_\-]{16,})(["\'])', r'\1***REDACTED***\3'),
        ]
        
        result = message
        for pattern, replacement in patterns:
            if callable(replacement):
                result = re.sub(pattern, replacement, result)
            else:
                result = re.sub(pattern, replacement, result)
        return result

    def _calculate_score(self, findings: List[Finding]) -> int:
        score = 100
        for f in findings:
            if f.severity == "high":
                score -= 15
            elif f.severity == "medium":
                score -= 7
            elif f.severity == "low":
                score -= 2
        return max(0, score)


if __name__ == "__main__":
    import sys
    from app.pillars.base import run_pillar_cli
    if len(sys.argv) < 3:
        print("Usage: python -m app.pillars.security --path /path/to/repo [--timeout 60]")
        sys.exit(1)
    path_idx = sys.argv.index("--path") if "--path" in sys.argv else -1
    timeout_idx = sys.argv.index("--timeout") if "--timeout" in sys.argv else -1
    repo_path = sys.argv[path_idx + 1] if path_idx != -1 and path_idx + 1 < len(sys.argv) else sys.argv[1]
    timeout_s = int(sys.argv[timeout_idx + 1]) if timeout_idx != -1 and timeout_idx + 1 < len(sys.argv) else 60
    run_pillar_cli(SecurityPillar, repo_path, timeout_s)