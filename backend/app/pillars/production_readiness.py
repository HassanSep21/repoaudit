import os
import re
from pathlib import Path
from typing import List, Optional

from app.pillars.base import Pillar, PillarResult, Finding


class ProductionReadinessPillar(Pillar):
    name = "Production Readiness"

    def run(self, repo_path: Path, *, timeout_s: int) -> PillarResult:
        python_files = list(repo_path.rglob("*.py"))
        js_ts_files = list(repo_path.rglob("*.js")) + list(repo_path.rglob("*.ts")) + \
                       list(repo_path.rglob("*.jsx")) + list(repo_path.rglob("*.tsx"))
        
        is_tier1_python = len(python_files) > 0
        is_tier1_js_ts = len(js_ts_files) > 0

        if not is_tier1_python and not is_tier1_js_ts:
            return self._run_tier2_generic(repo_path, timeout_s)

        findings = []
        tier = 1

        findings.extend(self._check_ci_cd(repo_path))
        findings.extend(self._check_error_handling(repo_path, python_files, js_ts_files))
        findings.extend(self._check_logging(repo_path, python_files, js_ts_files))
        findings.extend(self._check_license(repo_path))
        findings.extend(self._check_dockerfile(repo_path))
        findings.extend(self._check_health_checks(repo_path))

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

    def _check_ci_cd(self, repo_path: Path) -> List[Finding]:
        findings = []
        ci_dirs = [
            repo_path / ".github" / "workflows",
            repo_path / ".gitlab" / "ci",
            repo_path / ".circleci",
            repo_path / ".travis.yml",
            repo_path / "azure-pipelines.yml",
            repo_path / "Jenkinsfile",
        ]
        
        has_ci = any(p.exists() for p in ci_dirs)
        
        if not has_ci:
            findings.append(Finding(
                severity="high",
                category="ci_missing",
                message="No CI/CD configuration found (.github/workflows, .gitlab-ci.yml, Jenkinsfile, etc.)",
                file_path=None,
                line=None,
            ))
        else:
            for ci_path in ci_dirs:
                if ci_path.exists():
                    if ci_path.is_dir():
                        workflow_files = list(ci_path.glob("*.yml")) + list(ci_path.glob("*.yaml"))
                        if not workflow_files:
                            findings.append(Finding(
                                severity="low",
                                category="ci_empty",
                                message=f"CI directory exists but contains no workflow files: {ci_path.relative_to(repo_path)}",
                                file_path=str(ci_path.relative_to(repo_path)),
                                line=None,
                            ))
                    break

        return findings

    def _check_error_handling(self, repo_path: Path, python_files: List[Path], js_ts_files: List[Path]) -> List[Finding]:
        findings = []
        all_files = python_files + js_ts_files
        
        if not all_files:
            return findings

        total_functions = 0
        functions_with_error_handling = 0

        for file_path in all_files[:50]:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                
                if file_path.suffix == ".py":
                    func_matches = re.findall(r'^\s*def\s+(\w+)\s*\([^)]*\):', content, re.MULTILINE)
                    for func_name in func_matches:
                        total_functions += 1
                        func_pattern = rf'^\s*def\s+{func_name}\s*\([^)]*\):'
                        match = re.search(func_pattern, content, re.MULTILINE)
                        if match:
                            func_body = content[match.end():match.end()+500]
                            if "try:" in func_body or "except " in func_body or "raise " in func_body:
                                functions_with_error_handling += 1
                
                elif file_path.suffix in [".js", ".ts", ".jsx", ".tsx"]:
                    func_matches = re.findall(r'(?:export\s+)?(?:async\s+)?function\s+(\w+)|const\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>', content)
                    for match in func_matches:
                        func_name = match[0] or match[1]
                        if func_name:
                            total_functions += 1
                            func_start = content.find(func_name)
                            if func_start != -1:
                                brace_count = 0
                                in_func = False
                                func_body = ""
                                for i, ch in enumerate(content[func_start:], start=func_start):
                                    if ch == "{":
                                        brace_count += 1
                                        in_func = True
                                    elif ch == "}":
                                        brace_count -= 1
                                    if in_func:
                                        func_body += ch
                                        if brace_count == 0:
                                            break
                                if "try {" in func_body or "catch " in func_body or "throw " in func_body:
                                    functions_with_error_handling += 1
            except Exception:
                pass

        if total_functions > 20:
            ratio = functions_with_error_handling / total_functions
            if ratio < 0.2:
                findings.append(Finding(
                    severity="medium",
                    category="poor_error_handling",
                    message=f"Low error handling coverage ({functions_with_error_handling}/{total_functions} functions have try/catch)",
                    file_path=None,
                    line=None,
                ))
            elif ratio < 0.5:
                findings.append(Finding(
                    severity="low",
                    category="partial_error_handling",
                    message=f"Partial error handling coverage ({functions_with_error_handling}/{total_functions} functions have try/catch)",
                    file_path=None,
                    line=None,
                ))

        return findings

    def _check_logging(self, repo_path: Path, python_files: List[Path], js_ts_files: List[Path]) -> List[Finding]:
        findings = []
        all_files = python_files + js_ts_files
        
        if not all_files:
            return findings

        files_with_logging = 0
        total_relevant_files = 0

        for file_path in all_files[:50]:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                
                is_relevant = False
                if file_path.suffix == ".py":
                    is_relevant = "import logging" in content or "from logging import" in content or "logger" in content
                    if is_relevant:
                        total_relevant_files += 1
                        if re.search(r'logger\.(debug|info|warning|error|critical)\(', content):
                            files_with_logging += 1
                
                elif file_path.suffix in [".js", ".ts", ".jsx", ".tsx"]:
                    is_relevant = "console.log" in content or "console.error" in content or "console.warn" in content or "winston" in content or "pino" in content or "bunyan" in content
                    if is_relevant:
                        total_relevant_files += 1
                        if re.search(r'console\.(log|error|warn|info)\(', content):
                            files_with_logging += 1
            except Exception:
                pass

        if total_relevant_files > 5:
            ratio = files_with_logging / total_relevant_files
            if ratio < 0.3:
                findings.append(Finding(
                    severity="medium",
                    category="poor_logging",
                    message=f"Low logging usage ({files_with_logging}/{total_relevant_files} relevant files use logging)",
                    file_path=None,
                    line=None,
                ))
            elif ratio < 0.6:
                findings.append(Finding(
                    severity="low",
                    category="partial_logging",
                    message=f"Partial logging coverage ({files_with_logging}/{total_relevant_files} relevant files use logging)",
                    file_path=None,
                    line=None,
                ))

        return findings

    def _check_license(self, repo_path: Path) -> List[Finding]:
        findings = []
        license_files = list(repo_path.glob("LICENSE*")) + list(repo_path.glob("license*")) + \
                       list(repo_path.glob("COPYING*")) + list(repo_path.glob("copying*"))
        
        if not license_files:
            findings.append(Finding(
                severity="medium",
                category="license_missing",
                message="No LICENSE file found in repository root",
                file_path=None,
                line=None,
            ))
        else:
            try:
                content = license_files[0].read_text(encoding="utf-8", errors="replace")
                if len(content) < 100:
                    findings.append(Finding(
                        severity="low",
                        category="license_short",
                        message="LICENSE file appears minimal or incomplete",
                        file_path=str(license_files[0].relative_to(repo_path)),
                        line=None,
                    ))
            except Exception:
                pass

        return findings

    def _check_dockerfile(self, repo_path: Path) -> List[Finding]:
        findings = []
        dockerfiles = list(repo_path.glob("Dockerfile*")) + list(repo_path.glob("dockerfile*")) + \
                     list(repo_path.glob("*.dockerfile"))
        
        if not dockerfiles:
            findings.append(Finding(
                severity="low",
                category="dockerfile_missing",
                message="No Dockerfile found - containerization not configured",
                file_path=None,
                line=None,
            ))
        else:
            dockerfile = dockerfiles[0]
            try:
                content = dockerfile.read_text(encoding="utf-8", errors="replace")
                if "HEALTHCHECK" not in content:
                    findings.append(Finding(
                        severity="low",
                        category="dockerfile_no_healthcheck",
                        message="Dockerfile lacks HEALTHCHECK instruction",
                        file_path=str(dockerfile.relative_to(repo_path)),
                        line=None,
                    ))
                if "USER " not in content:
                    findings.append(Finding(
                        severity="low",
                        category="dockerfile_no_user",
                        message="Dockerfile does not specify non-root USER",
                        file_path=str(dockerfile.relative_to(repo_path)),
                        line=None,
                    ))
            except Exception:
                pass

        return findings

    def _check_health_checks(self, repo_path: Path) -> List[Finding]:
        findings = []
        python_files = list(repo_path.rglob("*.py"))
        
        has_health_endpoint = False
        for file_path in python_files[:30]:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                if any(keyword in content.lower() for keyword in ["/health", "/healthz", "/ready", "/live", "healthcheck", "health_check"]):
                    has_health_endpoint = True
                    break
            except Exception:
                pass

        if not has_health_endpoint and python_files:
            findings.append(Finding(
                severity="low",
                category="health_endpoint_missing",
                message="No health check endpoint detected (/health, /healthz, /ready, etc.)",
                file_path=None,
                line=None,
            ))

        return findings

    def _run_tier2_generic(self, repo_path: Path, timeout_s: int) -> PillarResult:
        findings = []
        
        ci_dirs = [
            repo_path / ".github" / "workflows",
            repo_path / ".gitlab" / "ci",
            repo_path / ".circleci",
            repo_path / ".travis.yml",
            repo_path / "azure-pipelines.yml",
            repo_path / "Jenkinsfile",
        ]
        if not any(p.exists() for p in ci_dirs):
            findings.append(Finding(
                severity="high",
                category="ci_missing",
                message="No CI/CD configuration found",
                file_path=None,
                line=None,
            ))

        license_files = list(repo_path.glob("LICENSE*")) + list(repo_path.glob("license*"))
        if not license_files:
            findings.append(Finding(
                severity="medium",
                category="license_missing",
                message="No LICENSE file found",
                file_path=None,
                line=None,
            ))

        dockerfiles = list(repo_path.glob("Dockerfile*"))
        if not dockerfiles:
            findings.append(Finding(
                severity="low",
                category="dockerfile_missing",
                message="No Dockerfile found",
                file_path=None,
                line=None,
            ))

        base_score = 55
        score = max(0, base_score - len(findings) * 5)

        return PillarResult(
            name=self.name,
            status="partial",
            tier=2,
            score=score,
            summary="Tier-2 (best-effort) analysis: basic structural checks only. No Tier-1 language detected.",
            findings=findings,
        )

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
        print("Usage: python -m app.pillars.production_readiness --path /path/to/repo [--timeout 60]")
        sys.exit(1)
    path_idx = sys.argv.index("--path") if "--path" in sys.argv else -1
    timeout_idx = sys.argv.index("--timeout") if "--timeout" in sys.argv else -1
    repo_path = sys.argv[path_idx + 1] if path_idx != -1 and path_idx + 1 < len(sys.argv) else sys.argv[1]
    timeout_s = int(sys.argv[timeout_idx + 1]) if timeout_idx != -1 and timeout_idx + 1 < len(sys.argv) else 60
    run_pillar_cli(ProductionReadinessPillar, repo_path, timeout_s)