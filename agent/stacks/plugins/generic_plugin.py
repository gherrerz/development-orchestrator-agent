from __future__ import annotations

from typing import Tuple

from agent.stacks.plugins.base import StackPlugin
from agent.stacks.spec import CommandSpec


class GenericPlugin(StackPlugin):
    """Fallback when language is unknown."""

    def supports(self, language: str) -> bool:
        return True

    def detect(self, repo_root: str) -> Tuple[float, str]:
        # Low confidence fallback.
        return 0.01, ""

    def default_toolchain_kind(self, language: str) -> str:
        return "generic"

    def default_toolchain_version(self, language: str) -> str:
        return ""

    def default_commands(self, repo_root: str, language: str) -> CommandSpec:
        return CommandSpec(test="")

    def allowed_test_prefixes(self, language: str) -> list[str]:
        return []
