from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from agent.stacks.plugins.base import StackPlugin
from agent.stacks.spec import CommandSpec


class NodePlugin(StackPlugin):
    def supports(self, language: str) -> bool:
        return language in ("javascript", "typescript")

    def detect(self, repo_root: str) -> Tuple[float, str]:
        root = Path(repo_root)
        if (root / "package.json").exists():
            if (root / "tsconfig.json").exists() or list(root.glob("**/*.ts")):
                return 0.95, "typescript"
            return 0.95, "javascript"
        if list(root.glob("**/*.ts")):
            return 0.6, "typescript"
        if list(root.glob("**/*.js")):
            return 0.5, "javascript"
        return 0.0, ""

    def default_toolchain_kind(self, language: str) -> str:
        return "node"

    def default_toolchain_version(self, language: str) -> str:
        return "20"

    def default_commands(self, repo_root: str, language: str) -> CommandSpec:
        pm = self._package_manager(Path(repo_root))
        if pm:
            return CommandSpec(test=f"{pm} test")
        return CommandSpec(test="npm test")

    def allowed_test_prefixes(self, language: str) -> list[str]:
        return [
            "npm test",
            "npm run test",
            "pnpm test",
            "pnpm run test",
            "yarn test",
        ]

    def compute_meta(self, repo_root: str, language: str) -> Dict[str, Any]:
        root = Path(repo_root)
        pm = self._package_manager(root) or "npm"
        return {"package_manager": pm}

    @staticmethod
    def _package_manager(root: Path) -> str:
        if (root / "pnpm-lock.yaml").exists():
            return "pnpm"
        if (root / "yarn.lock").exists():
            return "yarn"
        if (root / "package-lock.json").exists():
            return "npm"
        if (root / "package.json").exists():
            return "npm"
        return ""
