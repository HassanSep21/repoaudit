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
    """Main orchestrator - runs pillars sequentially (D16).
    
    Lock is already held by caller (main.py start_analysis). 
    This function releases it in finally.
    """
    from app.pillars.code_evaluation import CodeEvaluationPillar
    from app.pipeline.repo_fetcher import fetch_repo
    
    # Import lock from main
    from app.main import _analysis_lock, _current_run_id
    
    temp_dir = None
    try:
        _current_run_id = run_id
        
        # Phase 1: Fetch repo with size/archive checks (D19, D20)
        temp_dir = fetch_repo(repo_url)
        
        # Phase 2: Run pillars sequentially
        pillars = [
            CodeEvaluationPillar(),
            # TODO: Add SecurityPillar, DocumentationPillar, ProductionReadinessPillar, SemanticAnalysisPillar
        ]
        
        total_pillars = len(pillars)
        completed = 0
        scores = []
        
        for pillar in pillars:
            # Update run status to show which pillar is running
            with get_db() as db:
                run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
                if run:
                    run.pillars_completed = f"{completed}/{total_pillars}"
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
                run.pillars_completed = f"{completed}/{total_pillars}"
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
            run.pillars_completed = f"{total_pillars}/{total_pillars}"
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
                    run.pillars_completed = f"0/{total_pillars}"
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
        # Release lock (acquired by caller in main.py)
        _analysis_lock.release()
        _current_run_id = None