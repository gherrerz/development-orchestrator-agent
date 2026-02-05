import os
from typing import Dict, List

MAX_FILE_BYTES = 60_000

def list_files(root: str = ".") -> List[str]:
    out = []
    for dirpath, _, filenames in os.walk(root):
        if dirpath.startswith("./.git") or "/.git" in dirpath:
            continue
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            if path.startswith("./.github/workflows"):
                # include workflows; ok
                pass
            out.append(path.replace("\\", "/"))
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
        return f"<<unreadable: {e}>>"

def snapshot(paths: List[str]) -> Dict[str, str]:
    return {p: read_file_safe(p) for p in paths}
