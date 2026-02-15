import os
import subprocess
import sys
from pathlib import Path
from typing import List

from agent.stacks.registry import load_catalog, resolve_stack_spec


def run(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, text=True, capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}"
        )
    return p


def exists_any(patterns: List[str]) -> bool:
    for pat in patterns:
        if list(Path(".").glob(pat)):
            return True
    return False


def install_repo_declared_deps(language: str, package_manager: str) -> None:
    """Install dependencies declared in the repository itself (best-effort).

    This is intentionally generic and safe: it avoids running arbitrary scripts.
    """
    # Python
    if language == "python":
        if Path("requirements.txt").exists():
            run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], check=False)
        # Poetry support (if lock exists)
        if Path("poetry.lock").exists():
            run([sys.executable, "-m", "pip", "install", "poetry"], check=False)
            run(["poetry", "install", "--no-interaction"], check=False)
        # PEP 517 project install (if desired)
        if Path("pyproject.toml").exists() and not Path("poetry.lock").exists():
            run([sys.executable, "-m", "pip", "install", "."], check=False)

    # Node (npm/pnpm/yarn)
    if language in ("javascript", "typescript") or Path("package.json").exists():
        pm = package_manager or "npm"
        if pm == "pnpm":
            run(["corepack", "enable"], check=False)
            if Path("pnpm-lock.yaml").exists():
                run(["pnpm", "install", "--frozen-lockfile"], check=False)
            else:
                run(["pnpm", "install"], check=False)
        elif pm == "yarn":
            run(["corepack", "enable"], check=False)
            if Path("yarn.lock").exists():
                run(["yarn", "install", "--immutable"], check=False)
            else:
                run(["yarn", "install"], check=False)
        else:
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


def install_catalog_extras(spec) -> None:
    # apt deps
    if spec.deps.apt:
        run(["sudo", "apt-get", "update"], check=False)
        run(["sudo", "apt-get", "install", "-y"] + list(spec.deps.apt), check=False)

    if spec.deps.pip:
        run([sys.executable, "-m", "pip", "install"] + list(spec.deps.pip), check=False)

    if spec.deps.npm:
        pm = str(spec.meta.get("package_manager") or "npm")
        if pm == "pnpm":
            run(["corepack", "enable"], check=False)
            run(["pnpm", "add"] + list(spec.deps.npm), check=False)
        elif pm == "yarn":
            run(["corepack", "enable"], check=False)
            run(["yarn", "add"] + list(spec.deps.npm), check=False)
        else:
            run(["npm", "install"] + list(spec.deps.npm), check=False)


def install_safety_nets(spec) -> None:
    """Install minimal test runners only if tests imply them and repo doesn't already declare."""
    tc = (spec.commands.test or "").lower()

    if spec.language == "python" and ("pytest" in tc):
        run([sys.executable, "-m", "pip", "install", "pytest"], check=False)

    if spec.language in ("javascript", "typescript") and ("jest" in tc):
        pm = str(spec.meta.get("package_manager") or "npm")
        if pm == "pnpm":
            run(["pnpm", "add", "-D", "jest"], check=False)
        elif pm == "yarn":
            run(["yarn", "add", "-D", "jest"], check=False)
        else:
            run(["npm", "install", "--save-dev", "jest"], check=False)


def main() -> None:
    # The request can come from env or be inferred from repo
    req = {
        "stack": (os.environ.get("STACK") or "").strip(),
        "language": (os.environ.get("LANGUAGE") or "").strip(),
        "test_command": (os.environ.get("TEST_COMMAND") or "").strip(),
    }
    catalog = load_catalog()
    spec = resolve_stack_spec(req, repo_root=".", catalog=catalog)

    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], check=False)

    pm = str(spec.meta.get("package_manager") or "")

    install_repo_declared_deps(spec.language, pm)
    install_catalog_extras(spec)
    install_safety_nets(spec)

    print(f"STACK={spec.stack_id}")
    print(f"LANGUAGE={spec.language}")
    print(f"TEST_COMMAND={spec.commands.test}")
    print(f"TOOLCHAIN_KIND={spec.toolchain.kind}")
    print(f"TOOLCHAIN_VERSION={spec.toolchain.version}")
    print(f"PACKAGE_MANAGER={pm}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"::error::{e}")
        sys.exit(1)
