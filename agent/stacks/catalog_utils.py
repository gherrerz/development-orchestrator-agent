"""Utilities for working with the stack catalog (catalog.yml).

This module intentionally centralizes the "catalog shape" logic because several
parts of the project (extract_request, stack_setup, orchestrator) need to:

- support both catalog formats:
    A) flat mapping: { "python-django": {...}, ... }
    B) wrapper: { "stacks": { ... } }
- inspect markers (glob patterns) to decide whether a stack is present
- auto-detect a stack id from repo markers when stack="auto"

Keeping this here prevents duplicated helper functions across modules.
"""

from __future__ import annotations

import glob
import os
from typing import Any, Dict, List, Tuple


def catalog_stacks_view(catalog: Dict[str, Any]) -> Dict[str, Any]:
    """Return the mapping {stack_id -> entry} for either supported catalog shape."""
    if not isinstance(catalog, dict):
        return {}
    stacks = catalog.get("stacks")
    if isinstance(stacks, dict):
        return stacks
    return catalog


def catalog_entry(catalog: Dict[str, Any], stack_id: str) -> Dict[str, Any]:
    stacks = catalog_stacks_view(catalog)
    entry = stacks.get(stack_id) if isinstance(stacks, dict) else None
    return entry if isinstance(entry, dict) else {}


def _glob_many(patterns: List[str], repo_root: str = ".") -> List[str]:
    out: List[str] = []
    for pat in patterns or []:
        if not isinstance(pat, str) or not pat.strip():
            continue
        try:
            out.extend(glob.glob(os.path.join(repo_root, pat), recursive=True))
        except Exception:
            continue
    # unique, stable
    seen = set()
    uniq: List[str] = []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return sorted(uniq)


def exists_any_glob(patterns: List[str], repo_root: str = ".") -> bool:
    for pat in patterns or []:
        if _glob_many([pat], repo_root=repo_root):
            return True
    return False


def marker_score(patterns: List[str], repo_root: str = ".") -> int:
    """Simple score: number of marker patterns that match at least one file."""
    score = 0
    for pat in patterns or []:
        if _glob_many([pat], repo_root=repo_root):
            score += 1
    return score


def markers_found_for_stack(catalog: Dict[str, Any], stack_id: str, repo_root: str = ".") -> bool:
    entry = catalog_entry(catalog, stack_id)
    markers = entry.get("markers") if isinstance(entry.get("markers"), dict) else {}
    any_of = markers.get("any_of") or []
    if not isinstance(any_of, list) or not any_of:
        return False
    return exists_any_glob(any_of, repo_root=repo_root)


def repo_has_any_stack_markers(catalog: Dict[str, Any], repo_root: str = ".") -> bool:
    stacks = catalog_stacks_view(catalog)
    if not isinstance(stacks, dict):
        return False
    for _sid, entry in stacks.items():
        if not isinstance(entry, dict):
            continue
        markers = entry.get("markers") if isinstance(entry.get("markers"), dict) else {}
        any_of = markers.get("any_of") or []
        if isinstance(any_of, list) and any_of and exists_any_glob(any_of, repo_root=repo_root):
            return True
    return False


def bootstrap_kind_for_stack(catalog: Dict[str, Any], stack_id: str) -> str:
    entry = catalog_entry(catalog, stack_id)
    bootstrap = entry.get("bootstrap") if isinstance(entry.get("bootstrap"), dict) else {}
    return str(bootstrap.get("kind") or "none").strip().lower()


def auto_detect_stack_id(
    catalog: Dict[str, Any],
    repo_root: str = ".",
    preferred_language: str = "",
) -> str:
    """Return best-matching stack_id based on markers (deterministic).

    - Filter by language if preferred_language is provided.
    - Score by count of marker patterns that match.
    - Ties: (score desc, stack_id asc)

    Returns "" if nothing matches.
    """
    stacks = catalog_stacks_view(catalog)
    if not isinstance(stacks, dict):
        return ""

    preferred_language = (preferred_language or "").strip().lower()

    scored: List[Tuple[int, str]] = []
    for sid, entry in stacks.items():
        if not isinstance(entry, dict) or not isinstance(sid, str) or not sid.strip():
            continue

        lang = str(entry.get("language") or "").strip().lower()
        if preferred_language and lang and lang != preferred_language:
            continue

        markers = entry.get("markers") if isinstance(entry.get("markers"), dict) else {}
        any_of = markers.get("any_of") or []
        if not isinstance(any_of, list) or not any_of:
            continue

        s = marker_score(any_of, repo_root=repo_root)
        if s > 0:
            scored.append((s, sid))

    if not scored:
        if preferred_language:
            return auto_detect_stack_id(catalog, repo_root=repo_root, preferred_language="")
        return ""

    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[0][1]