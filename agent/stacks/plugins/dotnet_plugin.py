from __future__ import annotations

from pathlib import Path
from typing import Tuple

from agent.stacks.plugins.base import StackPlugin
from agent.stacks.spec import CommandSpec


class DotnetPlugin(StackPlugin):
    def supports(self, language: str) -> bool:
        return language in ("dotnet", "csharp")

    def detect(self, repo_root: str) -> Tuple[float, str]:
        root = Path(repo_root)
        if list(root.glob("**/*.sln")) or list(root.glob("**/*.csproj")):
            return 0.95, "dotnet"
        if list(root.glob("**/*.cs")):
            return 0.6, "dotnet"
        return 0.0, ""

    def default_toolchain_kind(self, language: str) -> str:
        return "dotnet"

    def default_toolchain_version(self, language: str) -> str:
        return "8.0.x"

    def default_commands(self, repo_root: str, language: str) -> CommandSpec:
        return CommandSpec(test="dotnet test", build="dotnet build")

    def allowed_test_prefixes(self, language: str) -> list[str]:
        return ["dotnet test"]
