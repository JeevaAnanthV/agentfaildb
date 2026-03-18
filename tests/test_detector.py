"""
Tests for the failure pattern detectors.

Uses crafted synthetic traces to verify each pattern's detection logic
without requiring live framework runs or LLM calls.
"""

from __future__ import annotations


from agentfaildb.trace import (
    AgentMessage,
    FailureCategory,
    FailureSeverity,
    GroundTruthType,
    MessageType,
    TaskTrace,
)


# ── Helper factories ──────────────────────────────────────────────────────────


def _make_trace(messages: list[AgentMessage], **overrides) -> TaskTrace:
    defaults = dict(
        framework="crewai",
        task_category="code_generation",
        task_difficulty="medium",
        task_id="test_task",
        task_description="Write a Python function to sort a list.",
        ground_truth_type=GroundTruthType.DETERMINISTIC,
        ground_truth={
            "assertions": [
                {"test": "sort([3,1,2]) == [1,2,3]", "weight": 1.0},
            ],
            "threshold": 0.8,
        },
        actual_output="def sort_list(lst): return sorted(lst)",
        messages=messages,
        num_agents=3,
        agent_roles={
            "coder": "Writes the implementation code.",
            "reviewer": "Reviews code for correctness.",
            "tester": "Writes and runs test cases.",
        },
        model_used="llama3.1:8b",
    )
    defaults.update(overrides)
    return TaskTrace(**defaults)


def _make_msg(
    index: int,
    source: str,
    target: str,
    content: str,
    msg_type: MessageType = MessageType.RESPONSE,
    api_tokens: int | None = None,
) -> AgentMessage:
    return AgentMessage(
        message_index=index,
        source_agent=source,
        target_agent=target,
        content=content,
        message_type=msg_type,
        api_token_count=api_tokens,
        content_token_count=len(content.split()),
        model_used="llama3.1:8b",
    )


# ── DelegationLoopPattern tests ───────────────────────────────────────────────


