import json
import os
import re
import sys
from typing import Any, Dict

from agent.stacks.registry import resolve_stack_spec


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


def main() -> None:
    body = os.environ.get("COMMENT_BODY", "")
    req = extract_json_from_comment(body)

    spec = resolve_stack_spec(req, repo_root=".")

    # Emit GitHub Actions outputs
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
