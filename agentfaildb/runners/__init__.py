"""
Framework runner registry for AgentFailDB.
"""

from __future__ import annotations

from agentfaildb.runners.base_runner import BaseRunner

__all__ = ["BaseRunner", "get_runner_class"]

_RUNNER_REGISTRY: dict[str, str] = {
    "crewai": "agentfaildb.runners.crewai_runner.CrewAIRunner",
    "autogen": "agentfaildb.runners.autogen_runner.AutoGenRunner",
    "langgraph": "agentfaildb.runners.langgraph_runner.LangGraphRunner",
    "metagpt": "agentfaildb.runners.metagpt_runner.MetaGPTRunner",
}


def get_runner_class(framework: str) -> type[BaseRunner]:
    """
    Return the runner class for the given framework name.

    Imports are deferred so that missing framework packages only cause
    errors when that specific runner is actually used.
    """
    import importlib

    path = _RUNNER_REGISTRY.get(framework.lower())
    if path is None:
        raise ValueError(
            f"Unknown framework '{framework}'. "
            f"Supported: {sorted(_RUNNER_REGISTRY.keys())}"
        )
    module_path, class_name = path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)
