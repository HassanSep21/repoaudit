import tempfile
import subprocess
import requests
from pathlib import Path
from typing import Optional
from app.models.schema import Repo


GITHUB_API_BASE = "https://api.github.com"
MAX_REPO_SIZE_KB = 500 * 1024  # 500MB
MAX_ARCHIVE_SIZE_KB = 5 * 1024  # 5MB

# User-friendly error messages
SIZE_LIMIT_ERROR = (
    "This repository is too large to analyze (over 500 MB). "
    "RepoAudit has a size limit to ensure analyses complete within a reasonable time. "
    "Try a smaller repository or a specific subdirectory."
)
ARCHIVE_WARNING_ERROR = (
    "This repository contains large archive files that could be zip bombs (compressed files "
    "that expand to enormous sizes). Analyzing them could exceed resource limits. "
    "If you trust this repository, you can continue anyway."
)
ARCHIVE_EXTENSIONS = {".zip", ".tar.gz", ".tgz", ".7z", ".rar", ".tar.bz2", ".tar.xz"}


class ArchiveFileError(Exception):
    """Raised when repo contains large archive files and confirm is not true."""
    def __init__(self, files):
        self.files = files
        super().__init__(ARCHIVE_WARNING_ERROR)


def fetch_repo(repo_url: str, confirm: bool = False) -> str:
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
        raise RuntimeError(SIZE_LIMIT_ERROR)
    
    # Check for large archive files (D20) - use recursive tree listing
    default_branch = repo_data.get("default_branch", "main")
    tree_resp = requests.get(
        f"{GITHUB_API_BASE}/repos/{owner}/{name}/git/trees/{default_branch}?recursive=1",
        headers=headers, timeout=10
    )
    if tree_resp.status_code == 409:
        # Empty repo - GitHub returns 409 for empty repos
        tree_data = []
    elif tree_resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch repo tree: {tree_resp.status_code}")
    else:
        tree_data = tree_resp.json().get("tree", [])
    
    archive_files = []
    for item in tree_data:
        if item.get("type") == "blob" and item.get("size", 0) > MAX_ARCHIVE_SIZE_KB:
            for ext in ARCHIVE_EXTENSIONS:
                if item["path"].endswith(ext):
                    archive_files.append(f"{item['path']} ({item['size']} KB)")
                    break
    
    if archive_files and not confirm:
        raise ArchiveFileError(archive_files)
    
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


def precheck_repo(repo_url: str, confirm: bool = False) -> None:
    """
    Pre-check repo size and archive files via GitHub API only (no clone).
    Raises ArchiveFileError if large archives found and not confirmed.
    Raises RuntimeError for other errors (size limit, not found, etc.).
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
        raise RuntimeError(SIZE_LIMIT_ERROR)
    
    # Check for large archive files (D20) - use recursive tree listing
    default_branch = repo_data.get("default_branch", "main")
    tree_resp = requests.get(
        f"{GITHUB_API_BASE}/repos/{owner}/{name}/git/trees/{default_branch}?recursive=1",
        headers=headers, timeout=10
    )
    if tree_resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch repo tree: {tree_resp.status_code}")
    
    archive_files = []
    for item in tree_resp.json().get("tree", []):
        if item.get("type") == "blob" and item.get("size", 0) > MAX_ARCHIVE_SIZE_KB:
            for ext in ARCHIVE_EXTENSIONS:
                if item["path"].endswith(ext):
                    archive_files.append(f"{item['path']} ({item['size']} KB)")
                    break
    
    if archive_files and not confirm:
        raise ArchiveFileError(archive_files)