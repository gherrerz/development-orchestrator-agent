# agent/tools/failure_hints.py
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple


@dataclass(frozen=True)
class FailureMeta:
    kind: str
    hints: List[str]
    signature: str  # stable-ish signature for stuck detection
    matched_rule_id: Optional[str] = None


# ---------------------------
# Rules loading (hotfix)
# ---------------------------

_DEFAULT_RULES_PATHS = [
    "agent/tools/failure_hints_rules.yml",
    "agent/failure_hints_rules.yml",
]


def _load_yaml(path: str) -> Optional[Dict[str, Any]]:
    try:
        import yaml  # type: ignore
    except Exception:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _get_rules_config() -> Dict[str, Any]:
    """
    Loads YAML config from FAILURE_HINTS_RULES_PATH or default locations.
    Returns empty config if unavailable.
    """
    env_path = (os.environ.get("FAILURE_HINTS_RULES_PATH") or "").strip()
    if env_path:
        cfg = _load_yaml(env_path)
        if cfg:
            return cfg

    for p in _DEFAULT_RULES_PATHS:
        cfg = _load_yaml(p)
        if cfg:
            return cfg

    return {}


def _compile_rules(cfg: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, List[str]]]:
    """
    Compile regex patterns. Invalid patterns are ignored.
    Returns (rules, language_hints).
    """
    rules_in = cfg.get("rules") or []
    compiled: List[Dict[str, Any]] = []

    if isinstance(rules_in, list):
        for r in rules_in:
            if not isinstance(r, dict):
                continue
            patterns = r.get("patterns") or []
            if not isinstance(patterns, list):
                continue
            compiled_patterns = []
            for p in patterns:
                if not isinstance(p, str) or not p.strip():
                    continue
                try:
                    compiled_patterns.append(re.compile(p))
                except re.error:
                    # ignore invalid regex (hotfix-friendly)
                    continue
            if not compiled_patterns:
                continue

            compiled.append(
                {
                    "id": str(r.get("id") or ""),
                    "kind": str(r.get("kind") or "unknown"),
                    "priority": int(r.get("priority") or 1000),
                    "stop_on_match": bool(r.get("stop_on_match", True)),
                    "patterns": compiled_patterns,
                    "hints": [str(x) for x in (r.get("hints") or []) if isinstance(x, str) and x.strip()],
                }
            )

    compiled.sort(key=lambda x: x["priority"])

    lang_hints_in = cfg.get("language_hints") or {}
    lang_hints: Dict[str, List[str]] = {}
    if isinstance(lang_hints_in, dict):
        for k, v in lang_hints_in.items():
            if not isinstance(k, str):
                continue
            if isinstance(v, list):
                lang_hints[k.lower().strip()] = [str(x) for x in v if isinstance(x, str) and x.strip()]

    return compiled, lang_hints


# Cache per-process (Actions run)
_RULES_CACHE: Optional[Tuple[List[Dict[str, Any]], Dict[str, List[str]]]] = None


def _rules() -> Tuple[List[Dict[str, Any]], Dict[str, List[str]]]:
    global _RULES_CACHE
    if _RULES_CACHE is None:
        cfg = _get_rules_config()
        _RULES_CACHE = _compile_rules(cfg)
    return _RULES_CACHE


# ---------------------------
# Built-in helpers
# ---------------------------

def _normalize_for_signature(text: str) -> str:
    """
    Reduce ruido (paths/line numbers/timestamps) para detectar repetición real del error.
    """
    t = text or ""
    t = re.sub(r"(/[A-Za-z0-9_\-./]+)+", "<PATH>", t)
    t = re.sub(r"[A-Za-z]:\\\\[A-Za-z0-9_\-\\\\.]+", "<PATH>", t)
    t = re.sub(r":\d+", ":<N>", t)
    t = re.sub(r"line\s+\d+", "line <N>", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:2500]


