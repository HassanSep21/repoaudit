from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import os
import asyncio
import json
from datetime import datetime
from typing import Optional
from collections import defaultdict

from app.db.session import init_db, get_db
from app.models.schema import Repo, AnalysisRun, PillarResult, Finding
from app.pipeline.orchestrator import run_analysis_pipeline
from app.pipeline.repo_fetcher import precheck_repo, ArchiveFileError


def generate_plain_summary(pillar_name: str, score: Optional[int], findings: list) -> str:
    """Generate a plain-language summary for a pillar."""
    if not findings:
        return "No issues found — this pillar passed cleanly."
    
    counts = defaultdict(int)
    for f in findings:
        counts[f.get("severity", "info")] += 1
    
    parts = []
    if counts.get("high", 0) > 0:
        parts.append(f"{counts['high']} high-severity issue{'s' if counts['high'] > 1 else ''}")
    if counts.get("medium", 0) > 0:
        parts.append(f"{counts['medium']} medium-severity issue{'s' if counts['medium'] > 1 else ''}")
    if counts.get("low", 0) > 0:
        parts.append(f"{counts['low']} low-severity issue{'s' if counts['low'] > 1 else ''}")
    if counts.get("info", 0) > 0:
        parts.append(f"{counts['info']} informational item{'s' if counts['info'] > 1 else ''}")
    
    score_text = f" (score: {score}/100)" if score is not None else ""
    pillar_lower = pillar_name.lower()
    
    if pillar_name.lower() == "code evaluation":
        return f"Code quality{score_text}: {', '.join(parts)}. Main issues are complexity and style inconsistencies."
    elif pillar_name.lower() == "security":
        return f"Security posture{score_text}: {', '.join(parts)}. Review findings for potential vulnerabilities."
    elif pillar_name.lower() == "documentation":
        return f"Documentation quality{score_text}: {', '.join(parts)}. Consider adding more inline docs and API documentation."
    elif pillar_name.lower() == "production readiness":
        return f"Production readiness{score_text}: {', '.join(parts)}. Consider adding CI/CD, health checks, and error handling."
    elif pillar_name.lower() == "semantic analysis":
        return f"Architecture review{score_text}: {', '.join(parts)}."
    return f"Found {', '.join(parts)}.{score_text}"


def group_findings(findings: list) -> list:
    """Group findings by message, returning list of groups with count and file locations."""
    groups = defaultdict(lambda: {"message": "", "severity": "", "category": "", "count": 0, "files": []})
    for f in findings:
        key = f.get("message", "")
        group = groups[key]
        if not group["message"]:
            group["message"] = f.get("message", "")
            group["severity"] = f.get("severity", "info")
            group["category"] = f.get("category", "")
        group["count"] += 1
        group["files"].append({
            "path": f.get("file_path", "unknown"),
            "line": f.get("line")
        })
    
    # Sort by severity: high > medium > low > info
    severity_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    return sorted(groups.values(), key=lambda g: severity_order.get(g["severity"], 4))

app = FastAPI(title="RepoAudit")

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

init_db()

# In-process lock for D16: only one AnalysisRun in flight at a time
_analysis_lock = asyncio.Lock()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health():
    return {"status": "ok"}


# D15: Async job pattern
@app.post("/analyze")
async def start_analysis(request: Request):
    body = await request.json()
    url = body.get("url")
    confirm = body.get("confirm", False)
    
    if not url:
        raise HTTPException(status_code=400, detail="Missing 'url' in request body")
    
    # Validate URL against allow-list (Rule 17)
    import re
    if not re.match(r"^https://github\.com/[^/]+/[^/]+(\.git)?$", url):
        raise HTTPException(
            status_code=400, 
            detail="Please enter a valid public GitHub repository URL (e.g., https://github.com/owner/repo)"
        )
    
    # Archive file check (D20) - before lock to avoid holding lock on API call
    try:
        precheck_repo(url, confirm=confirm)
    except ArchiveFileError as e:
        raise HTTPException(
            status_code=409, 
            detail={
                "reason": "contains_archive_files", 
                "files": e.files,
                "message": str(e)
            }
        )
    except Exception:
        # Other fetch errors (size, not found, etc.) will be caught in background task
        pass
    
    # Concurrency lock (D16) - acquire synchronously for atomic 429
    if _analysis_lock.locked():
        raise HTTPException(
            status_code=429, 
            detail="Another analysis is currently running. RepoAudit processes one repository at a time — please wait a moment and try again."
        )
    
    # Acquire lock for the entire pipeline
    await _analysis_lock.acquire()
    
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
            
            # Start async pipeline task with lock release callback
            task = asyncio.create_task(run_analysis_pipeline(run.id, url_clean, confirm))
            task.add_done_callback(lambda t: _analysis_lock.release() if _analysis_lock.locked() else None)
            
            return {"run_id": run.id}
    except Exception as e:
        # Release lock if scheduling failed
        _analysis_lock.release()
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


# D8: Export endpoints
@app.get("/analysis/{run_id}/export.json")
async def export_json(run_id: int):
    with get_db() as db:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Analysis run not found")
        
        # Get repo
        repo = db.query(Repo).filter(Repo.id == run.repo_id).first()
        
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
        
        export_data = {
            "run": {
                "id": run.id,
                "repo_id": run.repo_id,
                "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                "overall_score": run.overall_score,
                "overall_verdict": run.overall_verdict,
                "pillars_completed": run.pillars_completed,
            },
            "repo": {
                "url": repo.url if repo else None,
                "owner": repo.owner if repo else None,
                "name": repo.name if repo else None,
                "default_branch": repo.default_branch if repo else None,
                "primary_languages": repo.primary_languages if repo else None,
                "size_kb": repo.size_kb if repo else None,
            },
            "pillars": pillars_data,
            "exported_at": datetime.utcnow().isoformat(),
        }
        
        return JSONResponse(
            content=export_data,
            headers={"Content-Disposition": f"attachment; filename=repoaudit-{run_id}.json"}
        )


@app.get("/analysis/{run_id}/export.html")
async def export_html(run_id: int, request: Request):
    with get_db() as db:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Analysis run not found")
        
        # Get repo
        repo = db.query(Repo).filter(Repo.id == run.repo_id).first()
        
        # Get pillar results with grouped findings and plain summaries
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
            grouped = group_findings(findings)
            plain_summary = generate_plain_summary(pr.pillar_name, pr.score, findings)
            pillars_data.append({
                "name": pr.pillar_name,
                "status": pr.status,
                "tier": pr.tier,
                "score": pr.score,
                "summary": pr.summary,
                "plain_summary": plain_summary,
                "findings": findings,
                "grouped_findings": grouped,
            })
        
        return templates.TemplateResponse(
            "export.html",
            {
                "request": request,
                "run": run,
                "repo": repo,
                "pillars": pillars_data,
            },
            headers={"Content-Disposition": f"attachment; filename=repoaudit-{run_id}.html"}
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)