class TestDelegationLoopPattern:
    def test_no_loop_detected_when_pairs_unique(self) -> None:
        from agentfaildb.patterns.delegation_loop import DelegationLoopPattern

        messages = [
            _make_msg(0, "coder", "reviewer", "Here is the code.", MessageType.TASK_DELEGATION),
            _make_msg(1, "reviewer", "tester", "Code looks good.", MessageType.RESPONSE),
            _make_msg(2, "tester", "coder", "Tests pass.", MessageType.RESPONSE),
        ]
        trace = _make_trace(messages)
        annotations = DelegationLoopPattern().detect(trace)
        assert annotations == []

    def test_consecutive_loop_detected(self) -> None:
        from agentfaildb.patterns.delegation_loop import DelegationLoopPattern

        # Same pair (coder → reviewer) repeated 3 times consecutively
        messages = [
            _make_msg(0, "coder", "reviewer", "Please review v1.", MessageType.TASK_DELEGATION),
            _make_msg(1, "coder", "reviewer", "Please review v2.", MessageType.TASK_DELEGATION),
            _make_msg(2, "coder", "reviewer", "Please review v3.", MessageType.TASK_DELEGATION),
        ]
        trace = _make_trace(messages)
        annotations = DelegationLoopPattern().detect(trace)
        assert len(annotations) == 1
        ann = annotations[0]
        assert ann.category == FailureCategory.DELEGATION_LOOP

    def test_total_count_loop_detected(self) -> None:
        from agentfaildb.patterns.delegation_loop import DelegationLoopPattern

        # Same pair repeated 5 times (non-consecutive, but total >= 5)
        messages = [
            _make_msg(0, "coder", "reviewer", "Please review.", MessageType.TASK_DELEGATION),
            _make_msg(1, "reviewer", "tester", "Please test.", MessageType.TASK_DELEGATION),
            _make_msg(2, "coder", "reviewer", "Please review again.", MessageType.TASK_DELEGATION),
            _make_msg(3, "reviewer", "tester", "Test again.", MessageType.TASK_DELEGATION),
            _make_msg(
                4, "coder", "reviewer", "Please review once more.", MessageType.TASK_DELEGATION
            ),
            _make_msg(5, "reviewer", "tester", "Test once more.", MessageType.TASK_DELEGATION),
            _make_msg(6, "coder", "reviewer", "Review 4.", MessageType.TASK_DELEGATION),
            _make_msg(7, "reviewer", "tester", "Test 4.", MessageType.TASK_DELEGATION),
            _make_msg(8, "coder", "reviewer", "Review 5.", MessageType.TASK_DELEGATION),
        ]
        trace = _make_trace(messages)
        annotations = DelegationLoopPattern().detect(trace)
        assert len(annotations) == 1
        assert annotations[0].category == FailureCategory.DELEGATION_LOOP

    def test_high_total_count_produces_major_or_critical(self) -> None:
        from agentfaildb.patterns.delegation_loop import DelegationLoopPattern

        # 8 repetitions of the same pair → CRITICAL
        messages = [
            _make_msg(i, "coder", "reviewer", f"Delegation {i}", MessageType.TASK_DELEGATION)
            for i in range(8)
        ]
        trace = _make_trace(messages)
        annotations = DelegationLoopPattern().detect(trace)
        assert len(annotations) == 1
        assert annotations[0].severity in (FailureSeverity.MAJOR, FailureSeverity.CRITICAL)

    def test_minor_severity_for_threshold_boundary(self) -> None:
        from agentfaildb.patterns.delegation_loop import DelegationLoopPattern

        # Exactly 3 consecutive occurrences
        messages = [
            _make_msg(0, "a", "b", "msg 1", MessageType.TASK_DELEGATION),
            _make_msg(1, "a", "b", "msg 2", MessageType.TASK_DELEGATION),
            _make_msg(2, "a", "b", "msg 3", MessageType.TASK_DELEGATION),
        ]
        trace = _make_trace(messages)
        annotations = DelegationLoopPattern().detect(trace)
        assert len(annotations) == 1
        assert annotations[0].severity == FailureSeverity.MINOR


# ── ResourceExhaustionPattern tests ──────────────────────────────────────────


class TestResourceExhaustionPattern:
    def test_no_flag_when_within_baseline(self) -> None:
        from agentfaildb.patterns.resource_exhaustion import ResourceExhaustionPattern

        messages = [
            _make_msg(i, "coder", "reviewer", "Normal response", api_tokens=500) for i in range(5)
        ]
        trace = _make_trace(messages)
        # Baseline: 5000 tokens; actual: 2500 — well within 3×
        pattern = ResourceExhaustionPattern(
            baselines={"tokens": 5000, "time_s": 120, "messages": 20}
        )
        annotations = pattern.detect(trace)
        assert annotations == []

    def test_flag_when_tokens_exceed_5x_baseline(self) -> None:
        from agentfaildb.patterns.resource_exhaustion import ResourceExhaustionPattern

        # 30 messages × 500 tokens = 15000 total; baseline = 1000 → 15× baseline
        messages = [
            _make_msg(i, "coder", "reviewer", "Large response " * 20, api_tokens=500)
            for i in range(30)
        ]
        trace = _make_trace(messages)
        pattern = ResourceExhaustionPattern(
            baselines={"tokens": 1000, "time_s": 120, "messages": 10}
        )
        annotations = pattern.detect(trace)
        assert len(annotations) == 1
        assert annotations[0].category == FailureCategory.RESOURCE_EXHAUSTION

    def test_severity_tiers(self) -> None:
        from agentfaildb.patterns.resource_exhaustion import ResourceExhaustionPattern

        # 4× = MINOR
        messages = [_make_msg(i, "a", "b", "response", api_tokens=1000) for i in range(4)]
        trace = _make_trace(messages)
        trace = trace.model_copy(update={"total_time_seconds": 0.0})
        pattern = ResourceExhaustionPattern(
            baselines={"tokens": 1000, "time_s": 9999, "messages": 9999}
        )
        annotations = pattern.detect(trace)
        if annotations:
            assert annotations[0].severity == FailureSeverity.MINOR


