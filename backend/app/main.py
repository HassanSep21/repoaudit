from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import os
import uuid
import time
import threading
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


def run_fake_analysis(run_id: int):
    """Fake no-op pillar that sleeps 10s then returns a dummy result."""
    global _current_run_id
    try:
        time.sleep(10)
        
        with get_db() as db:
            run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
            if not run:
                return
            
            # Create a fake pillar result
            pillar = PillarResult(
                run_id=run_id,
                pillar_name="Code Evaluation",
                status="complete",
                score=85,
                tier=1,
                summary="Fake analysis complete — no-op pillar for Phase 1 validation",
            )
            db.add(pillar)
            db.commit()
            db.refresh(pillar)
            
            # Add a fake finding
            finding = Finding(
                pillar_result_id=pillar.id,
                severity="info",
                category="test",
                message="This is a fake finding from the no-op pillar",
                file_path=None,
                line=None,
            )
            db.add(finding)
            
            # Update run
            run.status = "complete"
            run.overall_score = 85
            run.overall_verdict = "Production Ready"
            run.pillars_completed = "1/5"
            run.completed_at = time.time()
            
            db.commit()
    finally:
        _analysis_lock.release()
        _current_run_id = None


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)