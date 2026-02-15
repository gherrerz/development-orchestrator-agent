import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

def run(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, text=True, capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}")
    return p

def exists_any(patterns: List[str]) -> bool:
    for pat in patterns:
        if list(Path(".").glob(pat)):
            return True
    return False

def load_catalog() -> Dict[str, Any]:
    yml = Path("agent/stacks/catalog.yml")
    if not yml.exists():
        return {}
    try:
        import yaml  # type: ignore
    except Exception:
        # best-effort: skip if PyYAML missing
        return {}
    return (yaml.safe_load(yml.read_text(encoding="utf-8")) or {})

def install_repo_declared_deps(language: str) -> None:
    # Python
    if language == "python":
        if Path("requirements.txt").exists():
            run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        if Path("pyproject.toml").exists():
            # install project itself (if it is a package)
            run([sys.executable, "-m", "pip", "install", "."])

    # Node
    if language in ("javascript", "typescript") or Path("package.json").exists():
        if Path("package-lock.json").exists():
            run(["npm", "ci"], check=False)
        elif Path("package.json").exists():
            run(["npm", "install"], check=False)

    # Java
    if language == "java" or Path("pom.xml").exists() or Path("gradlew").exists():
        if Path("pom.xml").exists():
            run(["mvn", "-q", "-DskipTests", "package"], check=False)
        if Path("gradlew").exists():
            run(["chmod", "+x", "./gradlew"], check=False)
            run(["./gradlew", "-q", "assemble"], check=False)

    # .NET
    if language in ("dotnet", "csharp") or exists_any(["*.sln", "*.csproj"]):
        run(["dotnet", "restore"], check=False)

    # Go
    if language == "go" or Path("go.mod").exists():
        run(["go", "mod", "download"], check=False)

def install_catalog_extras(stack: str, catalog: Dict[str, Any]) -> None:
    if not stack:
        return
    spec = catalog.get(stack) or {}
    deps = (spec.get("deps") or {})
    pip_deps = deps.get("pip") or []
    npm_deps = deps.get("npm") or []
    apt_deps = deps.get("apt") or []

    if apt_deps:
        run(["sudo", "apt-get", "update"], check=False)
        run(["sudo", "apt-get", "install", "-y"] + list(apt_deps), check=False)

    if pip_deps:
        run([sys.executable, "-m", "pip", "install"] + list(pip_deps), check=False)

    if npm_deps:
        run(["npm", "install"] + list(npm_deps), check=False)

def install_safety_nets(language: str, test_command: str, stack: str) -> None:
    """
    Only install minimal test runners if test_command implies them and repo doesn't declare them yet.
    Keep this generic and minimal.
    """
    tc = (test_command or "").lower()

    if language == "python" and ("pytest" in tc):
        # avoid "pytest: not found"
        run([sys.executable, "-m", "pip", "install", "pytest"], check=False)

    if language in ("javascript", "typescript") and ("jest" in tc):
        run(["npm", "install", "jest"], check=False)

    # Add small stack-specific nets where industry standard
    if stack == "python-django":
        # if tests use pytest-django, ensure it exists
        run([sys.executable, "-m", "pip", "install", "django", "pytest-django"], check=False)

    if stack == "python-fastapi-postgres":
        run([sys.executable, "-m", "pip", "install", "fastapi", "httpx", "uvicorn[standard]"], check=False)

def main() -> None:
    stack = (os.environ.get("STACK") or "").strip()
    language = (os.environ.get("LANGUAGE") or "").strip().lower()
    test_command = (os.environ.get("TEST_COMMAND") or "").strip()

    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], check=False)

    # Infer language if missing
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

    # repo declared deps first
    install_repo_declared_deps(language)

    # catalog extras next
    catalog = load_catalog()
    install_catalog_extras(stack, catalog)

    # minimal safety nets last
    install_safety_nets(language, test_command, stack)

    print(f"STACK={stack}")
    print(f"LANGUAGE={language}")
    print(f"TEST_COMMAND={test_command}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"::error::{e}")
        sys.exit(1)
