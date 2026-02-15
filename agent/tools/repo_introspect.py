import os
from typing import Dict, List, Optional, Union

MAX_FILE_BYTES = 60_000

DEFAULT_IGNORE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "dist", "build", ".next", ".nuxt", "target", "bin", "obj",
    ".idea", ".vscode",
}

DEFAULT_IGNORE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".tgz", ".7z",
    ".exe", ".dll", ".so", ".dylib",
}


def _should_skip_dir(dirpath: str) -> bool:
    parts = dirpath.replace("\\", "/").split("/")
    return any(p in DEFAULT_IGNORE_DIRS for p in parts if p)


def list_files(root: str = ".") -> List[str]:
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if _should_skip_dir(dirpath):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_IGNORE_DIRS]

        for fn in filenames:
            lower = fn.lower()
            if any(lower.endswith(s) for s in DEFAULT_IGNORE_SUFFIXES):
                continue
            path = os.path.join(dirpath, fn).replace("\\", "/")
            out.append(path)
    out.sort()
    return out


def read_file_safe(path: str) -> str:
    try:
        size = os.path.getsize(path)
        if size > MAX_FILE_BYTES:
            return f"<<omitted: file too large ({size} bytes)>>"
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"<<error reading file: {e}>>"


def snapshot(files_or_max: Union[List[str], int, None] = None) -> Dict[str, str]:
    """Return a snapshot of repository text files (path -> content).

    - If a list[str] is provided, snapshot only those paths (best for prompt focus).
    - If an int is provided, snapshot first N files from list_files().
    - If None, defaults to 120 files.
    """
    if isinstance(files_or_max, list):
        files = files_or_max
    else:
        max_files = int(files_or_max) if isinstance(files_or_max, int) else 120
        files = list_files(".")[:max_files]

    out: Dict[str, str] = {}
    for p in files:
        out[p] = read_file_safe(p)
    return out
