import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict

from agent.stacks.catalog_utils import (
    bootstrap_kind_for_stack,
    markers_found_for_stack,
    repo_has_any_stack_markers,
)
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


def write_out(rel_path: str, content: str) -> None:
    p = Path(rel_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content or "", encoding="utf-8")


def main() -> None:
    body = os.environ.get("COMMENT_BODY", "")
    req = extract_json_from_comment(body)

    catalog = load_catalog()

    requested_stack = str(req.get("stack") or "").strip()
    explicit_stack = bool(requested_stack and requested_stack.lower() not in ("auto",))

    # Resolve with requested (resolve_stack_spec handles stack='auto' by marker auto-detection).
    spec = resolve_stack_spec(req, repo_root=".", catalog=catalog)

    # Enterprise guard: if explicit stack but markers not found, try fallback auto.
    if explicit_stack and not markers_found_for_stack(catalog, spec.stack_id, repo_root="."):
        if repo_has_any_stack_markers(catalog, repo_root="."):
            # Repo seems like SOME known stack => auto-detect
            fallback_req = dict(req)
            fallback_req["stack"] = "auto"
            spec2 = resolve_stack_spec(fallback_req, repo_root=".", catalog=catalog)

            if markers_found_for_stack(catalog, spec2.stack_id, repo_root="."):
                write_out(
                    "agent/out/stack_resolution.txt",
                    (
                        f"Stack mismatch: requested='{requested_stack}' but markers not found. "
                        f"Fallback to auto-detected='{spec2.stack_id}'.\n"
                    ),
                )
                spec = spec2
            else:
                # Repo without markers for any stack, allow bootstrap if defined.
                bk = bootstrap_kind_for_stack(catalog, requested_stack)
                write_out(
                    "agent/out/stack_resolution.txt",
                    (
                        f"Repo has no stack markers. Keeping requested stack='{requested_stack}'. "
                        f"bootstrap.kind='{bk}'. stack_setup will scaffold if configured.\n"
                    ),
                )
                if bk in ("none", "", "unknown"):
                    raise ValueError(
                        f"Stack mismatch: requested='{requested_stack}' pero el repo no contiene markers "
                        "y este stack no define bootstrap. Usa stack='auto' o agrega bootstrap en catalog.yml."
                    )
        else:
            # Greenfield: keep requested; bootstrap may scaffold.
            bk = bootstrap_kind_for_stack(catalog, requested_stack)
            write_out(
                "agent/out/stack_resolution.txt",
                (
                    f"Repo has no stack markers. Keeping requested stack='{requested_stack}'. "
                    f"bootstrap.kind='{bk}'. stack_setup will scaffold if configured.\n"
                ),
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