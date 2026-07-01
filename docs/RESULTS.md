# Results

> **Complete run.** Full 250 × 3 matrix — **750 real multi-agent traces**, finished
> 2026-07-01, entirely local at **$0 API cost**. Read **[Limitations](../README.md#limitations)**
> first: the detectors are heuristic signals, not human-validated labels, and success is
> scored by a local 8B judge (treat framework gaps as directional). For the validated
> taxonomy see **MAST** (Cemri et al., NeurIPS 2025).

## Setup

- **Model:** Llama 3.1 8B (Q4_K_M) via Ollama, fully local
- **Hardware:** a single NVIDIA RTX 3050 (6 GB)
- **Cost:** **$0 API** — 750 runs / **~1.06M content tokens** generated locally
- **Frameworks:** CrewAI, AutoGen, LangGraph — identical tasks, same model and conditions
- **Tasks:** 250 (50 per category × 5 categories), 250 runs per framework

## Task success by framework

| Framework | runs | passed | success rate | avg score (0–1) | avg tokens | avg sec |
|---|---|---|---|---|---|---|
| CrewAI | 250 | 208 | **83%** | 0.78 | 863 | 182 |
| LangGraph | 250 | 207 | **83%** | 0.79 | 1,834 | 130 |
| AutoGen | 250 | 127 | **51%** | 0.51 | 1,548 | 156 |

> ⚠️ Framework gaps are **directional** — success is judged by a local 8B model, and a
> linear pipeline (LangGraph) / sequential crew (CrewAI) yields more judge-accepted output
> than a group-chat (AutoGen). How much is genuine reliability vs. judge behavior is
> unresolved. Not framework rankings.

**Notable:** CrewAI hits the same 83% as LangGraph on **less than half the tokens** (863 vs
1,834 avg) — the most token-efficient of the three here.

## Success by difficulty

| difficulty | runs | rate |
|---|---|---|
| easy | 150 | 74% |
| medium | 225 | 75% |
| hard | 225 | 74% |
| adversarial | 150 | 65% |

Strikingly **flat** — on a local 8B model, judged success barely tracks the intended
difficulty gradient (only adversarial dips). Likely a mix of judge leniency and the 8B
model failing "easy" and "hard" tasks at similar rates.

## Success by category

| category | rate |
|---|---|
| debate_reasoning | 99% |
| planning | 83% |
| collaborative_research | 72% |
| code_generation | 55% |
| data_analysis | 51% |

The real spread is **by task type, not difficulty**: open-ended reasoning/planning pass
easily, while code generation and data analysis (which need correct, checkable output) are
where the 8B multi-agent systems actually struggle.

## Failure-mode signals (heuristic detectors)

Rule-based detector firings across the 750 traces — **cheap signals, not validated labels:**

| signal | firings |
|---|---|
| cascading_hallucination | 149 |
| silent_failure | 64 |
| context_degradation | 32 |
| conflicting_outputs | 31 |
| role_violation | 17 |
| delegation_loop | 2 |

`cascading_hallucination` dominates. `resource_exhaustion` no longer appears (the
wall-clock-latency false-positive was fixed — see `REHAUL_DIAGNOSTIC.md`);
`delegation_loop` barely fires (partly real, partly an instrumentation limit on
linear/sequential runners).

## What this is genuinely good for

- **Feasibility:** a complete 750-run, 3-framework failure sweep runs **locally at $0** on
  a 6 GB consumer GPU (~130–180 s and ~0.9–1.8K tokens per multi-agent task).
- **Cheap detectors catch structure, not meaning:** rule-based checks flag structural
  issues; semantic failures (a fluent, propagated hallucination) need embeddings or an LLM
  judge — and even then it's a signal, not ground truth.
- **Where local 8B agents break:** code generation and data analysis, not open-ended
  reasoning — useful to know before deploying small-model agents on correctness-critical work.

## Limitations

See **[README → Limitations](../README.md#limitations)**: heuristic detectors (no accuracy
claimed), a local 8B judge (framework/difficulty rates are directional), a single model,
and no human-validated ground truth. For validated labels, use **MAST**.

## Regenerating

With the stack up:

```bash
python -m agentfaildb.analysis.compute_metrics   # writes analysis_results/metrics.json
```

Or the real-traces-only snapshot used here:

```sql
SELECT framework, COUNT(*) runs, COUNT(*) FILTER (WHERE task_success) ok,
       ROUND(AVG(task_score)::numeric, 2) avg_score,
       ROUND(AVG(total_content_tokens)::numeric) avg_tokens
FROM traces WHERE total_content_tokens > 0
GROUP BY framework ORDER BY framework;
```
