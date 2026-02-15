from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Tuple

from agent.stacks.spec import CommandSpec


class StackPlugin(ABC):
    @abstractmethod
    def supports(self, language: str) -> bool:
        ...

    @abstractmethod
    def detect(self, repo_root: str) -> Tuple[float, str]:
        """Return (confidence 0..1, language)."""
        ...

    @abstractmethod
    def default_toolchain_kind(self, language: str) -> str:
        ...

    @abstractmethod
    def default_toolchain_version(self, language: str) -> str:
        ...

    @abstractmethod
    def default_commands(self, repo_root: str, language: str) -> CommandSpec:
        ...

    @abstractmethod
    def allowed_test_prefixes(self, language: str) -> list[str]:
        ...

    def compute_meta(self, repo_root: str, language: str) -> Dict[str, Any]:
        return {}
