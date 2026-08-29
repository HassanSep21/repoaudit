import os
from pathlib import Path
from typing import List, Optional

from app.pillars.base import Pillar, PillarResult, Finding, normalize_file_paths


class DocumentationPillar(Pillar):
    name = "Documentation"

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

        findings.extend(self._check_readme(repo_path))
        findings.extend(self._check_setup_instructions(repo_path))
        findings.extend(self._check_comment_density(repo_path, python_files, js_ts_files))
        findings.extend(self._check_api_docs(repo_path, python_files, js_ts_files))

        # Normalize file paths to be relative to repo root
        findings = normalize_file_paths(findings, repo_path)

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

    def _check_readme(self, repo_path: Path) -> List[Finding]:
        findings = []
        readme_files = list(repo_path.glob("README*")) + list(repo_path.glob("readme*"))
        
        if not readme_files:
            findings.append(Finding(
                severity="high",
                category="readme_missing",
                message="No README file found in repository root",
                file_path=None,
                line=None,
            ))
            return findings

        readme_path = readme_files[0]
        try:
            content = readme_path.read_text(encoding="utf-8", errors="replace")
            word_count = len(content.split())
            
            if word_count < 100:
                findings.append(Finding(
                    severity="medium",
                    category="readme_short",
                    message=f"README is very short ({word_count} words) - consider adding more detail",
                    file_path=str(readme_path.relative_to(repo_path)),
                    line=None,
                ))
            elif word_count < 300:
                findings.append(Finding(
                    severity="low",
                    category="readme_short",
                    message=f"README is brief ({word_count} words) - could be expanded",
                    file_path=str(readme_path.relative_to(repo_path)),
                    line=None,
                ))
        except Exception:
            pass

        return findings

    def _check_setup_instructions(self, repo_path: Path) -> List[Finding]:
        findings = []
        readme_files = list(repo_path.glob("README*")) + list(repo_path.glob("readme*"))
        
        if not readme_files:
            return findings

        readme_path = readme_files[0]
        try:
            content = readme_path.read_text(encoding="utf-8", errors="replace").lower()
            
            setup_keywords = ["install", "setup", "getting started", "quick start", "requirements", "dependencies", "pip install", "npm install", "yarn install", "poetry install"]
            has_setup = any(keyword in content for keyword in setup_keywords)
            
            if not has_setup:
                findings.append(Finding(
                    severity="medium",
                    category="setup_missing",
                    message="README lacks clear setup/installation instructions",
                    file_path=str(readme_path.relative_to(repo_path)),
                    line=None,
                ))
        except Exception:
            pass

        return findings

    def _check_comment_density(self, repo_path: Path, python_files: List[Path], js_ts_files: List[Path]) -> List[Finding]:
        findings = []
        all_files = python_files + js_ts_files
        
        if not all_files:
            return findings

        total_lines = 0
        comment_lines = 0
        files_with_no_comments = 0

        for file_path in all_files[:100]:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines()
                total_lines += len(lines)
                
                file_comment_lines = 0
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("/*"):
                        file_comment_lines += 1
                    elif stripped.startswith("#!") or stripped.startswith("# -*-"):
                        file_comment_lines += 1
                
                comment_lines += file_comment_lines
                if file_comment_lines == 0 and len(lines) > 20:
                    files_with_no_comments += 1
            except Exception:
                pass

        if total_lines > 0:
            ratio = comment_lines / total_lines
            if ratio < 0.05:
                findings.append(Finding(
                    severity="medium",
                    category="low_comment_density",
                    message=f"Very low comment density ({ratio:.1%}) - consider adding inline documentation",
                    file_path=None,
                    line=None,
                ))
            elif ratio < 0.15:
                findings.append(Finding(
                    severity="low",
                    category="low_comment_density",
                    message=f"Low comment density ({ratio:.1%}) - inline documentation could be improved",
                    file_path=None,
                    line=None,
                ))

        if files_with_no_comments > 5:
            findings.append(Finding(
                severity="low",
                category="files_without_comments",
                message=f"{files_with_no_comments} files with >20 lines have no comments",
                file_path=None,
                line=None,
            ))

        return findings

    def _check_api_docs(self, repo_path: Path, python_files: List[Path], js_ts_files: List[Path]) -> List[Finding]:
        findings = []
        all_files = python_files + js_ts_files
        
        if not all_files:
            return findings

        public_functions = 0
        documented_functions = 0

        for file_path in all_files[:100]:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                
                if file_path.suffix == ".py":
                    import re
                    func_matches = re.findall(r'^\s*def\s+(\w+)\s*\([^)]*\):', content, re.MULTILINE)
                    for func_name in func_matches:
                        if not func_name.startswith("_"):
                            public_functions += 1
                            func_pattern = rf'^\s*def\s+{func_name}\s*\([^)]*\):'
                            match = re.search(func_pattern, content, re.MULTILINE)
                            if match:
                                after_func = content[match.end():match.end()+200]
                                if '"""' in after_func[:50] or "'''" in after_func[:50]:
                                    documented_functions += 1
                
                elif file_path.suffix in [".js", ".ts", ".jsx", ".tsx"]:
                    import re
                    func_matches = re.findall(r'(?:export\s+)?(?:async\s+)?function\s+(\w+)|const\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>', content)
                    for match in func_matches:
                        func_name = match[0] or match[1]
                        if func_name and not func_name.startswith("_"):
                            public_functions += 1
                            if "/**" in content[:content.find(func_name)]:
                                documented_functions += 1
            except Exception:
                pass

        if public_functions > 10 and documented_functions == 0:
            findings.append(Finding(
                severity="medium",
                category="no_api_docs",
                message=f"No API documentation found for {public_functions} public functions/methods",
                file_path=None,
                line=None,
            ))
        elif public_functions > 10 and documented_functions / public_functions < 0.3:
            findings.append(Finding(
                severity="low",
                category="low_api_doc_coverage",
                message=f"Low API documentation coverage ({documented_functions}/{public_functions} functions documented)",
                file_path=None,
                line=None,
            ))

        return findings

    def _run_tier2_generic(self, repo_path: Path, timeout_s: int) -> PillarResult:
        findings = []
        
        readme_files = list(repo_path.glob("README*")) + list(repo_path.glob("readme*"))
        if not readme_files:
            findings.append(Finding(
                severity="high",
                category="readme_missing",
                message="No README file found in repository root",
                file_path=None,
                line=None,
            ))
        else:
            try:
                content = readme_files[0].read_text(encoding="utf-8", errors="replace")
                if len(content.split()) < 100:
                    findings.append(Finding(
                        severity="medium",
                        category="readme_short",
                        message="README is very brief",
                        file_path=str(readme_files[0].relative_to(repo_path)),
                        line=None,
                    ))
            except Exception:
                pass

        base_score = 65
        score = max(0, base_score - len(findings) * 5)

        return PillarResult(
            name=self.name,
            status="partial",
            tier=2,
            score=score,
            summary="Tier-2 (best-effort) analysis: basic README checks only. No Tier-1 language detected.",
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
        print("Usage: python -m app.pillars.documentation --path /path/to/repo [--timeout 60]")
        sys.exit(1)
    path_idx = sys.argv.index("--path") if "--path" in sys.argv else -1
    timeout_idx = sys.argv.index("--timeout") if "--timeout" in sys.argv else -1
    repo_path = sys.argv[path_idx + 1] if path_idx != -1 and path_idx + 1 < len(sys.argv) else sys.argv[1]
    timeout_s = int(sys.argv[timeout_idx + 1]) if timeout_idx != -1 and timeout_idx + 1 < len(sys.argv) else 60
    run_pillar_cli(DocumentationPillar, repo_path, timeout_s)