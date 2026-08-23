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