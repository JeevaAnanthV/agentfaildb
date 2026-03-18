"""
Base task model for AgentFailDB.

Every task definition inherits from BaseTask and carries its own ground truth,
canonical agent roles, and per-framework role name translations.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from agentfaildb.trace import GroundTruthType


class BaseTask(BaseModel):
    """
    Canonical definition of one benchmark task.

    ground_truth format depends on ground_truth_type:
      - DETERMINISTIC → {"assertions": [{"test": str, "weight": float}], "threshold": float}
      - CLAIM_LIST    → {"claims": [{"claim": str, "weight": float}], "threshold": float}
      - RUBRIC        → {"dimensions": [str, ...], "threshold": float}

    canonical_roles maps role_name → plain-English description of that role's
    responsibilities, independent of any specific framework.

    framework_role_mappings translates canonical role names to the name strings
    used by each supported framework.  Keys must be exactly:
      "crewai", "autogen", "langgraph", "metagpt"
    """

    model_config = ConfigDict(populate_by_name=True, frozen=False)

    task_id: str
    description: str
    difficulty: str  # "easy" | "medium" | "hard" | "adversarial"
    task_category: str
    ground_truth_type: GroundTruthType
    canonical_roles: dict[str, str]  # role_name → description
    framework_role_mappings: dict[str, dict[str, str]]  # framework → {canonical → framework_name}
    ground_truth: dict[str, Any] | None = None
    expected_output_description: str = ""
