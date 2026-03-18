"""
Task registry for AgentFailDB.

Exports ALL_TASKS and helper functions to filter by category and difficulty.
"""

from __future__ import annotations

from agentfaildb.tasks.base_task import BaseTask
from agentfaildb.tasks.collaborative_research import COLLABORATIVE_RESEARCH_TASKS
from agentfaildb.tasks.code_generation import CODE_GENERATION_TASKS
from agentfaildb.tasks.data_analysis import DATA_ANALYSIS_TASKS
from agentfaildb.tasks.debate_reasoning import DEBATE_REASONING_TASKS
from agentfaildb.tasks.planning import PLANNING_TASKS

ALL_TASKS: list[BaseTask] = (
    COLLABORATIVE_RESEARCH_TASKS
    + CODE_GENERATION_TASKS
    + DEBATE_REASONING_TASKS
    + PLANNING_TASKS
    + DATA_ANALYSIS_TASKS
)


def get_tasks_by_category(category: str) -> list[BaseTask]:
    """Return all tasks matching the given task_category string."""
    return [t for t in ALL_TASKS if t.task_category == category]


def get_tasks_by_difficulty(difficulty: str) -> list[BaseTask]:
    """Return all tasks matching the given difficulty string."""
    return [t for t in ALL_TASKS if t.difficulty == difficulty]


def get_task_by_id(task_id: str) -> BaseTask | None:
    """Return a single task by its task_id, or None if not found."""
    for task in ALL_TASKS:
        if task.task_id == task_id:
            return task
    return None


__all__ = [
    "BaseTask",
    "ALL_TASKS",
    "get_tasks_by_category",
    "get_tasks_by_difficulty",
    "get_task_by_id",
    "COLLABORATIVE_RESEARCH_TASKS",
    "CODE_GENERATION_TASKS",
    "DEBATE_REASONING_TASKS",
    "PLANNING_TASKS",
    "DATA_ANALYSIS_TASKS",
]
