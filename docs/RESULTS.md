# Results (preliminary)

> **Status: run in progress.** A live snapshot from a benchmark run still executing
> — **151 real multi-agent traces** as of 2026-06-29. CrewAI was just re-run clean
> after a harness fix (CrewAI 1.x made LiteLLM optional; see `REHAUL_DIAGNOSTIC.md`)
> and is repopulating, so CrewAI numbers are omitted here. Treat everything below as
> **directional, not final** — and read **[Limitations](../README.md#limitations)** first:
> the detectors are heuristic signals, not human-validated labels.

## Setup

- **Model:** Llama 3.1 8B (Q4_K_M) via Ollama, fully local
- **Hardware:** a single NVIDIA RTX 3050 (6 GB)
- **Cost:** **$0 API** — 151 runs / ~233K content tokens so far, entirely local
- **Frameworks:** CrewAI, AutoGen, LangGraph — identical tasks, same model and conditions
- **Tasks:** 250 (50 per category × 5 categories)

## Task success (real traces only)

| Framework | runs | passed | rate | avg score (0–1) | avg tokens | avg sec |
|---|---|---|---|---|---|---|
| LangGraph | 62 | 50 | 81% | 0.80 | 1,699 | 137 |
| AutoGen | 88 | 33 | 38% | 0.47 | 1,440 | 122 |
| CrewAI | re-running clean | — | — | — | — | — |

> ⚠️ The LangGraph-vs-AutoGen gap is **directional only**. Under an identical *local 8B
> judge*, a linear pipeline (LangGraph) yields more judge-accepted outputs than a
> group-chat (AutoGen) — but how much of that is reliability vs. the 8B judge's leniency
> is unresolved. Do **not** read these as framework rankings.

## Success by difficulty (real)

| difficulty | runs | rate |
|---|---|---|
| easy | 35 | 57% |
| medium | 48 | 60% |
| hard | 44 | 52% |
| adversarial | 24 | 50% |

Notably **flat** — on a local 8B model, judged success barely degrades with task
difficulty (subject to the same judge-leniency caveat).

## Failure-mode signals (heuristic detectors)

Rule-based detector firings — **counts of cheap signals, not validated labels:**

| signal | firings |
|---|---|
| cascading_hallucination | 32 |
| silent_failure | 13 |
| conflicting_outputs | 12 |
| context_degradation | 5 |
| delegation_loop | 1 |
| role_violation | 1 |

`cascading_hallucination` dominates the cheap signals. `resource_exhaustion` no longer
false-fires after the wall-clock-latency fix (see `REHAUL_DIAGNOSTIC.md`); `delegation_loop`
and `role_violation` rarely fire — partly real, partly a known instrumentation limit.

## What this is genuinely good for

- **Feasibility:** a multi-agent failure sweep that normally needs an API budget runs
  **locally at $0** on a consumer GPU (~2 min and ~1.5K tokens per multi-agent task).
- **Cheap detectors catch structure, not meaning:** rule-based checks flag structural
  issues; semantic failures (a fluent, propagated hallucination) need embeddings or an
  LLM judge — and even then it's a signal, not ground truth.

## Limitations

See **[README → Limitations](../README.md#limitations)**. In short: heuristic detectors
(no accuracy claimed), a lenient local 8B judge (inflates pass rates — LangGraph's high
rate especially), a single model, and framework comparison that is directional only. For
the validated taxonomy, see **MAST** (Cemri et al., *"Why Do Multi-Agent LLM Systems
Fail?"*, NeurIPS 2025).

## Regenerating

With the stack up:

```bash
python -m agentfaildb.analysis.compute_metrics   # writes analysis_results/metrics.json
```

Or the real-traces-only snapshot used above:

```sql
SELECT framework, COUNT(*) runs, COUNT(*) FILTER (WHERE task_success) ok,
       ROUND(AVG(task_score)::numeric, 2) avg_score,
       ROUND(AVG(total_content_tokens)::numeric) avg_tokens
FROM traces WHERE total_content_tokens > 0
GROUP BY framework ORDER BY framework;
```
