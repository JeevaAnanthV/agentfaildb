"""
Failure pattern detector modules for AgentFailDB.
"""

from __future__ import annotations

from agentfaildb.patterns.base_pattern import BasePattern
from agentfaildb.patterns.cascading_hallucination import CascadingHallucinationPattern
from agentfaildb.patterns.conflicting_outputs import ConflictingOutputsPattern
from agentfaildb.patterns.context_degradation import ContextDegradationPattern
from agentfaildb.patterns.delegation_loop import DelegationLoopPattern
from agentfaildb.patterns.resource_exhaustion import ResourceExhaustionPattern
from agentfaildb.patterns.role_violation import RoleViolationPattern
from agentfaildb.patterns.silent_failure import SilentFailurePattern

__all__ = [
    "BasePattern",
    "DelegationLoopPattern",
    "ResourceExhaustionPattern",
    "RoleViolationPattern",
    "ContextDegradationPattern",
    "ConflictingOutputsPattern",
    "CascadingHallucinationPattern",
    "SilentFailurePattern",
]