def _builtin_float_patterns_match(out: str) -> bool:
    float_patterns = [
        r"\d+\.\d+\s*!=\s*\d+\.\d+",
        r"expected.*\d+\.\d+.*but.*\d+\.\d+",
        r"Expected:.*\d+\.\d+.*Received:.*\d+\.\d+",
        r"E\s+assert\s+.*\d+\.\d+.*==\s+.*\d+\.\d+",
        r"AssertionError:.*\d+\.\d+.*\d+\.\d+",
    ]
    return any(re.search(p, out, flags=re.IGNORECASE) for p in float_patterns)


def _builtin_kind_fallback(test_output: str) -> str:
    out = test_output or ""
    if _builtin_float_patterns_match(out):
        return "float_precision_mismatch"
    if "ModuleNotFoundError" in out or "No module named" in out:
        return "missing_module"
    if "Cannot find module" in out or "ERR_MODULE_NOT_FOUND" in out:
        return "missing_module"
    if "BUILD FAILURE" in out or "Could not resolve dependencies" in out or "Could not find artifact" in out:
        return "build_failure"
    if "pytest: not found" in out or "No module named pytest" in out:
        return "runner_missing"
    if "jest: not found" in out or "jest command not found" in out:
        return "runner_missing"
    if "dotnet: command not found" in out or "mvn: command not found" in out or "go: command not found" in out:
        return "runner_missing"
    return "unknown"


# ---------------------------
# Public API
# ---------------------------

def classify_failure(
    test_output: str,
    language: str = "",
    stack: str = "",
    test_command: str = "",
) -> FailureMeta:
    """
    Multi-stack classifier with external YAML rules + built-in fallback.
    Rules are evaluated first (ordered by priority).
    """
    out = test_output or ""
    lang = (language or "").lower().strip()

    rules, language_hints = _rules()

    # 1) Apply external rules (first match wins if stop_on_match)
    best_kind: Optional[str] = None
    best_hints: List[str] = []
    best_rule_id: Optional[str] = None

    for r in rules:
        if any(p.search(out) for p in r["patterns"]):
            best_kind = r["kind"]
            best_hints.extend(r.get("hints") or [])
            best_rule_id = r.get("id") or None
            if r.get("stop_on_match", True):
                break

    # 2) Built-in fallback classification (if no rule matched)
    kind = best_kind or _builtin_kind_fallback(out)

    # 3) Merge language-specific hints for float precision mismatch
    hints = list(best_hints)
    if kind == "float_precision_mismatch":
        hints.append("Posible mismatch de precisión (float). Evita comparaciones exactas.")
        # Language-specific extra hints from YAML
        hints.extend(language_hints.get(lang, []))
        # generic fallback if YAML lacks language
        if not language_hints.get(lang):
            hints.append("Evita comparar floats exactos; usa tolerancia (delta/epsilon) o asserts 'close'.")
        # domain nudge
        if any(k in out.lower() for k in ["credit", "cuota", "interest", "interés", "amort"]):
            hints.append("Dominio financiero: considera Decimal/centavos (integers) para evitar drift de float.")

    signature = f"{kind}:{_normalize_for_signature(out)}"
    return FailureMeta(kind=kind, hints=hints, signature=signature, matched_rule_id=best_rule_id)


def summarize_hints(meta: FailureMeta, limit: int = 8) -> List[str]:
    """
    Ensure hints are concise and capped to keep LLM prompt tight.
    """
    uniq: List[str] = []
    for h in meta.hints:
        h2 = (h or "").strip()
        if h2 and h2 not in uniq:
            uniq.append(h2)
        if len(uniq) >= limit:
            break
    return uniq


def should_count_as_stuck(prev_signature: str, new_signature: str, changed_files: str) -> bool:
    """
    Stuck is only true if:
      - signature repeats AND
      - changed_files (set) hasn't changed meaningfully
    """
    if not prev_signature or not new_signature:
        return False
    if prev_signature != new_signature:
        return False

    if not (changed_files or "").strip():
        return True

    files = sorted([f.strip() for f in (changed_files or "").splitlines() if f.strip()])
    files_sig = "|".join(files)
    return bool(files_sig)
