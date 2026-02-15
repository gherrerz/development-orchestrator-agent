from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from agent.stacks.plugins.base import StackPlugin
from agent.stacks.spec import CommandSpec


class JavaPlugin(StackPlugin):
    def supports(self, language: str) -> bool:
        return language == "java"

    def detect(self, repo_root: str) -> Tuple[float, str]:
        root = Path(repo_root)
        if (root / "pom.xml").exists() or (root / "gradlew").exists():
            return 0.95, "java"
        if list(root.glob("**/*.java")):
            return 0.6, "java"
        return 0.0, ""

    def default_toolchain_kind(self, language: str) -> str:
        return "java"

    def default_toolchain_version(self, language: str) -> str:
        return "21"

    def default_commands(self, repo_root: str, language: str) -> CommandSpec:
        root = Path(repo_root)
        if (root / "pom.xml").exists():
            return CommandSpec(test="mvn -q test", build="mvn -q -DskipTests package")
        if (root / "gradlew").exists():
            return CommandSpec(test="./gradlew -q test", build="./gradlew -q assemble")
        return CommandSpec(test="mvn -q test")

    def allowed_test_prefixes(self, language: str) -> list[str]:
        return [
            "mvn test",
            "mvn -q test",
            "./gradlew test",
            "./gradlew -q test",
        ]

    def compute_meta(self, repo_root: str, language: str) -> Dict[str, Any]:
        root = Path(repo_root)
        build_tool = "maven" if (root / "pom.xml").exists() else ("gradle" if (root / "gradlew").exists() else "")
        return {"java_build_tool": build_tool}
