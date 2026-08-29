from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List


@dataclass
class Finding:
    severity: str  # info, low, medium, high
    category: str
    message: str
    file_path: Optional[str] = None
    line: Optional[int] = None


@dataclass
class PillarResult:
    name: str
    status: str  # complete, partial, failed
    tier: int
    score: Optional[int]
    summary: str
    findings: List[Finding] = field(default_factory=list)


class Pillar(ABC):
    name: str
    
    @abstractmethod
    def run(self, repo_path: Path, *, timeout_s: int) -> PillarResult:
        ...


def normalize_file_paths(findings: List[Finding], repo_path: Path) -> List[Finding]:
    """
    Convert absolute file paths to paths relative to the repo root.
    This strips the temp directory prefix so reports show clean repo-relative paths.
    """
    repo_path = repo_path.resolve()
    normalized = []
    for finding in findings:
        file_path = finding.file_path
        if file_path is None:
            normalized.append(finding)
            continue
        try:
            abs_path = Path(file_path).resolve()
            # Only strip if path is under repo_path
            if abs_path.is_relative_to(repo_path):
                rel_path = abs_path.relative_to(repo_path)
                normalized.append(Finding(
                    severity=finding.severity,
                    category=finding.category,
                    message=finding.message,
                    file_path=str(rel_path),
                    line=finding.line,
                ))
            else:
                normalized.append(finding)
        except Exception:
            # If any error, keep original
            normalized.append(finding)
    return normalized


class Pillar(ABC):
    name: str
    
    @abstractmethod
    def run(self, repo_path: Path, *, timeout_s: int) -> PillarResult:
        ...


def run_pillar_cli(pillar_class, repo_path: str, timeout_s: int = 60):
    """CLI entry point for running a pillar standalone during development."""
    import json
    pillar = pillar_class()
    result = pillar.run(Path(repo_path), timeout_s=timeout_s)
    output = {
        "name": result.name,
        "status": result.status,
        "tier": result.tier,
        "score": result.score,
        "summary": result.summary,
        "findings": [
            {
                "severity": f.severity,
                "category": f.category,
                "message": f.message,
                "file_path": f.file_path,
                "line": f.line,
            }
            for f in result.findings
        ],
    }
    print(json.dumps(output, indent=2))