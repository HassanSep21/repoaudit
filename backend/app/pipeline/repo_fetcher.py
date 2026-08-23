import tempfile
import subprocess
import requests
from pathlib import Path
from typing import Optional
from app.models.schema import Repo


GITHUB_API_BASE = "https://api.github.com"
MAX_REPO_SIZE_KB = 500 * 1024  # 500MB
MAX_ARCHIVE_SIZE_KB = 5 * 1024  # 5MB
ARCHIVE_EXTENSIONS = {".zip", ".tar.gz", ".tgz", ".7z", ".rar", ".tar.bz2", ".tar.xz"}


def fetch_repo(repo_url: str) -> str:
    """
    Fetch repo with size/archive checks (D19, D20).
    Returns path to temp directory containing cloned repo.
    """
    # Parse owner/repo from URL
    url_clean = repo_url.rstrip(".git")
    parts = url_clean.split("/")
    owner = parts[-2]
    name = parts[-1]
    
    # Check repo size via GitHub API (D19)
    headers = {"Accept": "application/vnd.github.v3+json"}
    import os
    github_token = os.getenv("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"token {github_token}"
    
    repo_resp = requests.get(f"{GITHUB_API_BASE}/repos/{owner}/{name}", headers=headers, timeout=10)
    if repo_resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch repo metadata: {repo_resp.status_code}")
    
    repo_data = repo_resp.json()
    size_kb = repo_data.get("size", 0)
    
    if size_kb > MAX_REPO_SIZE_KB:
        raise RuntimeError(f"Repository too large ({size_kb} KB > {MAX_REPO_SIZE_KB} KB limit)")
    
    # Check for large archive files (D20)
    contents_resp = requests.get(f"{GITHUB_API_BASE}/repos/{owner}/{name}/contents", headers=headers, timeout=10)
    if contents_resp.status_code == 200:
        for item in contents_resp.json():
            if item.get("size", 0) > MAX_ARCHIVE_SIZE_KB:
                for ext in ARCHIVE_EXTENSIONS:
                    if item["name"].endswith(ext):
                        raise RuntimeError(f"Large archive file detected: {item['name']} ({item['size']} KB). Use confirm=true to proceed anyway.")
    
    # Clone with depth=1 and blob limit (D19)
    temp_dir = tempfile.mkdtemp(prefix="repoaudit-")
    clone_cmd = [
        "git", "clone", "--depth", "1", "--filter=blob:limit=10m",
        repo_url, temp_dir
    ]
    result = subprocess.run(clone_cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError(f"Git clone failed: {result.stderr}")
    
    return temp_dir