import os
import json
import subprocess
from typing import Any, Dict, List, Optional

def run(cmd: List[str], check: bool = True) -> str:
    p = subprocess.run(cmd, text=True, capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}")
    return p.stdout.strip()

def gh_api(path: str) -> Dict[str, Any]:
    out = run(["gh", "api", path])
    return json.loads(out)

def get_issue(repo: str, issue_number: str) -> Dict[str, Any]:
    return gh_api(f"repos/{repo}/issues/{issue_number}")

def create_branch(branch: str, base: str = "HEAD") -> None:
    run(["git", "checkout", "-b", branch, base])

def git_status_porcelain() -> str:
    return run(["git", "status", "--porcelain"], check=False)

def git_commit_all(message: str) -> None:
    run(["git", "add", "-A"])
    try:
        run(["git", "commit", "-m", message])
    except RuntimeError as e:
        msg = str(e)
        if "Author identity unknown" in msg or "empty ident name" in msg:
            # Configure identity locally (no global)
            run(["git", "config", "user.name", "github-actions[bot]"])
            run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
            run(["git", "commit", "-m", message])
            return
        raise

def git_push(branch: str) -> None:
    run(["git", "push", "origin", branch])

def gh_pr_create(title: str, body: str, head: str, base: str = "main") -> str:
    out = run(["gh", "pr", "create", "--title", title, "--body", body, "--head", head, "--base", base])
    return out  # contains URL

def gh_issue_comment(issue_number: str, body: str) -> None:
    run(["gh", "issue", "comment", issue_number, "--body", body])

def current_branch() -> str:
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
