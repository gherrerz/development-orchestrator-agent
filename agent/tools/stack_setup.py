import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

from agent.stacks.registry import load_catalog, resolve_stack_spec


def run(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, text=True, capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}"
        )
    return p


def exists_any(patterns: List[str]) -> bool:
    """True if any glob pattern matches at least one file."""
    for pat in patterns:
        if list(Path(".").glob(pat)):
            return True
    return False


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_file_if_missing(rel_path: str, content: str) -> bool:
    p = Path(rel_path)
    if p.exists():
        return False
    ensure_parent_dir(p)
    p.write_text(content, encoding="utf-8")
    return True


def load_stack_entry(catalog: Dict[str, Any], stack_id: str) -> Dict[str, Any]:
    entry = catalog.get(stack_id) or {}
    return entry if isinstance(entry, dict) else {}


def write_preflight_report(report: Dict[str, Any]) -> None:
    out_dir = Path("agent/out")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "preflight.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def preflight_bootstrap(spec, catalog_entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Preflight: ensure minimal build/config markers exist for the selected stack.
    If missing and bootstrap.kind is defined, scaffold minimal files/commands.

    Always writes agent/out/preflight.json for observability.
    """
    markers = catalog_entry.get("markers") if isinstance(catalog_entry.get("markers"), dict) else {}
    any_of = markers.get("any_of") or []
    if not isinstance(any_of, list):
        any_of = []

    bootstrap = catalog_entry.get("bootstrap") if isinstance(catalog_entry.get("bootstrap"), dict) else {}
    b_kind = str((bootstrap or {}).get("kind") or "none").strip().lower()

    report: Dict[str, Any] = {
        "stack_id": getattr(spec, "stack_id", ""),
        "language": getattr(spec, "language", ""),
        "markers_any_of": any_of,
        "markers_found": bool(any_of and exists_any(any_of)),
        "bootstrap_kind": b_kind,
        "bootstrap_applied": False,
        "created_files": [],
        "commands_run": [],
        "notes": [],
    }

    # No markers defined => skip (unknown stack), but still write report
    if not any_of:
        report["notes"].append("No markers.any_of defined for this stack; skipping bootstrap.")
        write_preflight_report(report)
        return report

    # Markers present => skip, but still write report
    if report["markers_found"]:
        report["notes"].append("Markers present; skipping bootstrap.")
        write_preflight_report(report)
        return report

    # Markers missing => attempt bootstrap
    report["notes"].append("Markers missing; attempting bootstrap.")
    created: List[str] = []

    if b_kind == "template":
        templates = bootstrap.get("templates") or []
        if not isinstance(templates, list):
            templates = []
        for t in templates:
            if not isinstance(t, dict):
                continue
            path = str(t.get("path") or "").strip()
            content = t.get("content")
            if not path or not isinstance(content, str):
                continue
            if write_file_if_missing(path, content):
                created.append(path)

        report["bootstrap_applied"] = True
        report["created_files"] = created
        report["notes"].append(f"Template bootstrap created_files={len(created)}")

    elif b_kind == "command":
        cmds = bootstrap.get("commands") or []
        if not isinstance(cmds, list):
            cmds = []
        for c in cmds:
            if not isinstance(c, str) or not c.strip():
                continue
            p = subprocess.run(c, shell=True, text=True, capture_output=True)
            report["commands_run"].append(
                {
                    "cmd": c,
                    "rc": p.returncode,
                    "stdout": (p.stdout or "")[-2000:],
                    "stderr": (p.stderr or "")[-2000:],
                }
            )
        report["bootstrap_applied"] = True
        report["notes"].append("Command bootstrap executed (best-effort).")

    else:
        report["notes"].append("bootstrap.kind is none/unknown; cannot scaffold automatically.")

    # Re-check markers after bootstrap
    report["markers_found_after"] = bool(exists_any(any_of))
    if not report["markers_found_after"]:
        report["notes"].append("Markers still missing after bootstrap.")

    write_preflight_report(report)
    return report


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
    if language in ("dotnet", "csharp") or exists_any(["*.sln", "*.csproj", "**/*.sln", "**/*.csproj"]):
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
    # The request can come from env
    req = {
        "stack": (os.environ.get("STACK") or "").strip(),
        "language": (os.environ.get("LANGUAGE") or "").strip(),
        "test_command": (os.environ.get("TEST_COMMAND") or "").strip(),
    }

    catalog = load_catalog()
    spec = resolve_stack_spec(req, repo_root=".", catalog=catalog)

    # Upgrade pip early
    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], check=False)

    # Preflight bootstrap (data-driven)
    entry = load_stack_entry(catalog, spec.stack_id)
    preflight_bootstrap(spec, entry)

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
