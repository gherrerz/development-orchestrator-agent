import json
import os
import re
import sys
import glob
from typing import Any, Dict, List, Tuple

from agent.stacks.registry import resolve_stack_spec, load_catalog


def extract_json_from_comment(body: str) -> Dict[str, Any]:
    """Expected formats:
      /agent run { ...json... }
      { ...json... }
    """
    if not body:
        raise ValueError("COMMENT_BODY vacío")

    m = re.search(r"/agent\s+run\s*(\{.*\})\s*$", body.strip(), re.DOTALL)
    if not m:
        m = re.search(r"(\{.*\})", body.strip(), re.DOTALL)
    if not m:
        raise ValueError("No se encontró JSON. Usa: /agent run { ... }")
    raw = m.group(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON inválido en comentario: {e}") from e


def _glob_many(patterns: List[str]) -> List[str]:
    out: List[str] = []
    for pat in patterns or []:
        try:
            out.extend(glob.glob(pat, recursive=True))
        except Exception:
            continue
    # unique & stable
    seen = set()
    uniq = []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return sorted(uniq)


def _exists_any_glob(patterns: List[str]) -> bool:
    for pat in patterns or []:
        if _glob_many([pat]):
            return True
    return False


def _catalog_stacks_view(catalog: Dict[str, Any]) -> Dict[str, Any]:
    """
    Soporta ambos formatos:
      A) mapping plano: { "python-django": {...}, "java-spring-boot": {...} }
      B) wrapper: { "stacks": { ... } }
    """
    if not isinstance(catalog, dict):
        return {}
    stacks = catalog.get("stacks")
    if isinstance(stacks, dict):
        return stacks
    return catalog  # mapping plano


def _catalog_stacks_view(catalog: Dict[str, Any]) -> Dict[str, Any]:
    """
    Soporta ambos formatos:
      A) mapping plano: { "python-django": {...}, "java-spring-boot": {...} }
      B) wrapper: { "stacks": { ... } }
    """
    if not isinstance(catalog, dict):
        return {}
    stacks = catalog.get("stacks")
    if isinstance(stacks, dict):
        return stacks
    return catalog


def _catalog_entry(catalog: Dict[str, Any], stack_id: str) -> Dict[str, Any]:
    stacks = _catalog_stacks_view(catalog)
    entry = stacks.get(stack_id) if isinstance(stacks, dict) else None
    return entry if isinstance(entry, dict) else {}


def _markers_found_for_stack(catalog: Dict[str, Any], stack_id: str) -> bool:
    entry = _catalog_entry(catalog, stack_id)
    markers = entry.get("markers") if isinstance(entry.get("markers"), dict) else {}
    any_of = markers.get("any_of") or []
    if not isinstance(any_of, list) or not any_of:
        return False
    return _exists_any_glob(any_of)


def _repo_has_any_stack_markers(catalog: Dict[str, Any]) -> bool:
    stacks = _catalog_stacks_view(catalog)
    if not isinstance(stacks, dict):
        return False
    for _sid, entry in stacks.items():
        if not isinstance(entry, dict):
            continue
        markers = entry.get("markers") if isinstance(entry.get("markers"), dict) else {}
        any_of = markers.get("any_of") or []
        if isinstance(any_of, list) and any_of and _exists_any_glob(any_of):
            return True
    return False


def _bootstrap_kind_for_stack(catalog: Dict[str, Any], stack_id: str) -> str:
    entry = _catalog_entry(catalog, stack_id)
    bootstrap = entry.get("bootstrap") if isinstance(entry.get("bootstrap"), dict) else {}
    return str(bootstrap.get("kind") or "none").strip().lower()


def _bootstrap_kind_for_stack(catalog: Dict[str, Any], stack_id: str) -> str:
    entry = _catalog_entry(catalog, stack_id)
    bootstrap = entry.get("bootstrap") if isinstance(entry.get("bootstrap"), dict) else {}
    return str(bootstrap.get("kind") or "none").strip().lower()


def main() -> None:
    body = os.environ.get("COMMENT_BODY", "")
    req = extract_json_from_comment(body)

    catalog = load_catalog()

    requested_stack = str(req.get("stack") or "").strip()
    explicit_stack = bool(requested_stack and requested_stack.lower() not in ("auto",))

    # Resolve with requested
    spec = resolve_stack_spec(req, repo_root=".", catalog=catalog)

    # If explicit stack but markers not found:
    if explicit_stack and not _markers_found_for_stack(catalog, spec.stack_id):
        if _repo_has_any_stack_markers(catalog):
            # Repo ya parece "otro stack": fallback a auto
            fallback_req = dict(req)
            fallback_req["stack"] = "auto"
            spec2 = resolve_stack_spec(fallback_req, repo_root=".", catalog=catalog)

            if _markers_found_for_stack(catalog, spec2.stack_id):
                os.makedirs("agent/out", exist_ok=True)
                with open("agent/out/stack_resolution.txt", "w", encoding="utf-8") as f:
                    f.write(
                        f"Stack mismatch: requested='{requested_stack}' but markers not found. "
                        f"Fallback to auto-detected='{spec2.stack_id}'.\n"
                    )
                spec = spec2
            else:
                # Repo sin markers (greenfield / repo-orquestador). No fallar aquí:
                # permitir que stack_setup/bootstrap scaffoldée el proyecto del stack solicitado.
                bk = _bootstrap_kind_for_stack(catalog, requested_stack)
                write_out(
                    "agent/out/stack_resolution.txt",
                    (
                        f"Repo has no stack markers. Keeping requested stack='{requested_stack}'. "
                        f"bootstrap.kind='{bk}'. stack_setup will scaffold if configured."
                    ),
                )
                # Si NO hay bootstrap configurado, recién ahí conviene fallar (fail-fast)
                if bk in ("none", "", "unknown"):
                    raise ValueError(
                        f"Stack mismatch: requested='{requested_stack}' pero el repo no contiene markers "
                        "y este stack no define bootstrap. Usa stack='auto' o agrega bootstrap en catalog.yml."
                    )
        else:
            # Repo no tiene markers de ningún stack (greenfield / repo-orquestador):
            # mantén el stack solicitado; stack_setup/bootstrap hará el scaffold.
            bk = _bootstrap_kind_for_stack(catalog, requested_stack)
            os.makedirs("agent/out", exist_ok=True)
            with open("agent/out/stack_resolution.txt", "w", encoding="utf-8") as f:
                f.write(
                    f"Repo has no stack markers. Keeping requested stack='{requested_stack}'. "
                    f"bootstrap.kind='{bk}'. stack_setup will scaffold if configured.\n"
                )

    out_path = os.environ.get("GITHUB_OUTPUT")
    outputs = {
        "stack": spec.stack_id,
        "language": spec.language,
        "test_command": spec.commands.test,
        "toolchain_kind": spec.toolchain.kind,
        "toolchain_version": spec.toolchain.version,
        "package_manager": str(spec.meta.get("package_manager", "")),
        "java_build_tool": str(spec.meta.get("java_build_tool", "")),
    }

    if not out_path:
        print(json.dumps(outputs, ensure_ascii=False))
        return

    with open(out_path, "a", encoding="utf-8") as f:
        for k, v in outputs.items():
            f.write(f"{k}={v}\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"::error::{e}")
        sys.exit(1)
