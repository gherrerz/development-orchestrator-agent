import os
import re
import tempfile
import subprocess
from typing import Dict, Any

def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

def write_file(path: str, content: str) -> None:
    ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8", errors="replace") as f:
        f.write(content if content.endswith("\n") else content + "\n")

def delete_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass

def looks_like_valid_unified_diff(diff_text: str) -> bool:
    # Minimal heuristics: must contain --- and +++ and at least one @@ hunk
    return ("--- " in diff_text) and ("+++ " in diff_text) and ("@@ " in diff_text)

def try_git_apply(diff_text: str) -> None:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tf:
        tf.write(diff_text)
        tmp = tf.name
    try:
        p = subprocess.run(["git", "apply", "--whitespace=fix", tmp], text=True, capture_output=True)
        if p.returncode != 0:
            raise RuntimeError(
                f"git apply failed\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}\nDiff:\n{diff_text[:2000]}"
            )
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

def extract_target_path_from_headers(diff_text: str) -> str:
    """
    Extracts path from '+++ b/...' header when present.
    """
    m = re.search(r"^\+\+\+\s+b/(.+)$", diff_text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    m = re.search(r"^\+\+\+\s+(.+)$", diff_text, re.MULTILINE)
    if m:
        p = m.group(1).strip()
        return p.replace("b/", "", 1) if p.startswith("b/") else p
    return ""

def strip_diff_headers_to_content(diff_text: str) -> str:
    """
    If diff is not valid (no hunks), treat lines starting with '+' as file content,
    and ignore header lines.
    """
    lines = []
    for line in diff_text.splitlines():
        if line.startswith(("--- ", "+++ ", "diff --git", "index ", "new file mode", "deleted file mode")):
            continue
        if line.startswith("@@"):
            continue
        if line.startswith("+"):
            lines.append(line[1:])
        elif line.startswith(" "):
            lines.append(line[1:])
        # ignore '-' lines in this fallback
    return "\n".join(lines).strip() + "\n"

def apply_patch_object(patch_obj: Dict[str, Any]) -> None:
    """
    Supports:
    - {"files": {path: {content, operation}}} -> write/delete directly
    - {"patches": [{path,diff}], ...} -> try git apply; if invalid diff, fallback to write file.
    """
    # Apply files-first (most reliable)
    if "files" in patch_obj and isinstance(patch_obj["files"], dict):
        for path, spec in patch_obj["files"].items():
            op = "modify"
            content = ""
            if isinstance(spec, dict):
                op = (spec.get("operation") or "modify").lower()
                content = spec.get("content", "")
            else:
                content = str(spec)
            norm_path = path[2:] if path.startswith("./") else path
            if op == "delete":
                delete_file(norm_path)
            else:
                write_file(norm_path, content)
        return

    # Otherwise apply diffs with safe fallback
    for item in patch_obj.get("patches", []):
        diff_text = item["diff"]
        if looks_like_valid_unified_diff(diff_text):
            try_git_apply(diff_text)
            continue

        # Fallback: write file content from '+' lines
        path = item.get("path") or extract_target_path_from_headers(diff_text)
        if not path:
            raise RuntimeError(f"Patch inválido y sin path detectable. Diff:\n{diff_text[:500]}")
        content = strip_diff_headers_to_content(diff_text)
        write_file(path, content)
