from __future__ import annotations

from pathlib import Path
from typing import Tuple

from agent.stacks.plugins.base import StackPlugin
from agent.stacks.spec import CommandSpec


class GoPlugin(StackPlugin):
    def supports(self, language: str) -> bool:
        return language == "go"

    def detect(self, repo_root: str) -> Tuple[float, str]:
        root = Path(repo_root)
        if (root / "go.mod").exists():
            return 0.95, "go"
        if list(root.glob("**/*.go")):
            return 0.6, "go"
        return 0.0, ""

    def default_toolchain_kind(self, language: str) -> str:
        return "go"

    def default_toolchain_version(self, language: str) -> str:
        return "1.22.x"

    def default_commands(self, repo_root: str, language: str) -> CommandSpec:
        return CommandSpec(test="go test ./...")

    def allowed_test_prefixes(self, language: str) -> list[str]:
        return [
            "go test ./...",
            "go test ./... -v",
            "go test ./... -count=1",
        ]
