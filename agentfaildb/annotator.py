"""
LLM-assisted failure annotator for AgentFailDB.

Uses Ollama (or Claude API if ANTHROPIC_API_KEY is set) to annotate traces
for subtle failures that rule-based patterns miss.

Runs annotation_passes times (configurable via settings) and takes a
majority vote on each failure category across passes.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any

import httpx

from agentfaildb.config import settings
from agentfaildb.trace import (
    AnnotationSource,
    FailureAnnotation,
    FailureCategory,
    FailureSeverity,
    TaskTrace,
)

logger = logging.getLogger(__name__)

_OLLAMA_TIMEOUT = 90.0

_CATEGORY_VALUES = [c.value for c in FailureCategory]
_SEVERITY_VALUES = [s.value for s in FailureSeverity]


class LLMAnnotator:
    """
    Uses Ollama or Claude API to annotate traces for failure modes.

    Runs annotation_passes times and takes a majority vote per category
    to reduce noise from single-pass LLM outputs.
    """

    ANNOTATION_PROMPT = """You are an expert evaluator of multi-agent LLM system failures.
Analyse the following multi-agent execution trace and identify any failure modes present.

## Task Description
{task_description}

## Agent Roles
{agent_roles}

## Message Trace
{messages}

## Final Output
{actual_output}

## Failure Categories to Check
1. cascading_hallucination: False claims from one agent adopted and amplified by subsequent agents
2. delegation_loop: Same agents repeatedly delegating back and forth without progress
3. context_degradation: Task context or key information lost as messages propagate
4. conflicting_outputs: Different agents producing mutually contradictory outputs
5. role_violation: Agent performing actions outside its declared role
6. silent_failure: Task failed without any obvious error signal (confident wrong answer)
7. resource_exhaustion: Excessive token usage, time, or message count
8. none: No failure detected

For each category that applies, respond in this EXACT JSON format:
{{
  "annotations": [
    {{
      "category": "<one of the categories above>",
      "severity": "<none|minor|major|critical>",
      "description": "<one sentence explanation>",
      "confidence": <float 0.0 to 1.0>,
      "root_cause_agent": "<agent name or null>",
      "failure_point_index": <message index or null>
    }}
  ]
}}

If no failures are detected, return:
{{"annotations": [{{"category": "none", "severity": "none", "description": "No failure detected", "confidence": 0.9, "root_cause_agent": null, "failure_point_index": null}}]}}

