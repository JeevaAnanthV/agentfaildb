"""
Tests for the GroundTruthEvaluator.

LLM/Ollama calls are mocked so these tests run offline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentfaildb.evaluator import GroundTruthEvaluator
from agentfaildb.trace import GroundTruthType, TaskTrace


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_trace(
    ground_truth_type: GroundTruthType,
    ground_truth: dict,
    actual_output: str,
) -> TaskTrace:
    return TaskTrace(
        framework="crewai",
        task_category="test",
        task_difficulty="easy",
        task_id="test_001",
        task_description="Test task description",
        ground_truth_type=ground_truth_type,
        ground_truth=ground_truth,
        actual_output=actual_output,
        num_agents=2,
        agent_roles={"coder": "writes code", "reviewer": "reviews code"},
        model_used="llama3.1:8b",
    )


# ── Tier 1: Deterministic assertion tests ─────────────────────────────────────


class TestTier1Evaluation:
    def test_all_assertions_pass_returns_high_score(self) -> None:
        evaluator = GroundTruthEvaluator()
        # Mock _check_assertion to always return True (pass)
        evaluator._check_assertion = MagicMock(return_value=True)

        gt = {
            "assertions": [
                {"test": "sort([3,1,2]) == [1,2,3]", "weight": 0.5},
                {"test": "sort([]) == []", "weight": 0.5},
            ],
            "threshold": 0.8,
        }
        trace = _make_trace(GroundTruthType.DETERMINISTIC, gt, "def sort(l): return sorted(l)")
        success, score, method = evaluator.evaluate(trace)

        assert success is True
        assert score == pytest.approx(1.0)
        assert method == "tier1_assertions"

    def test_all_assertions_fail_returns_zero_score(self) -> None:
        evaluator = GroundTruthEvaluator()
        evaluator._check_assertion = MagicMock(return_value=False)

        gt = {
            "assertions": [
                {"test": "sort([3,1,2]) == [1,2,3]", "weight": 1.0},
            ],
            "threshold": 0.8,
        }
        trace = _make_trace(GroundTruthType.DETERMINISTIC, gt, "wrong output")
        success, score, method = evaluator.evaluate(trace)

        assert success is False
        assert score == pytest.approx(0.0)

    def test_partial_pass_score_weighted_correctly(self) -> None:
        evaluator = GroundTruthEvaluator()
        # First assertion passes, second fails
        evaluator._check_assertion = MagicMock(side_effect=[True, False])

        gt = {
            "assertions": [
                {"test": "assertion_1", "weight": 0.6},
                {"test": "assertion_2", "weight": 0.4},
            ],
            "threshold": 0.8,
        }
        trace = _make_trace(GroundTruthType.DETERMINISTIC, gt, "partial output")
        success, score, method = evaluator.evaluate(trace)

        assert score == pytest.approx(0.6)
        assert success is False  # 0.6 < threshold 0.8

    def test_empty_assertions_returns_success(self) -> None:
        evaluator = GroundTruthEvaluator()
        gt = {"assertions": [], "threshold": 0.8}
        trace = _make_trace(GroundTruthType.DETERMINISTIC, gt, "any output")
        success, score, method = evaluator.evaluate(trace)
        assert success is True
        assert score == pytest.approx(1.0)


# ── Tier 2: Claim coverage tests ──────────────────────────────────────────────


class TestTier2Evaluation:
    def test_all_claims_covered_returns_success(self) -> None:
        evaluator = GroundTruthEvaluator()
        evaluator._check_claim_coverage = MagicMock(return_value=True)

        gt = {
            "claims": [
                {"claim": "uses bubble sort", "weight": 0.4},
                {"claim": "handles empty list", "weight": 0.3},
                {"claim": "returns sorted result", "weight": 0.3},
            ],
            "threshold": 0.6,
        }
        trace = _make_trace(
            GroundTruthType.CLAIM_LIST,
            gt,
            "The function uses bubble sort and handles empty lists, returning sorted results.",
        )
        success, score, method = evaluator.evaluate(trace)

        assert success is True
        assert score == pytest.approx(1.0)
        assert method == "tier2_claim_coverage"

    def test_no_claims_covered_returns_failure(self) -> None:
        evaluator = GroundTruthEvaluator()
        evaluator._check_claim_coverage = MagicMock(return_value=False)

        gt = {
            "claims": [
                {"claim": "solid state battery", "weight": 0.5},
                {"claim": "lithium electrolyte", "weight": 0.5},
            ],
            "threshold": 0.6,
        }
        trace = _make_trace(
            GroundTruthType.CLAIM_LIST,
            gt,
            "This output contains nothing relevant.",
        )
        success, score, method = evaluator.evaluate(trace)

        assert success is False
        assert score == pytest.approx(0.0)

    def test_partial_claim_coverage_computes_weighted_score(self) -> None:
        evaluator = GroundTruthEvaluator()
        # Only first claim covered
        evaluator._check_claim_coverage = MagicMock(side_effect=[True, False])

        gt = {
            "claims": [
                {"claim": "claim A", "weight": 0.7},
                {"claim": "claim B", "weight": 0.3},
            ],
            "threshold": 0.6,
        }
        trace = _make_trace(GroundTruthType.CLAIM_LIST, gt, "only covers A")
        success, score, method = evaluator.evaluate(trace)

        assert score == pytest.approx(0.7)
        assert success is True  # 0.7 >= threshold 0.6

    def test_empty_claims_returns_success(self) -> None:
        evaluator = GroundTruthEvaluator()
        gt = {"claims": [], "threshold": 0.6}
        trace = _make_trace(GroundTruthType.CLAIM_LIST, gt, "any output")
        success, score, method = evaluator.evaluate(trace)
        assert success is True
        assert score == pytest.approx(1.0)


# ── Tier 3: Rubric scoring tests ──────────────────────────────────────────────


class TestTier3Evaluation:
    def test_high_scores_return_success(self) -> None:
        evaluator = GroundTruthEvaluator()
        # All dimensions score 4
        evaluator._score_rubric_dimension = MagicMock(return_value=4.0)

        gt = {
            "dimensions": ["argument_coherence", "evidence_usage", "balance", "resolution"],
            "threshold": 3.0,
        }
        trace = _make_trace(
            GroundTruthType.RUBRIC,
            gt,
            "A well-argued essay with evidence on both sides and clear resolution.",
        )
        success, score, method = evaluator.evaluate(trace)

        assert success is True
        assert score == pytest.approx(4.0)
        assert method == "tier3_rubric"

    def test_low_scores_return_failure(self) -> None:
        evaluator = GroundTruthEvaluator()
        evaluator._score_rubric_dimension = MagicMock(return_value=1.5)

        gt = {
            "dimensions": ["argument_coherence", "evidence_usage"],
            "threshold": 3.0,
        }
        trace = _make_trace(GroundTruthType.RUBRIC, gt, "Weak output.")
        success, score, method = evaluator.evaluate(trace)

        assert success is False
        assert score == pytest.approx(1.5)

    def test_mixed_scores_average_correctly(self) -> None:
        evaluator = GroundTruthEvaluator()
        # 2 dimensions: one scores 5, one scores 2 → average 3.5
        evaluator._score_rubric_dimension = MagicMock(side_effect=[5.0, 2.0])

        gt = {
            "dimensions": ["argument_coherence", "evidence_usage"],
            "threshold": 3.0,
        }
        trace = _make_trace(GroundTruthType.RUBRIC, gt, "Mixed quality output.")
        success, score, method = evaluator.evaluate(trace)

        assert score == pytest.approx(3.5)
        assert success is True  # 3.5 >= threshold 3.0

    def test_empty_dimensions_returns_max_score(self) -> None:
        evaluator = GroundTruthEvaluator()
        gt = {"dimensions": [], "threshold": 3.0}
        trace = _make_trace(GroundTruthType.RUBRIC, gt, "any output")
        success, score, method = evaluator.evaluate(trace)
        assert success is True
        assert score == pytest.approx(5.0)


# ── No ground truth tests ─────────────────────────────────────────────────────


class TestNoGroundTruth:
    def test_no_ground_truth_returns_neutral(self) -> None:
        evaluator = GroundTruthEvaluator()
        trace = _make_trace(GroundTruthType.DETERMINISTIC, {}, "some output")
        # Override ground_truth to None
        trace = trace.model_copy(update={"ground_truth": None})
        success, score, method = evaluator.evaluate(trace)
        assert success is True
        assert score == pytest.approx(0.5)
        assert method == "no_ground_truth"


# ── Ollama call tests (with mock) ─────────────────────────────────────────────


class TestOllamaCall:
    def test_check_assertion_parses_pass(self) -> None:
        evaluator = GroundTruthEvaluator()
        with patch.object(evaluator, "_call_ollama", return_value="PASS"):
            result = evaluator._check_assertion("output", "sort([1,2,3]) == [1,2,3]")
        assert result is True

    def test_check_assertion_parses_fail(self) -> None:
        evaluator = GroundTruthEvaluator()
        with patch.object(evaluator, "_call_ollama", return_value="FAIL"):
            result = evaluator._check_assertion("wrong output", "sort([1,2,3]) == [1,2,3]")
        assert result is False

    def test_check_claim_coverage_parses_yes(self) -> None:
        evaluator = GroundTruthEvaluator()
        with patch.object(evaluator, "_call_ollama", return_value="YES"):
            result = evaluator._check_claim_coverage("the text mentions batteries", "batteries")
        assert result is True

    def test_check_claim_coverage_parses_no(self) -> None:
        evaluator = GroundTruthEvaluator()
        with patch.object(evaluator, "_call_ollama", return_value="NO"):
            result = evaluator._check_claim_coverage("unrelated text", "batteries")
        assert result is False

    def test_score_rubric_dimension_parses_integer(self) -> None:
        evaluator = GroundTruthEvaluator()
        with patch.object(evaluator, "_call_ollama", return_value="4"):
            result = evaluator._score_rubric_dimension("good essay", "argument_coherence")
        assert result == pytest.approx(4.0)

    def test_score_rubric_dimension_fallback_on_bad_response(self) -> None:
        evaluator = GroundTruthEvaluator()
        with patch.object(evaluator, "_call_ollama", return_value="excellent"):
            result = evaluator._score_rubric_dimension("good essay", "argument_coherence")
        assert result == pytest.approx(3.0)  # fallback default

    def test_check_assertion_handles_exception(self) -> None:
        evaluator = GroundTruthEvaluator()
        with patch.object(evaluator, "_call_ollama", side_effect=Exception("network error")):
            result = evaluator._check_assertion("output", "test")
        assert result is False  # graceful fallback