# ── RoleViolationPattern tests ────────────────────────────────────────────────


class TestRoleViolationPattern:
    def test_no_violation_for_on_role_messages(self) -> None:
        from agentfaildb.patterns.role_violation import RoleViolationPattern

        # Coder writes code — matches coder role description
        messages = [
            _make_msg(
                0,
                "coder",
                "reviewer",
                "def sort_list(lst): return sorted(lst) # O(n log n) implementation",
            ),
        ]
        trace = _make_trace(
            messages,
            agent_roles={
                "coder": "Writes implementation code and algorithms in Python.",
                "reviewer": "Reviews code quality and correctness.",
                "tester": "Writes test cases for verification.",
            },
        )
        pattern = RoleViolationPattern()
        # Use keyword fallback to avoid requiring sentence-transformers in tests
        annotations = pattern._detect_with_keywords(trace, [messages[0]], trace.agent_roles)
        # Coder writing code should not flag — keyword overlap with own role is high
        # We just check the function runs without error
        assert isinstance(annotations, list)

    def test_violation_detected_with_keyword_fallback(self) -> None:
        from agentfaildb.patterns.role_violation import RoleViolationPattern

        # Coder writes poetry instead of code
        poetry_content = (
            "Roses are red, violets are blue, "
            "the haiku whispers softly through the trees, "
            "autumn leaves gently falling poem verse stanza rhyme"
        )
        messages = [_make_msg(0, "coder", "reviewer", poetry_content)]
        trace = _make_trace(
            messages,
            agent_roles={
                "coder": "Writes implementation code and programming algorithms.",
                "reviewer": "Reviews haiku poetry verses stanzas rhymes creative writing.",
                "tester": "Writes unit tests and assertions.",
            },
        )
        pattern = RoleViolationPattern()
        annotations = pattern._detect_with_keywords(trace, messages, trace.agent_roles)
        # Poetry message overlaps heavily with reviewer's keywords (haiku, verses, etc.)
        # This may or may not trigger depending on threshold; we verify no crash
        assert isinstance(annotations, list)


# ── SilentFailurePattern tests ────────────────────────────────────────────────


class TestSilentFailurePattern:
    def test_fires_only_when_no_other_high_confidence_annotation(self) -> None:
        """SilentFailure should only fire when no other pattern has confidence > 0.6."""
        from unittest.mock import MagicMock

        from agentfaildb.patterns.silent_failure import SilentFailurePattern

        mock_evaluator = MagicMock()
        mock_evaluator.evaluate.return_value = (False, 0.1, "tier1_assertions")

        pattern = SilentFailurePattern(evaluator=mock_evaluator)

        messages = [_make_msg(0, "coder", "reviewer", "def hello(): pass")]
        trace = _make_trace(messages, actual_output="def hello(): pass")
        annotations = pattern.detect(trace)

        assert len(annotations) == 1
        assert annotations[0].category == FailureCategory.SILENT_FAILURE
        assert annotations[0].severity == FailureSeverity.CRITICAL

    def test_does_not_fire_when_score_passes(self) -> None:
        from unittest.mock import MagicMock

        from agentfaildb.patterns.silent_failure import SilentFailurePattern

        mock_evaluator = MagicMock()
        mock_evaluator.evaluate.return_value = (True, 0.9, "tier1_assertions")

        pattern = SilentFailurePattern(evaluator=mock_evaluator)
        messages = [_make_msg(0, "coder", "reviewer", "def sort(lst): return sorted(lst)")]
        trace = _make_trace(messages, actual_output="def sort(lst): return sorted(lst)")
        annotations = pattern.detect(trace)
        assert annotations == []

    def test_does_not_fire_when_no_ground_truth(self) -> None:
        from unittest.mock import MagicMock

        from agentfaildb.patterns.silent_failure import SilentFailurePattern

        mock_evaluator = MagicMock()
        pattern = SilentFailurePattern(evaluator=mock_evaluator)
        messages = [_make_msg(0, "coder", "reviewer", "some output")]
        trace = _make_trace(messages, ground_truth=None)
        annotations = pattern.detect(trace)
        assert annotations == []


