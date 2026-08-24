from pathlib import Path
import tempfile
import shutil
import subprocess
import time
from datetime import datetime
from typing import Optional

from app.db.session import get_db
from app.models.schema import AnalysisRun, PillarResult, Finding


def run_analysis_pipeline(run_id: int, repo_url: str):
    """Main orchestrator - runs pillars sequentially (D16)."""
    from app.pillars.code_evaluation import CodeEvaluationPillar
    from app.pipeline.repo_fetcher import fetch_repo
    
    # Import lock from main
    from app.main import _analysis_lock, _current_run_id
    
    lock_acquired = False
    temp_dir = None
    try:
        # Acquire lock at start of pipeline (D16)
        if not _analysis_lock.acquire(blocking=False):
            raise RuntimeError("Another analysis is in progress")
        lock_acquired = True
        _current_run_id = run_id
        
        # Phase 1: Fetch repo with size/archive checks (D19, D20)
        temp_dir = fetch_repo(repo_url)
        
        # Phase 2: Run pillars sequentially
        pillars = [
            CodeEvaluationPillar(),
            # TODO: Add SecurityPillar, DocumentationPillar, ProductionReadinessPillar, SemanticAnalysisPillar
        ]
        
        completed = 0
        scores = []
        
        for pillar in pillars:
            # Update run status to show which pillar is running
            with get_db() as db:
                run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
                if run:
                    run.pillars_completed = f"{completed}/5"
                    db.commit()
            
            # Run pillar with 60s timeout (D9) - pass Path object
            result = pillar.run(Path(temp_dir), timeout_s=60)
            
            # Persist pillar result
            with get_db() as db:
                run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
                if not run:
                    return
                
                pillar_result = PillarResult(
                    run_id=run_id,
                    pillar_name=pillar.name,
                    status=result.status,
                    score=result.score,
                    tier=result.tier,
                    summary=result.summary,
                )
                db.add(pillar_result)
                db.commit()
                db.refresh(pillar_result)
                
                # Add findings
                for finding in result.findings:
                    db_finding = Finding(
                        pillar_result_id=pillar_result.id,
                        severity=finding.severity,
                        category=finding.category,
                        message=finding.message,
                        file_path=finding.file_path,
                        line=finding.line,
                    )
                    db.add(db_finding)
                
                # Update progress
                completed += 1
                run.pillars_completed = f"{completed}/5"
                if result.score is not None:
                    scores.append(result.score)
                db.commit()
        
        # Finalize run
        with get_db() as db:
            run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
            if not run:
                return
            
            run.status = "complete"
            run.overall_score = sum(scores) // len(scores) if scores else 0
            if run.overall_score >= 80:
                run.overall_verdict = "Production Ready"
            elif run.overall_score >= 50:
                run.overall_verdict = "Needs Work"
            else:
                run.overall_verdict = "Not Ready"
            run.pillars_completed = "5/5"
            run.completed_at = datetime.utcnow()
            db.commit()
            
    except Exception as e:
        print(f"[run_analysis_pipeline] ERROR for run {run_id}: {e}")
        import traceback
        traceback.print_exc()
        # Mark run as failed
        try:
            with get_db() as db:
                run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
                if run:
                    run.status = "failed"
                    run.pillars_completed = "0/5"
                    run.completed_at = datetime.utcnow()
                    db.commit()
        except Exception as e2:
            print(f"[run_analysis_pipeline] Failed to mark run as failed: {e2}")
    finally:
        # Cleanup temp directory (Rule 19)
        if temp_dir:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
        # Release lock ONLY if we acquired it
        if lock_acquired:
            _analysis_lock.release()
            _current_run_id = None