Respond with ONLY the JSON object, no other text.
"""

    def annotate(self, trace: TaskTrace) -> list[FailureAnnotation]:
        """
        Run annotation_passes times and take majority vote per category.

        Returns a consolidated list of FailureAnnotations.
        """
        n_passes = settings.annotation_passes
        all_pass_results: list[list[FailureAnnotation]] = []

        for pass_num in range(n_passes):
            try:
                prompt = self._build_prompt(trace)
                response = self._call_llm(prompt)
                annotations = self._parse_response(response, trace)
                all_pass_results.append(annotations)
                logger.debug(
                    "Annotation pass %d/%d for trace %s: %d annotations",
                    pass_num + 1,
                    n_passes,
                    trace.trace_id,
                    len(annotations),
                )
            except Exception as exc:
                logger.warning("Annotation pass %d failed: %s", pass_num + 1, exc)
                all_pass_results.append([])

        return self._majority_vote(all_pass_results, trace)

    def _build_prompt(self, trace: TaskTrace) -> str:
        """Construct the annotation prompt from trace data."""
        # Format agent roles
        roles_text = "\n".join(
            f"  - {role}: {desc}" for role, desc in trace.agent_roles.items()
        )

        # Format messages (limit to avoid context overflow)
        content_msgs = [m for m in trace.messages if m.message_type.is_content]
        max_msgs = min(len(content_msgs), 10)
        messages_text = ""
        for i, msg in enumerate(content_msgs[:max_msgs]):
            messages_text += (
                f"\n[{i}] {msg.source_agent} → {msg.target_agent} "
                f"({msg.message_type.value}):\n"
                f"{msg.content[:500]}{'...' if len(msg.content) > 500 else ''}\n"
            )
        if len(content_msgs) > max_msgs:
            messages_text += f"\n... ({len(content_msgs) - max_msgs} more messages truncated)"

        actual_output = trace.actual_output[:1000]
        if len(trace.actual_output) > 1000:
            actual_output += "..."

        return self.ANNOTATION_PROMPT.format(
            task_description=trace.task_description[:500],
            agent_roles=roles_text,
            messages=messages_text,
            actual_output=actual_output,
        )

    def _parse_response(
        self, response: str, trace: TaskTrace
    ) -> list[FailureAnnotation]:
        """Parse LLM JSON response into FailureAnnotation objects."""
        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if not json_match:
            logger.warning("No JSON found in LLM annotation response")
            return []

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse annotation JSON: %s", exc)
            return []

        annotations = []
        source = (
            AnnotationSource.LLM_CLAUDE
            if settings.anthropic_api_key
            else AnnotationSource.LLM_OLLAMA
        )

        for item in data.get("annotations", []):
            try:
                category_str = item.get("category", "none")
                if category_str not in _CATEGORY_VALUES:
                    category_str = "none"
                severity_str = item.get("severity", "none")
                if severity_str not in _SEVERITY_VALUES:
                    severity_str = "none"

                confidence = float(item.get("confidence", 0.5))
                confidence = min(1.0, max(0.0, confidence))

                if (
                    confidence < settings.annotation_confidence_threshold
                    and category_str != "none"
                ):
                    continue

                annotation = FailureAnnotation(
                    trace_id=trace.trace_id,
                    category=FailureCategory(category_str),
                    severity=FailureSeverity(severity_str),
                    description=str(item.get("description", ""))[:500],
                    confidence=confidence,
                    source=source,
                    annotator_id=f"llm_{settings.annotation_model}",
                    root_cause_agent=item.get("root_cause_agent"),
                    failure_point_index=item.get("failure_point_index"),
                )
                annotations.append(annotation)
            except Exception as parse_exc:
                logger.warning("Failed to parse annotation item: %s", parse_exc)

        return annotations

    def _majority_vote(
        self,
        all_annotations: list[list[FailureAnnotation]],
        trace: TaskTrace,
    ) -> list[FailureAnnotation]:
        """
        Consolidate multiple annotation passes by majority vote.

        For each failure category, a category is included in the final result
        only if it was detected in more than half the passes.
        For included categories, the annotation with median confidence is returned.
        """
        n_passes = len(all_annotations)
        if n_passes == 0:
            return []

        # Group by category across all passes
        by_category: dict[str, list[FailureAnnotation]] = {}
        for pass_results in all_annotations:
            for ann in pass_results:
                by_category.setdefault(ann.category.value, []).append(ann)

        majority_threshold = n_passes / 2.0
        final: list[FailureAnnotation] = []

        for category_str, anns in by_category.items():
            if len(anns) <= majority_threshold:
                continue  # Not a majority

            if category_str == "none" and any(
                c != "none" for c in by_category.keys() if len(by_category[c]) > majority_threshold
            ):
                continue  # Don't include "none" if real failures exist

            # Pick the annotation with median confidence
            sorted_anns = sorted(anns, key=lambda a: a.confidence)
            median_ann = sorted_anns[len(sorted_anns) // 2]

            # Adjust confidence based on agreement level
            agreement_ratio = len(anns) / n_passes
            adjusted_confidence = min(1.0, median_ann.confidence * (0.7 + 0.3 * agreement_ratio))
            adjusted_ann = median_ann.model_copy(
                update={"confidence": adjusted_confidence, "trace_id": trace.trace_id}
            )
            final.append(adjusted_ann)

        return final

    # ── LLM call dispatch ─────────────────────────────────────────────────────

    def _call_llm(self, prompt: str) -> str:
        """Route to Anthropic Claude or Ollama based on configuration."""
        if settings.anthropic_api_key:
            return self._call_anthropic(prompt)
        return self._call_ollama(prompt)

    def _call_ollama(self, prompt: str) -> str:
        """HTTP call to Ollama /api/generate."""
        base = settings.ollama_base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]

        url = f"{base}/api/generate"
        payload = {
            "model": settings.annotation_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_ctx": settings.ollama_num_ctx,
                "temperature": 0.1,
            },
        }

        with httpx.Client(timeout=_OLLAMA_TIMEOUT) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "")

    def _call_anthropic(self, prompt: str) -> str:
        """Call Anthropic Claude API."""
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": "claude-haiku-20240307",
            "max_tokens": 1024,
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
