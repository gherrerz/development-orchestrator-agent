import os
import re
import tempfile
import subprocess
from typing import Any, Dict, List, Optional, Tuple


# -----------------------------
# Path hardening (enterprise)
# -----------------------------
def _normalize_rel_path(path: str) -> str:
    p = (path or "").strip()
    if p.startswith("./"):
        p = p[2:]
    # Normalize separators + remove redundant segments
    p = os.path.normpath(p)
    # Prevent weird normpath outputs
    if p in (".", ""):
        return ""
    return p


def _is_safe_rel_path(path: str) -> Tuple[bool, str]:
    """
    Only allow writing within repo workspace:
      - must be relative
      - no absolute paths
      - no parent traversal
    """
    p = _normalize_rel_path(path)
    if not p:
        return False, "empty path"
    if os.path.isabs(p):
        return False, "absolute path not allowed"
    # normpath can yield ".." or start with ".." for traversal
    if p == ".." or p.startswith(".." + os.sep) or p.startswith("../"):
        return False, "path traversal not allowed"
    return True, p


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
    return ("--- " in diff_text) and ("+++ " in diff_text) and ("@@ " in diff_text)


def try_git_apply(diff_text: str) -> None:
    """Apply unified diff using git apply (preferred)."""
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
        f.write(diff_text)
        tmp_path = f.name
    try:
        p = subprocess.run(["git", "apply", "--whitespace=nowarn", tmp_path], text=True, capture_output=True)
        if p.returncode != 0:
            raise RuntimeError(f"git apply failed:\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}")
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def extract_target_path_from_headers(diff_text: str) -> Optional[str]:
    """Try to infer file path from '+++ b/<path>' or diff --git headers."""
    m = re.search(r"\+\+\+\s+b/(.+)", diff_text)
    if m:
        return m.group(1).strip()
    m = re.search(r"diff --git a/(.+?) b/(.+)", diff_text)
    if m:
        return m.group(2).strip()
    return None


def strip_added_lines_to_content(diff_text: str) -> str:
    """Safe fallback extractor for add-only new files.

    We only reconstruct file content from '+' lines (excluding headers).
    This is safe for *new file* patches, but NOT for arbitrary edits.
    """
    lines: List[str] = []
    for line in diff_text.splitlines():
        if line.startswith(("--- ", "+++ ", "diff --git", "index ", "new file mode")):
            continue
        if line.startswith("@@"):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    return ("\n".join(lines).rstrip() + "\n") if lines else ""


def is_new_file_diff(diff_text: str) -> bool:
    return ("new file mode" in diff_text) or ("--- /dev/null" in diff_text)


def _coerce_file_entry(val: Any) -> Optional[Dict[str, str]]:
    """
    Normalize one files{} entry to the canonical shape:
      {"operation": "add|modify|delete", "content": "<str>"}
    Accepts:
      - string content
      - dict {"content": "...", "operation": "..."}
    """
    if isinstance(val, str):
        return {"operation": "modify", "content": val}

    if isinstance(val, dict):
        content = val.get("content")
        if content is None:
            # If delete, content is optional
            op = str(val.get("operation") or "modify").strip().lower()
            if op == "delete":
                return {"operation": "delete", "content": ""}
            return None
        if not isinstance(content, str):
            return None
        op = str(val.get("operation") or "modify").strip().lower()
        if op not in ("add", "modify", "delete"):
            op = "modify"
        return {"operation": op, "content": content}

    return None


def apply_patch_object(patch_obj: Dict[str, Any]) -> None:
    """Apply a patch object. Supports:

    1) Modern full-content format (recommended):
       { "files": { "path/to/file": "full content" OR {"operation": "...","content":"..."}, ... },
         "notes": [...] }

    2) Unified diff format:
       { "patches": [ { "path": "...", "diff": "..." }, ... ], "notes": [...] }

    Enterprise rules:
      - Full-content files{} is the most robust and language-agnostic.
      - In files{} mode we support operation add/modify/delete.
      - UPSERT semantics: operation=modify on missing file -> treat as add.
      - Path hardening: reject abs / traversal paths.
      - For diffs: prefer git apply; fallback ONLY for new-file add-only diffs.
    """
    if not isinstance(patch_obj, dict):
        raise ValueError("Patch debe ser un objeto JSON.")

    # -----------------------
    # Full-content files{} mode
    # -----------------------
    files_obj = patch_obj.get("files")
    if isinstance(files_obj, dict) and files_obj:
        for raw_path, raw_val in files_obj.items():
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue

            ok, p = _is_safe_rel_path(raw_path)
            if not ok:
                raise RuntimeError(f"Ruta de archivo inválida o insegura: '{raw_path}' ({p})")

            entry = _coerce_file_entry(raw_val)
            if not entry:
                # ignore invalid entry (but do not crash)
                continue

            op = entry["operation"]
            content = entry["content"]

            # Empty file marker (compat)
            if isinstance(content, str) and content.strip().lower() in ("(archivo vacío)", "(archivo vacio)"):
                content = ""

            # UPSERT: modify on missing -> add
            if op == "modify" and not os.path.exists(p):
                op = "add"

            if op == "delete":
                delete_file(p)
            else:
                write_file(p, content)

        # deletions (optional legacy)
        deletions = patch_obj.get("delete")
        if isinstance(deletions, list):
            for raw_p in deletions:
                if isinstance(raw_p, str):
                    ok, p = _is_safe_rel_path(raw_p)
                    if ok:
                        delete_file(p)
        return

    # -----------------------
    # Unified diff mode
    # -----------------------
    patches = patch_obj.get("patches") or []
    if not isinstance(patches, list):
        raise ValueError("patches debe ser una lista.")

    for item in patches:
        if not isinstance(item, dict):
            continue

        diff_text = str(item.get("diff", "") or "")
        raw_path = str(item.get("path", "") or "").strip()

        # Prefer git apply when possible
        if diff_text and looks_like_valid_unified_diff(diff_text):
            try:
                try_git_apply(diff_text)
                continue
            except Exception:
                # fallthrough to safe fallback
                pass

        if not raw_path:
            raw_path = extract_target_path_from_headers(diff_text) or ""
        if not raw_path:
            raise RuntimeError(
                "Patch inválido: no se pudo determinar 'path'. "
                "Para máxima robustez, emite formato files{} con contenido completo."
            )

        ok, path = _is_safe_rel_path(raw_path)
        if not ok:
            raise RuntimeError(f"Ruta de archivo inválida o insegura: '{raw_path}' ({path})")

        # Explicit empty-file marker
        if "(archivo vacío)" in diff_text or "(archivo vacio)" in diff_text:
            write_file(path, "")
            continue

        # Safe fallback only for new files
        if not is_new_file_diff(diff_text):
            raise RuntimeError(
                f"No se pudo aplicar patch para '{path}'. "
                "El diff no es aplicable y NO es un 'new file' seguro. "
                "Emite formato files{} con contenido completo."
            )

        content = strip_added_lines_to_content(diff_text)
        if not content:
            # Create empty as last resort for new file diffs
            write_file(path, "")
            continue

        write_file(path, content)
