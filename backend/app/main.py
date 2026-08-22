from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import os
import subprocess

app = FastAPI(title="RepoAudit")

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health():
    return {"status": "ok"}


# TEMPORARY: Phase 0 debug endpoint — remove after Phase 0 DoD confirmed on Render
# Not part of the documented API contract in 02_ARCHITECTURE.md
@app.get("/_debug/toolchain")
async def debug_toolchain():
    tools = [
        ["ruff", "--version"],
        ["radon", "--version"],
        ["bandit", "--version"],
        ["semgrep", "--version"],
        ["node", "--version"],
        ["npm", "--version"],
        ["trivy", "--version"],
        ["cloc", "--version"],
    ]
    results = {}
    for cmd in tools:
        tool_name = cmd[0]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            results[tool_name] = {
                "exit_code": result.returncode,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip() if result.stderr else "",
            }
        except subprocess.TimeoutExpired:
            results[tool_name] = {"exit_code": -1, "stdout": "", "stderr": "timeout"}
        except Exception as e:
            results[tool_name] = {"exit_code": -1, "stdout": "", "stderr": str(e)}
    return results


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)