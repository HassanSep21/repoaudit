from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import os
import uuid
import time
import threading
from datetime import datetime
from typing import Optional

from app.db.session import init_db, get_db
from app.models.schema import Repo, AnalysisRun, PillarResult, Finding

app = FastAPI(title="RepoAudit")

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

init_db()

# In-process lock for D16: only one AnalysisRun in flight at a time
_analysis_lock = threading.Lock()
_current_run_id: Optional[int] = None


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health():
    return {"status": "ok"}


# D15: Async job pattern
@app.post("/analyze")
async def start_analysis(background_tasks: BackgroundTasks, request: Request):
    global _current_run_id
    
    body = await request.json()
    url = body.get("url")
    confirm = body.get("confirm", False)
    
    if not url:
        raise HTTPException(status_code=400, detail="Missing 'url' in request body")
    
    # Validate URL against allow-list (Rule 17)
    import re
    if not re.match(r"^https://github\.com/[^/]+/[^/]+(\.git)?$", url):
        raise HTTPException(status_code=400, detail="Invalid GitHub URL. Must be https://github.com/owner/repo")
    
    # Concurrency lock (D16)
    if not _analysis_lock.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="Another analysis is in progress. Try again shortly.")
    
    try:
        with get_db() as db:
            # Parse owner/repo from URL
            url_clean = url.rstrip(".git")
            parts = url_clean.split("/")
            owner = parts[-2]
            name = parts[-1]
            
            # Create or get repo
            repo = db.query(Repo).filter(Repo.url == url_clean).first()
            if not repo:
                repo = Repo(
                    url=url_clean,
                    owner=owner,
                    name=name,
                    default_branch="main",
                    primary_languages="Unknown",
                    size_kb=0,
                )
                db.add(repo)
                db.commit()
                db.refresh(repo)
            
            # Create analysis run
            run = AnalysisRun(
                repo_id=repo.id,
                status="running",
                overall_score=None,
                overall_verdict=None,
                pillars_completed="0/5",
            )
            db.add(run)
            db.commit()
            db.refresh(run)
            
            _current_run_id = run.id
            
            # Start background task
            background_tasks.add_task(run_fake_analysis, run.id)
            
            return {"run_id": run.id}
    except Exception as e:
        _analysis_lock.release()
        _current_run_id = None
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/analysis/{run_id}")
async def get_analysis(run_id: int):
    with get_db() as db:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Analysis run not found")
        
        # Get pillar results
        pillars_data = []
        for pr in run.pillar_results:
            findings = [
                {
                    "severity": f.severity,
                    "category": f.category,
                    "message": f.message,
                    "file_path": f.file_path,
                    "line": f.line,
                }
                for f in pr.findings
            ]
            pillars_data.append({
                "name": pr.pillar_name,
                "status": pr.status,
                "tier": pr.tier,
                "score": pr.score,
                "summary": pr.summary,
                "findings": findings,
            })
        
        return {
            "status": run.status,
            "overall_score": run.overall_score,
            "overall_verdict": run.overall_verdict,
            "pillars_completed": run.pillars_completed,
            "pillars": pillars_data,
        }


