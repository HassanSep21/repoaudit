from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import os

from app.db.session import init_db, get_db
from app.models.schema import Repo, AnalysisRun, PillarResult, Finding

app = FastAPI(title="RepoAudit")

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

init_db()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/_debug/storage-test")
async def storage_test():
    with get_db() as db:
        repo = Repo(
            url="https://github.com/test/storage-check",
            owner="test",
            name="storage-check",
            default_branch="main",
            primary_languages="Python",
            size_kb=100,
        )
        db.add(repo)
        db.commit()
        db.refresh(repo)

        run = AnalysisRun(
            repo_id=repo.id,
            status="complete",
            overall_score=85,
            overall_verdict="Production Ready",
            pillars_completed="5/5",
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        return {
            "repo_id": repo.id,
            "run_id": run.id,
            "message": "Storage test write successful",
        }


@app.get("/_debug/storage-test")
async def storage_test_read():
    with get_db() as db:
        repo = db.query(Repo).filter(Repo.url == "https://github.com/test/storage-check").first()
        if not repo:
            return {"found": False, "message": "Test repo not found"}

        run = db.query(AnalysisRun).filter(AnalysisRun.repo_id == repo.id).first()
        return {
            "found": True,
            "repo_id": repo.id,
            "run_id": run.id if run else None,
            "overall_score": run.overall_score if run else None,
            "overall_verdict": run.overall_verdict if run else None,
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)