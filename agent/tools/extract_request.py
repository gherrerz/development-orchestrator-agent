import json
import os
import re
import sys
from typing import Any, Dict

def extract_json_from_comment(body: str) -> Dict[str, Any]:
    """
    Expected formats:
      /agent run { ...json... }
      /agent run
      { ...json... }
    """
    if not body:
        raise ValueError("COMMENT_BODY vacío")

    # find first {...} block after /agent run
    m = re.search(r"/agent\s+run\s*(\{.*\})\s*$", body.strip(), re.DOTALL)
    if not m:
        # fallback: first {...} anywhere
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

    stack = (req.get("stack") or "").strip()
    language = (req.get("language") or "").strip().lower()
    test_command = (req.get("test_command") or "").strip()

    # fallback: infer from stack prefix
    if not language and stack:
        if stack.startswith("python-"):
            language = "python"
        elif stack.startswith("java-"):
            language = "java"
        elif stack.startswith("node-"):
            language = "javascript"
        elif stack.startswith("dotnet-"):
            language = "dotnet"
        elif stack.startswith("go-"):
            language = "go"

    if not test_command:
        # common defaults
        if language == "python":
            test_command = "pytest -q"
        elif language in ("javascript", "typescript"):
            test_command = "npm test"
        elif language == "java":
            test_command = "mvn -q test"
        elif language in ("dotnet", "csharp"):
            test_command = "dotnet test"
        elif language == "go":
            test_command = "go test ./..."
        else:
            test_command = ""

    # Emit GitHub Actions outputs
    # https://docs.github.com/actions/using-workflows/workflow-commands-for-github-actions
    out_path = os.environ.get("GITHUB_OUTPUT")
    if not out_path:
        print(json.dumps({"stack": stack, "language": language, "test_command": test_command}))
        return

    with open(out_path, "a", encoding="utf-8") as f:
        f.write(f"stack={stack}\n")
        f.write(f"language={language}\n")
        f.write(f"test_command={test_command}\n")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"::error::{e}")
        sys.exit(1)