# TEMPORARY: Phase 1 resource ceiling check — remove after Phase 1 DoD
@app.get("/_debug/resource-test")
async def resource_test():
    import subprocess
    import tempfile
    import os
    import json
    
    results = {}
    
    # Create a small test repo
    with tempfile.TemporaryDirectory() as tmpdir:
        test_repo = os.path.join(tmpdir, "test-repo")
        os.makedirs(test_repo)
        
        # Write a simple Python file
        with open(os.path.join(test_repo, "main.py"), "w") as f:
            f.write("def hello():\n    print('hello')\n\nhello()\n")
        
        # Write a simple package.json for npm audit
        with open(os.path.join(test_repo, "package.json"), "w") as f:
            json.dump({"name": "test", "version": "1.0.0", "dependencies": {}}, f)
        
        # Test Trivy (filesystem scan)
        try:
            import time
            start = time.time()
            result = subprocess.run(
                ["trivy", "fs", "--format", "json", test_repo],
                capture_output=True, text=True, timeout=120
            )
            elapsed = time.time() - start
            results["trivy"] = {
                "exit_code": result.returncode,
                "elapsed_seconds": round(elapsed, 2),
                "stdout_len": len(result.stdout),
                "stderr": result.stderr[:200] if result.stderr else "",
            }
        except subprocess.TimeoutExpired:
            results["trivy"] = {"exit_code": -1, "elapsed_seconds": 120, "error": "timeout"}
        except Exception as e:
            results["trivy"] = {"exit_code": -1, "error": str(e)}
        
        # Test Semgrep
        try:
            start = time.time()
            result = subprocess.run(
                ["semgrep", "--config=auto", "--json", test_repo],
                capture_output=True, text=True, timeout=120
            )
            elapsed = time.time() - start
            results["semgrep"] = {
                "exit_code": result.returncode,
                "elapsed_seconds": round(elapsed, 2),
                "stdout_len": len(result.stdout),
                "stderr": result.stderr[:200] if result.stderr else "",
            }
        except subprocess.TimeoutExpired:
            results["semgrep"] = {"exit_code": -1, "elapsed_seconds": 120, "error": "timeout"}
        except Exception as e:
            results["semgrep"] = {"exit_code": -1, "error": str(e)}
    
    return results


def run_fake_analysis(run_id: int):
    """Run 5 fake pillars sequentially (D16), each sleeping briefly and writing a dummy result."""
    global _current_run_id
    pillars = [
        ("Code Evaluation", 1, 85, "Tier-1 static analysis complete"),
        ("Security", 1, 90, "Tier-1 security scan complete"),
        ("Documentation", 1, 75, "Tier-1 docs check complete"),
        ("Production Readiness", 1, 80, "Tier-1 prod-readiness check complete"),
        ("Semantic Analysis", 1, 88, "LLM semantic analysis complete"),
    ]
    try:
        for i, (name, tier, score, summary) in enumerate(pillars, 1):
            time.sleep(3)  # Short sleep per pillar
            
            with get_db() as db:
                run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
                if not run:
                    return
                
                # Create pillar result
                pillar = PillarResult(
                    run_id=run_id,
                    pillar_name=name,
                    status="complete",
                    score=score,
                    tier=tier,
                    summary=summary,
                )
                db.add(pillar)
                db.commit()
                db.refresh(pillar)
                
                # Add a fake finding
                finding = Finding(
                    pillar_result_id=pillar.id,
                    severity="info",
                    category="test",
                    message=f"Fake finding from {name}",
                    file_path=None,
                    line=None,
                )
                db.add(finding)
                
                # Update run progress
                run.pillars_completed = f"{i}/5"
                db.commit()
        
        # All pillars done - finalize run
        with get_db() as db:
            run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
            if not run:
                return
            
            # Average score across all pillars
            avg_score = sum(p[2] for p in pillars) // len(pillars)
            run.status = "complete"
            run.overall_score = avg_score
            run.overall_verdict = "Production Ready" if avg_score >= 80 else "Needs Work"
            run.pillars_completed = "5/5"
            run.completed_at = datetime.utcnow()
            db.commit()
            
    except Exception as e:
        print(f"[run_fake_analysis] ERROR for run {run_id}: {e}")
        import traceback
        traceback.print_exc()
        # Mark run as failed so it doesn't stay "running" forever
        try:
            with get_db() as db:
                run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
                if run:
                    run.status = "failed"
                    run.pillars_completed = "0/5"
                    run.completed_at = datetime.utcnow()
                    db.commit()
        except Exception as e2:
            print(f"[run_fake_analysis] Failed to mark run as failed: {e2}")
    finally:
        _analysis_lock.release()
        _current_run_id = None


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)