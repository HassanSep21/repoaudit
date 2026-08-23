import subprocess
import json
import os
from pathlib import Path
from typing import List, Optional

from app.pillars.base import Pillar, PillarResult, Finding


class CodeEvaluationPillar(Pillar):
    name = "Code Evaluation"
    
    def run(self, repo_path: Path, *, timeout_s: int) -> PillarResult:
        # Detect primary language
        python_files = list(repo_path.rglob("*.py"))
        js_ts_files = list(repo_path.rglob("*.js")) + list(repo_path.rglob("*.ts")) + \
                       list(repo_path.rglob("*.jsx")) + list(repo_path.rglob("*.tsx"))
        
        is_tier1_python = len(python_files) > 0
        is_tier1_js_ts = len(js_ts_files) > 0
        
        if not is_tier1_python and not is_tier1_js_ts:
            # Tier 2: fallback to cloc for basic metrics
            return self._run_tier2_cloc(repo_path, timeout_s)
        
        findings = []
        tier = 1
        
        if is_tier1_python:
            findings.extend(self._run_ruff(repo_path, timeout_s))
            findings.extend(self._run_radon(repo_path, timeout_s))
        
        if is_tier1_js_ts:
            findings.extend(self._run_eslint(repo_path, timeout_s))
        
        # Calculate score using shared formula (D18)
        score = self._calculate_score(findings)
        
        # Generate summary
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
    
    def _run_ruff(self, repo_path: Path, timeout_s: int) -> List[Finding]:
        findings = []
        try:
            result = subprocess.run(
                ["ruff", "check", "--output-format=json", str(repo_path)],
                capture_output=True, text=True, timeout=timeout_s
            )
            if result.stdout:
                issues = json.loads(result.stdout)
                for issue in issues:
                    findings.append(Finding(
                        severity=self._map_ruff_severity(issue.get("code", "")),
                        category="style",
                        message=issue.get("message", "Ruff issue"),
                        file_path=issue.get("filename"),
                        line=issue.get("location", {}).get("row"),
                    ))
        except subprocess.TimeoutExpired:
            findings.append(Finding(severity="high", category="tool_timeout", message="Ruff timed out"))
        except Exception as e:
            findings.append(Finding(severity="medium", category="tool_error", message=f"Ruff failed: {e}"))
        return findings
    
    def _run_radon(self, repo_path: Path, timeout_s: int) -> List[Finding]:
        findings = []
        try:
            # Run radon for cyclomatic complexity
            result = subprocess.run(
                ["radon", "cc", "-j", str(repo_path)],
                capture_output=True, text=True, timeout=timeout_s
            )
            if result.stdout:
                data = json.loads(result.stdout)
                for file_path, functions in data.items():
                    for func in functions:
                        if func.get("complexity", 0) > 10:
                            findings.append(Finding(
                                severity="medium" if func["complexity"] <= 20 else "high",
                                category="complexity",
                                message=f"High cyclomatic complexity ({func['complexity']}) in {func['name']}",
                                file_path=file_path,
                                line=func.get("lineno"),
                            ))
        except subprocess.TimeoutExpired:
            findings.append(Finding(severity="high", category="tool_timeout", message="Radon timed out"))
        except Exception:
            pass  # Radon output parsing can be fragile, don't crash
        return findings
    
    def _run_eslint(self, repo_path: Path, timeout_s: int) -> List[Finding]:
        findings = []
        try:
            # Check if eslint config exists
            eslint_config = repo_path / ".eslintrc.js"
            if not eslint_config.exists():
                eslint_config = repo_path / ".eslintrc.json"
            if not eslint_config.exists():
                eslint_config = repo_path / ".eslintrc.yml"
            if not eslint_config.exists():
                eslint_config = repo_path / "eslint.config.js"
            
            if not eslint_config.exists():
                return findings
            
            result = subprocess.run(
                ["eslint", "--format=json", str(repo_path)],
                capture_output=True, text=True, timeout=timeout_s
            )
            if result.stdout:
                data = json.loads(result.stdout)
                for file_result in data:
                    for msg in file_result.get("messages", []):
                        severity = "high" if msg.get("severity") == 2 else "medium" if msg.get("severity") == 1 else "info"
                        findings.append(Finding(
                            severity=severity,
                            category="lint",
                            message=msg.get("message", "ESLint issue"),
                            file_path=file_result.get("filePath"),
                            line=msg.get("line"),
                        ))
        except subprocess.TimeoutExpired:
            findings.append(Finding(severity="high", category="tool_timeout", message="ESLint timed out"))
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
                comment_lines = data.get("SUM", {}).get("comment", 0)
                
                if total_lines > 0:
                    comment_ratio = comment_lines / total_lines
                    if comment_ratio < 0.1:
                        findings.append(Finding(
                            severity="low",
                            category="documentation",
                            message=f"Low comment ratio ({comment_ratio:.1%}) - consider adding more comments",
                            file_path=None,
                            line=None,
                        ))
        except Exception:
            pass
        
        # Tier 2 gets a base score, slightly penalized for not having Tier 1 analysis
        base_score = 70
        score = max(0, base_score - len(findings) * 5)
        
        return PillarResult(
            name=self.name,
            status="partial",
            tier=2,
            score=score,
            summary="Tier-2 (best-effort) analysis: basic cloc metrics only. No Tier-1 language detected.",
            findings=findings,
        )
    
    def _map_ruff_severity(self, code: str) -> str:
        # Map ruff codes to severity
        if code.startswith("E9") or code.startswith("F"):  # Syntax errors, undefined names
            return "high"
        elif code.startswith("E") or code.startswith("W"):  # Errors, warnings
            return "medium"
        elif code.startswith("I"):  # Import issues
            return "low"
        elif code.startswith("UP") or code.startswith("C4"):  # Upgrade, comprehension
            return "info"
        else:
            return "low"
    
    def _calculate_score(self, findings: List[Finding]) -> int:
        """Shared scoring formula (D18): start at 100, deduct by severity."""
        score = 100
        for f in findings:
            if f.severity == "high":
                score -= 15
            elif f.severity == "medium":
                score -= 7
            elif f.severity == "low":
                score -= 2
            # info = 0
        return max(0, score)