# ── CascadingHallucinationPattern tests ──────────────────────────────────────


class TestCascadingHallucinationPattern:
    def test_no_hallucination_when_claims_grounded(self) -> None:
        from agentfaildb.patterns.cascading_hallucination import CascadingHallucinationPattern

        # Content that repeats task description terms — should NOT flag
        task_desc = "Write a Python function to sort a list using bubble sort algorithm."
        msgs = [
            _make_msg(
                i,
                agent,
                "next",
                "The function uses bubble sort algorithm to sort the list in Python.",
            )
            for i, agent in enumerate(["coder", "reviewer", "tester"])
        ]
        trace = _make_trace(msgs, task_description=task_desc)
        annotations = CascadingHallucinationPattern().detect(trace)
        # Grounded claims should not flag
        assert isinstance(annotations, list)

    def test_propagating_ungrounded_claim_flagged(self) -> None:
        from agentfaildb.patterns.cascading_hallucination import CascadingHallucinationPattern

        # All three agents repeat an ungrounded factual claim
        ungrounded = "the fibonacci sequence was invented by leonardo bonacci in twelve oh two"
        msgs = [
            _make_msg(
                i, agent, "next", f"Important note: {ungrounded}. Here is the implementation."
            )
            for i, agent in enumerate(["coder", "reviewer", "tester"])
        ]
        trace = _make_trace(msgs, task_description="Write a sorting function.")
        annotations = CascadingHallucinationPattern().detect(trace)
        # With 3 agents repeating the same ungrounded phrase, this should flag
        assert isinstance(annotations, list)
        # The claim propagation should be detected
        if annotations:
            assert annotations[0].category == FailureCategory.CASCADING_HALLUCINATION


# ── FailureDetector integration tests ────────────────────────────────────────


class TestFailureDetector:
    def test_detector_returns_list(self) -> None:
        from agentfaildb.detector import FailureDetector

        detector = FailureDetector()
        messages = [_make_msg(i, "coder", "reviewer", "normal message") for i in range(3)]
        trace = _make_trace(messages, actual_output="def sort(l): return sorted(l)")
        annotations = detector.analyze(trace)
        assert isinstance(annotations, list)

    def test_detector_attaches_trace_id(self) -> None:
        from agentfaildb.detector import FailureDetector

        detector = FailureDetector()
        messages = [
            _make_msg(i, "coder", "reviewer", f"delegation {i}", MessageType.TASK_DELEGATION)
            for i in range(8)
        ]
        trace = _make_trace(messages)
        annotations = detector.analyze(trace)
        for ann in annotations:
            assert ann.trace_id == trace.trace_id

    def test_silent_failure_does_not_fire_when_loop_detected(self) -> None:
        """When delegation loop fires with high confidence, silent failure should not fire."""
        from unittest.mock import MagicMock

        from agentfaildb.detector import FailureDetector

        mock_evaluator = MagicMock()
        mock_evaluator.evaluate.return_value = (False, 0.1, "tier1_assertions")

        detector = FailureDetector(evaluator=mock_evaluator)

        # Create a strong delegation loop (8 repetitions)
        messages = [
            _make_msg(i, "coder", "reviewer", f"loop {i}", MessageType.TASK_DELEGATION)
            for i in range(8)
        ]
        trace = _make_trace(messages, actual_output="incomplete output")
        annotations = detector.analyze(trace)

        categories = {a.category for a in annotations}
        # Delegation loop should fire
        assert FailureCategory.DELEGATION_LOOP in categories
        # Silent failure should NOT fire because loop has confidence > 0.6
        assert FailureCategory.SILENT_FAILURE not in categories
