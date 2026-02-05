import os
import subprocess
import tempfile
from typing import List, Dict, Any

def apply_unified_diff(diff_text: str) -> None:
    """
    Apply unified diff using 'git apply'. Diff must include file headers.
    """
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tf:
        tf.write(diff_text)
        tmp = tf.name
    try:
        p = subprocess.run(["git", "apply", "--whitespace=fix", tmp], text=True, capture_output=True)
        if p.returncode != 0:
            raise RuntimeError(f"git apply failed\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}\nDiff:\n{diff_text[:2000]}")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

def apply_patch_object(patch_obj: Dict[str, Any]) -> None:
    for item in patch_obj.get("patches", []):
        apply_unified_diff(item["diff"])
