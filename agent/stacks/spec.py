from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ToolchainSpec:
    kind: str = ""         # python|node|java|dotnet|go|generic
    version: str = ""      # e.g. "3.11", "20", "21", "8.0.x", "1.22.x"


@dataclass
class CommandSpec:
    install: str = ""      # optional, informational
    build: str = ""        # optional, informational
    test: str = ""         # primary test command used by orchestrator
    lint: str = ""         # optional, informational


@dataclass
class DependencySpec:
    apt: List[str] = field(default_factory=list)
    pip: List[str] = field(default_factory=list)
    npm: List[str] = field(default_factory=list)


@dataclass
class MarkerSpec:
    """
    Build/config markers used to detect if a repo is initialized for a stack.
    any_of: if none of these patterns exist, bootstrap may run.
    """
    any_of: List[str] = field(default_factory=list)


@dataclass
class BootstrapTemplate:
    path: str
    content: str


@dataclass
class BootstrapSpec:
    """
    Bootstrap strategy to scaffold minimal build files if missing markers.
    kind:
      - "none": do nothing
      - "template": write templates if missing
      - "command": run idempotent commands (best-effort)
    """
    kind: str = "none"  # none|template|command
    templates: List[BootstrapTemplate] = field(default_factory=list)
    commands: List[str] = field(default_factory=list)


@dataclass
class StackSpec:
    """Resolved spec used across extract_request, stack_setup and orchestrator.

    This is designed to be *data-driven*: most stack differences live in catalog.yml.
    Plugins provide safe defaults and repo detection per language/toolchain.
    """

    stack_id: str = ""
    language: str = ""               # python|javascript|typescript|java|dotnet|csharp|go|...
    toolchain: ToolchainSpec = field(default_factory=ToolchainSpec)
    commands: CommandSpec = field(default_factory=CommandSpec)
    deps: DependencySpec = field(default_factory=DependencySpec)

    # Preflight bootstrap
    markers: MarkerSpec = field(default_factory=MarkerSpec)
    bootstrap: BootstrapSpec = field(default_factory=BootstrapSpec)

    # Security: allowlist for test command prefixes (no shell features).
    allowed_test_prefixes: List[str] = field(default_factory=list)

    # Extra metadata (package manager, build tool, etc.)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_env(self) -> Dict[str, str]:
        return {
            "STACK": self.stack_id,
            "LANGUAGE": self.language,
            "TEST_COMMAND": self.commands.test,
            "INSTALL_COMMAND": self.commands.install,
            "BUILD_COMMAND": self.commands.build,
            "LINT_COMMAND": self.commands.lint,
            "TOOLCHAIN_KIND": self.toolchain.kind,
            "TOOLCHAIN_VERSION": self.toolchain.version,
            "ALLOWED_TEST_PREFIXES": "|".join(self.allowed_test_prefixes or []),
            "META_JSON": "",
        }
