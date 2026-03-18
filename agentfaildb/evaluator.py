"""
Ground truth evaluator for AgentFailDB.

Supports three evaluation tiers:
  Tier 1 (DETERMINISTIC) — assertion-based checks on actual_output
  Tier 2 (CLAIM_LIST)    — LLM-assisted claim coverage via Ollama
  Tier 3 (RUBRIC)        — LLM-scored rubric dimensions via Ollama

All LLM calls use httpx to talk to the Ollama /api/generate endpoint.
If ANTHROPIC_API_KEY is set, Tier 2/3 calls fall back to the Claude API.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from agentfaildb.config import settings
from agentfaildb.trace import GroundTruthType, TaskTrace

logger = logging.getLogger(__name__)

_OLLAMA_TIMEOUT = 120.0


class GroundTruthEvaluator:
    """
    Evaluates a completed TaskTrace against its ground truth definition.

    Returns a 3-tuple: (task_success: bool, task_score: float, method_description: str)
    """

    def evaluate(self, trace: TaskTrace) -> tuple[bool, float, str]:
        """
        Dispatch to the appropriate tier evaluator based on ground_truth_type.

        Returns (task_success, task_score, method_description).
        """
        if trace.ground_truth is None:
            logger.warning("Trace %s has no ground_truth; returning neutral result", trace.trace_id)
            return True, 0.5, "no_ground_truth"

        gtt = trace.ground_truth_type
        actual = trace.actual_output

        # For CLAIM_LIST evaluation, use all message content (concatenated) so
        # that claims spread across researcher/writer messages are not missed.
        # The actual_output is the last agent's output (often a reviewer comment)
        # which may not contain the original claims verbatim.
        if trace.messages:
            from agentfaildb.config import settings as _s  # noqa: PLC0415
            content_types = _s.content_message_types()
            full_context = "\n\n".join(
                m.content for m in trace.messages
                if m.message_type.value in content_types and m.content
            )
            eval_text = full_context if full_context else actual
        else:
            eval_text = actual

        if gtt == GroundTruthType.DETERMINISTIC:
            passed, score = self._evaluate_tier1(actual, trace.ground_truth)
            method = "tier1_assertions"
        elif gtt == GroundTruthType.CLAIM_LIST:
            passed, score = self._evaluate_tier2(eval_text, trace.ground_truth)
            method = "tier2_claim_coverage"
        elif gtt == GroundTruthType.RUBRIC:
            passed, score = self._evaluate_tier3(eval_text, trace.ground_truth)
            method = "tier3_rubric"
        else:
            logger.warning("Unknown ground truth type %s", gtt)
            return True, 0.5, "unknown_type"

        return passed, score, method

    # ── Tier 1: Deterministic assertion checks ────────────────────────────────

    def _evaluate_tier1(
        self, actual_output: str, ground_truth: dict[str, Any]
    ) -> tuple[bool, float]:
        """
        Run assertion checks against actual_output.

        Each assertion has a "test" (description) and a "weight".
        We use LLM to judge whether each assertion is satisfied, since
        actual Python eval is unsafe for arbitrary agent outputs.
        """
        assertions: list[dict[str, Any]] = ground_truth.get("assertions", [])
        threshold: float = ground_truth.get("threshold", 0.8)

        if not assertions:
            return True, 1.0

        total_weight = sum(a.get("weight", 1.0) for a in assertions)
        if total_weight == 0:
            return True, 1.0

        weighted_score = 0.0
        for assertion in assertions:
            test_description = assertion.get("test", "")
            weight = assertion.get("weight", 1.0)
            passed = self._check_assertion(actual_output, test_description)
            if passed:
                weighted_score += weight

        score = weighted_score / total_weight
        return score >= threshold, score

    def _check_assertion(self, actual_output: str, assertion_description: str) -> bool:
        """
        Use Ollama to judge whether an assertion is satisfied by the actual_output.

        Returns True if the assertion passes, False otherwise.
        """
        prompt = (
            "You are an automated test evaluator. Your job is to determine whether "
            "the following assertion is satisfied by the provided output.\n\n"
            f"ASSERTION: {assertion_description}\n\n"
            f"OUTPUT:\n{actual_output[:3000]}\n\n"
            "Respond with EXACTLY one word: PASS or FAIL.\n"
            "PASS means the output satisfies the assertion.\n"
            "FAIL means the output does not satisfy the assertion.\n"
            "Response:"
        )
        try:
            response = self._call_ollama(prompt)
            return "PASS" in response.upper()
        except Exception as exc:
            logger.warning("Assertion check failed: %s", exc)
            return False

    # ── Tier 2: Claim coverage ────────────────────────────────────────────────

    def _evaluate_tier2(
        self, actual_output: str, ground_truth: dict[str, Any]
    ) -> tuple[bool, float]:
        """
        Check claim coverage via LLM.

        Each claim is judged independently; weighted coverage score is computed.
        """
        claims: list[dict[str, Any]] = ground_truth.get("claims", [])
        threshold: float = ground_truth.get("threshold", 0.6)

        if not claims:
            return True, 1.0

        total_weight = sum(c.get("weight", 1.0) for c in claims)
        if total_weight == 0:
            return True, 1.0

        weighted_covered = 0.0
        for claim_item in claims:
            claim_text = claim_item.get("claim", "")
            weight = claim_item.get("weight", 1.0)
            covered = self._check_claim_coverage(actual_output, claim_text)
            if covered:
                weighted_covered += weight

        score = weighted_covered / total_weight
        return score >= threshold, score

    def _check_claim_coverage(self, actual_output: str, claim: str) -> bool:
        """
        Use Ollama to judge whether a specific claim is covered in the output.

        Falls back to keyword matching if Ollama times out or fails.
        """
        prompt = (
            "You are evaluating whether a specific claim or fact is addressed in a piece of text.\n\n"
            f"CLAIM TO CHECK: {claim}\n\n"
            f"TEXT TO EVALUATE:\n{actual_output[:4000]}\n\n"
            "Does the text above address or contain this claim?\n"
            "Respond with EXACTLY one word: YES or NO.\n"
            "YES means the claim is present or addressed in the text.\n"
            "NO means the claim is absent from the text.\n"
            "Response:"
        )
        try:
            response = self._call_ollama(prompt)
            return "YES" in response.upper()
        except Exception as exc:
            logger.warning("Claim coverage check failed: %s — using keyword fallback", exc)
            return self._keyword_claim_fallback(actual_output, claim)

    def _keyword_claim_fallback(self, actual_output: str, claim: str) -> bool:
        """
        Fallback claim check using keyword overlap when LLM is unavailable.

        Extracts content words from the claim (ignoring stop words) and checks
        whether at least half of them appear in the output.  Conservative but
        non-zero: prevents a slow Ollama from scoring everything as 0.
        """
        _STOP = {
            "a", "an", "the", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "shall", "can",
            "of", "in", "on", "at", "to", "for", "and", "or", "but",
            "not", "with", "from", "by", "as", "that", "this", "it",
            "its", "than", "their", "they", "them", "over", "above",
            "into", "between", "compared", "during", "two", "key",
        }
        claim_words = [
            w.lower().strip(".,;:!?\"'")
            for w in claim.split()
            if w.lower().strip(".,;:!?\"'") not in _STOP and len(w) > 2
        ]
        if not claim_words:
            return True  # nothing meaningful to check

        output_lower = actual_output.lower()
        matched = sum(1 for w in claim_words if w in output_lower)
        coverage = matched / len(claim_words)
        logger.debug(
            "Keyword fallback: %d/%d claim words matched (%.0f%%)",
            matched, len(claim_words), coverage * 100,
        )
        # Require at least 50% keyword match to count as covered
        return coverage >= 0.5

    # ── Tier 3: Rubric scoring ────────────────────────────────────────────────

    def _evaluate_tier3(
        self, actual_output: str, ground_truth: dict[str, Any]
    ) -> tuple[bool, float]:
        """
        Score rubric dimensions via LLM.

        Each dimension is scored 1–5. Average score is compared to threshold.
        """
        dimensions: list[str] = ground_truth.get("dimensions", [])
        threshold: float = ground_truth.get("threshold", 3.0)

        if not dimensions:
            return True, 5.0

        scores: list[float] = []
        for dimension in dimensions:
            score = self._score_rubric_dimension(actual_output, dimension)
            scores.append(score)

        avg_score = sum(scores) / len(scores)
        return avg_score >= threshold, avg_score

    def _score_rubric_dimension(self, actual_output: str, dimension: str) -> float:
        """
        Use Ollama to score a single rubric dimension on a 1–5 scale.
        """
        dimension_descriptions = {
            "argument_coherence": "How logically consistent and well-structured are the arguments?",
            "evidence_usage": "How well does the output use evidence, examples, or data to support claims?",
            "balance": "How fairly does the output represent multiple perspectives or sides?",
            "resolution": "How satisfying and complete is the conclusion or synthesis?",
        }
        desc = dimension_descriptions.get(
            dimension, f"How well does the output perform on {dimension}?"
        )

        prompt = (
            f"You are evaluating a piece of text on the dimension of '{dimension}'.\n"
            f"Dimension description: {desc}\n\n"
            f"TEXT TO EVALUATE:\n{actual_output[:4000]}\n\n"
            f"Score this text on '{dimension}' using a scale of 1 to 5:\n"
            "1 = Very poor\n"
            "2 = Poor\n"
            "3 = Acceptable\n"
            "4 = Good\n"
            "5 = Excellent\n\n"
            "Respond with EXACTLY one integer from 1 to 5.\n"
            "Score:"
        )
        try:
            response = self._call_ollama(prompt)
            # Extract first digit found in response
            match = re.search(r"[1-5]", response)
            if match:
                return float(match.group())
            logger.warning("Could not parse rubric score from response: %r", response)
            return 3.0
        except Exception as exc:
            logger.warning("Rubric dimension scoring failed: %s", exc)
            return 3.0

    # ── Ollama HTTP client ────────────────────────────────────────────────────

    def _call_ollama(self, prompt: str) -> str:
        """
        Make an HTTP call to Ollama's /api/generate endpoint.

        Uses settings.ollama_base_url (strips /v1 suffix if present).
        Falls back to Anthropic Claude API if ANTHROPIC_API_KEY is set.
        """
        if settings.anthropic_api_key:
            return self._call_anthropic(prompt)

        base = settings.ollama_base_url.rstrip("/")
        # Ollama /api/generate uses a different base than the OpenAI-compatible /v1
        if base.endswith("/v1"):
            base = base[:-3]

        url = f"{base}/api/generate"
        payload = {
            "model": settings.annotation_model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_ctx": settings.ollama_num_ctx, "temperature": 0},
        }

        with httpx.Client(timeout=_OLLAMA_TIMEOUT) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "")

    def _call_anthropic(self, prompt: str) -> str:
        """
        Call Anthropic Claude API as a fallback when ANTHROPIC_API_KEY is set.
        """
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": "claude-haiku-20240307",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": prompt}],
        }
        with httpx.Client(timeout=_OLLAMA_TIMEOUT) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("content", [])
            if content and isinstance(content, list):
                return content[0].get("text", "")
            return ""
