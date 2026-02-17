import os
import json
import subprocess
from typing import Any, Dict, List, Optional


def run(cmd: List[str], check: bool = True) -> str:
    p = subprocess.run(cmd, text=True, capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}"
        )
    return (p.stdout or "").strip()


def gh_api(path: str) -> Dict[str, Any]:
    out = run(["gh", "api", path])
    return json.loads(out)


def get_issue(repo: str, issue_number: str) -> Dict[str, Any]:
    return gh_api(f"repos/{repo}/issues/{issue_number}")


def ensure_branch(branch: str, base: str = "main") -> None:
    """Checkout an existing branch if it exists; otherwise create it from base."""
    # If branch exists locally, checkout
    p = subprocess.run(
        ["git", "show-ref", "--verify", f"refs/heads/{branch}"],
        text=True,
        capture_output=True,
    )
    if p.returncode == 0:
        run(["git", "checkout", branch])
        return

    # If branch exists in origin, fetch and checkout
    p = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        text=True,
        capture_output=True,
    )
    if p.returncode == 0 and (p.stdout or "").strip():
        run(["git", "fetch", "origin", f"{branch}:{branch}"])
        run(["git", "checkout", branch])
        return

    # Otherwise create from base (base can be local branch name or a ref like origin/main)
    run(["git", "checkout", "-b", branch, base])


def create_branch(branch: str, base: str = "HEAD") -> None:
    """Backwards compatible: create a new branch from base."""
    run(["git", "checkout", "-b", branch, base])


def current_branch() -> str:
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"])


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
            run(
                ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"]
            )
            run(["git", "commit", "-m", message])
            return
        # Allow no-op commits to be handled by callers
        raise


def git_commits_ahead(base_ref: str, head_ref: str = "HEAD") -> int:
    """Returns how many commits head_ref is ahead of base_ref."""
    # Ensure we have base_ref locally.
    subprocess.run(
        ["git", "fetch", "--no-tags", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*"],
        text=True,
        capture_output=True,
    )
    out = run(["git", "rev-list", "--count", f"{base_ref}..{head_ref}"], check=False)
    try:
        return int((out or "0").strip() or 0)
    except Exception:
        return 0


def git_push(branch: str) -> None:
    run(["git", "push", "origin", branch])


def gh_pr_view_url(head: str) -> Optional[str]:
    """Return PR URL for the given head branch if one exists, else None."""
    p = subprocess.run(
        ["gh", "pr", "view", head, "--json", "url", "-q", ".url"],
        text=True,
        capture_output=True,
    )
    if p.returncode != 0:
        return None
    url = (p.stdout or "").strip()
    return url or None


def gh_pr_create(title: str, body: str, head: str, base: str = "main") -> str:
    out = run(["gh", "pr", "create", "--title", title, "--body", body, "--head", head, "--base", base])
    return out  # contains URL


def gh_pr_ensure(title: str, body: str, head: str, base: str = "main") -> str:
    """Idempotent PR creation: returns existing PR URL for head if found, else creates one."""
    existing = gh_pr_view_url(head)
    if existing:
        return existing
    return gh_pr_create(title=title, body=body, head=head, base=base)


def gh_issue_comment(issue_number: str, body: str) -> None:
    run(["gh", "issue", "comment", issue_number, "--body", body])
