from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml  # PyYAML

from agent.stacks.spec import StackSpec, ToolchainSpec, CommandSpec, DependencySpec
from agent.stacks.plugins.base import StackPlugin
from agent.stacks.plugins.python_plugin import PythonPlugin
from agent.stacks.plugins.node_plugin import NodePlugin
from agent.stacks.plugins.java_plugin import JavaPlugin
from agent.stacks.plugins.dotnet_plugin import DotnetPlugin
from agent.stacks.plugins.go_plugin import GoPlugin
from agent.stacks.plugins.generic_plugin import GenericPlugin


CATALOG_PATH = Path("agent/stacks/catalog.yml")

# Built-in plugin registry (language/toolchain aware).
PLUGINS: List[StackPlugin] = [
    PythonPlugin(),
    NodePlugin(),
    JavaPlugin(),
    DotnetPlugin(),
    GoPlugin(),
    GenericPlugin(),
]


def load_catalog(path: Path = CATALOG_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def detect_language(repo_root: str = ".") -> Tuple[str, StackPlugin]:
    """Best-effort detection based on repo files. Returns (language, plugin)."""
    best: Tuple[float, str, StackPlugin] = (0.0, "", GenericPlugin())
    for plugin in PLUGINS:
        score, lang = plugin.detect(repo_root)
        if score > best[0]:
            best = (score, lang, plugin)
    return best[1] or "", best[2]


def resolve_stack_spec(
    run_req: Dict[str, Any],
    repo_root: str = ".",
    catalog: Optional[Dict[str, Any]] = None,
) -> StackSpec:
    """Resolve a StackSpec from run request + catalog + repo detection."""
    catalog = catalog if catalog is not None else load_catalog()

    stack_id = (run_req.get("stack") or "").strip()
    req_lang = (run_req.get("language") or "").strip().lower()
    req_test = (run_req.get("test_command") or "").strip()

    # Start with catalog entry if present.
    c = catalog.get(stack_id, {}) if stack_id else {}
    c_lang = (c.get("language") or "").strip().lower()

    # Detect if not given.
    detected_lang, detected_plugin = detect_language(repo_root)

    # Determine final language.
    language = req_lang or c_lang or detected_lang
    # Pick plugin by language (fallback to detected plugin / generic).
    plugin = next((p for p in PLUGINS if p.supports(language)), None) or detected_plugin

    # Toolchain defaults from plugin, overridden by catalog (toolchain.version) if present.
    toolchain_kind = (c.get("toolchain", {}) or {}).get("kind") or plugin.default_toolchain_kind(language)
    toolchain_version = (c.get("toolchain", {}) or {}).get("version") or plugin.default_toolchain_version(language)

    # Commands: prefer explicit request > catalog > plugin defaults
    default_cmds = plugin.default_commands(repo_root, language)
    commands = CommandSpec(
        install=(c.get("commands", {}) or {}).get("install") or default_cmds.install,
        build=(c.get("commands", {}) or {}).get("build") or default_cmds.build,
        test=req_test or (c.get("test_command") or "") or (c.get("commands", {}) or {}).get("test") or default_cmds.test,
        lint=(c.get("commands", {}) or {}).get("lint") or default_cmds.lint,
    )

    # Dependencies from catalog only (plugins shouldn't install big deps implicitly).
    deps = c.get("deps", {}) or {}
    dep_spec = DependencySpec(
        apt=list(deps.get("apt") or []),
        pip=list(deps.get("pip") or []),
        npm=list(deps.get("npm") or []),
    )

    allowed_prefixes = list(c.get("allowed_test_prefixes") or plugin.allowed_test_prefixes(language))

    meta = {}
    meta.update(c.get("meta") or {})
    meta.update(plugin.compute_meta(repo_root, language))

    return StackSpec(
        stack_id=stack_id,
        language=language,
        toolchain=ToolchainSpec(kind=str(toolchain_kind or ""), version=str(toolchain_version or "")),
        commands=commands,
        deps=dep_spec,
        allowed_test_prefixes=allowed_prefixes,
        meta=meta,
    )
