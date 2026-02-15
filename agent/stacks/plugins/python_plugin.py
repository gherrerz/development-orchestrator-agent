from __future__ import annotations

from pathlib import Path
from typing import Tuple

from agent.stacks.plugins.base import StackPlugin
from agent.stacks.spec import CommandSpec


class PythonPlugin(StackPlugin):
    def supports(self, language: str) -> bool:
        return language == "python"

    def detect(self, repo_root: str) -> Tuple[float, str]:
        root = Path(repo_root)
        if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists():
            return 0.9, "python"
        if list(root.glob("**/*.py")):
            return 0.6, "python"
        return 0.0, ""

    def default_toolchain_kind(self, language: str) -> str:
        return "python"

    def default_toolchain_version(self, language: str) -> str:
        return "3.11"

    def default_commands(self, repo_root: str, language: str) -> CommandSpec:
        root = Path(repo_root)
        if (root / "pyproject.toml").exists():
            return CommandSpec(test="python -m pytest -q")
        if (root / "requirements.txt").exists() or list(root.glob("**/*.py")):
            return CommandSpec(test="pytest -q")
        return CommandSpec(test="")

    def allowed_test_prefixes(self, language: str) -> list[str]:
        return [
            "pytest",
            "python -m pytest",
        ]
