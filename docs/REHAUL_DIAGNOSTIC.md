# AgentFailDB — Rehaul Diagnostic

A validity audit of the current system, grounded in the data actually sitting in PostgreSQL (757 traces, 306 annotations) and the code that produced it. Every finding cites the file:line responsible and what it breaks for **(P)aper** and **(Prod)uct**.

Read order: fix the **Tier-0 blockers** before re-running anything. Everything downstream (metrics, leaderboard, paper, product) is invalid until these are fixed.

---

## TL;DR severity table

| # | Problem | Evidence | Breaks | Effort |
|---|---------|----------|--------|--------|
| 1 | No ground truth — 100% of labels are the detector's own output | `SELECT source FROM annotations` → all `rule_based` | P (fatal) | High |
| 2 | `--annotate` is dead code — annotator never wired into the main run | `orchestrator.py:537-539` | P | Low |
| 3 | Cross-framework detection bias is an instrumentation artifact | langgraph `silent_failure`=61 vs crewai=3 | P (fatal), Prod | High |
| 4 | Tier-3 rubric eval returns the fallback constant every time | 149/150 runs scored exactly 3.00 | P, Prod | Med |
| 5 | `task_score` not normalized across tiers (0–1 vs 1–5) | langgraph avg "1.041" | P, Prod | Low |
| 6 | Token accounting absent → `resource_exhaustion` driven by GPU latency | `total_api_tokens` ≈ 0 everywhere | P, Prod | Med |
| 7 | `delegation_loop` / `role_violation` structurally cannot fire | 1 each across 757 runs | P | Med |
| 8 | Silent-failure runs the LLM evaluator twice per trace | `silent_failure.py:66` | Prod (cost) | Low |
| 9 | MetaGPT half-present and broken | 4 runs, score 0 | P, Prod | Low (delete) |

---

## Tier 0 — Validity blockers (fix first)

### 1. There is no ground truth (the missing research core)

**Evidence.** Every one of the 306 annotations has `source = 'rule_based'`. Zero `llm_ollama`, zero `llm_claude`, zero `human`. The "failure database" is currently "whatever the heuristics flagged," with no measure of whether those flags are correct.

**Why it's fatal for a paper.** Your own blueprint (`docs/agentfaildb-blueprint.md:572-613`) states the requirement: report inter-annotator agreement (Cohen's Kappa) between LLM/human labels, and precision/recall/F1 per failure mode. None exists. Without a human-labeled validation set there is no way to claim the detectors *detect failures* — only that they *emit annotations*.

**Fix.**
1. Hand-label a stratified sample of **120–150 traces** (balanced across the 3 frameworks × 5 categories × pass/fail). Store as `source='human'` annotations.
2. Run the LLM annotator (see #2) and a Claude-judge pass.
3. Compute Kappa (human vs LLM) and precision/recall/F1 (rule-based vs human) per category. This is the spine of both the paper and the product's credibility claim.

### 2. `--annotate` is dead — the LLM annotator was never wired in

**Root cause.** `agentfaildb/harness/orchestrator.py:537-539` builds the orchestrator with **no annotator**:
```python
detector = FailureDetector(redis_client=redis_client)
evaluator = GroundTruthEvaluator()
orchestrator = Orchestrator(db=db, detector=detector, evaluator=evaluator)  # annotator defaults to None
```
So in `run_task_on_framework`, the guard `if annotate and self.annotator is not None` (`orchestrator.py:167`) is always False. Even running with `--annotate` produces nothing — which is exactly why the DB has zero LLM annotations despite a 297-line `annotator.py` existing.

**Fix.** Instantiate the annotator in `__main__` (pointed at Claude via `ANTHROPIC_API_KEY`, not local 8B) and pass it in. This is a ~3-line change that unblocks finding #1.

### 3. Cross-framework detection bias is an instrumentation artifact (the most dangerous bug)

**Evidence.** The same detector fires wildly differently by framework:

| detector | langgraph | crewai | autogen |
|---|---|---|---|
| silent_failure | 61 | 3 | 2 |
| cascading_hallucination | 49 | 0 | 9 |
| resource_exhaustion | 61 | 62 | 45 |

A 20–30× gap for the *same* detector is not a property of the frameworks — it's a property of **how each runner records messages**. The headline finding the whole project is built on ("framework X fails more than Y") would be measuring your logging code, not the frameworks.

**Root causes — each runner emits structurally different traces:**
- **CrewAI** tags agent "thoughts" as `INTERNAL_REASONING` (`crewai_runner.py:105-107`), which the detector filters out by default (`detector.py:92-96`). So most CrewAI content never reaches the content-based detectors → near-zero `cascading_hallucination`.
- **LangGraph** tags *everything* as `RESPONSE` (`langgraph_runner.py:81-88`) — no reasoning/delegation distinction — so detectors see a clean stream of "responses" and trip more often.
- **LangGraph graph is strictly linear** (`langgraph_runner.py:172-177`: `add_edge` chain → `END`). Loops are structurally impossible, so `delegation_loop` can never fire for it.
- **CrewAI** assigns `source`/`target` by `step_index % n_agents` (`crewai_runner.py:96-100`) — synthetic routing, not real delegation. Any delegation/role detector reads fiction.
- **AutoGen** uses yet another split, tagging manager/terminate messages as `SYSTEM_CONTROL` (`autogen_runner.py:143-149`).

**Why it's fatal.** Cross-framework comparison is invalid until a "message" means the same thing in every runner. A reviewer will catch this immediately; a customer comparing frameworks would be misled.

**Fix.** Define a **runner output contract** and conform all three to it:
- Same taxonomy of message types, applied by the same rules.
- Either capture `INTERNAL_REASONING` for all frameworks or none — be consistent about what the detectors see.
- Real source/target where the framework exposes it; explicitly mark it `unknown` where it doesn't (never fabricate round-robin).
- Add a runner-conformance test that asserts, for a fixed task, that the *shape* of the trace (message-type distribution, content/artifact split) is comparable across runners.
- Re-run, then confirm `silent_failure` no longer differs 20× by framework. If it still does after normalization, *that* is a real, publishable finding.

---

## Tier 1 — Metric correctness

### 4. Tier-3 rubric evaluation is non-functional

**Evidence.** Of 150 rubric-scored runs, **149 scored exactly 3.00** and one scored 3.75.

**Root cause.** `evaluator.py:299-336` (`_score_rubric_dimension`) asks local `llama3.1:8b` for an integer 1–5 and **returns `3.0` whenever it can't parse one** (lines 332-336, plus the except at 334-336). The 8B model isn't returning parseable single-integer scores, so the default fires ~100% of the time. The rubric dimension is pure noise.

**Fix.** (a) Route the judge to Claude via the existing `_call_anthropic` path (`evaluator.py:369-391`); (b) constrain output (e.g. force a JSON `{"score": N}` and validate); (c) log parse-failure rate so silent defaulting can't recur; (d) until fixed, **exclude tier-3 from any reported metric** rather than reporting 3.0s.

### 5. `task_score` is not on a common scale

**Evidence.** `tier1_assertions` and `tier2_claim_coverage` range 0–1; `tier3_rubric` ranges 1–5. Averaging them (leaderboard/dashboard) yields nonsense like langgraph "avg 1.041".

**Root cause.** Tiers return native scales (`evaluator.py:_evaluate_tier1/2` → 0–1; `_evaluate_tier3` → 1–5 at line 296-297) and nothing normalizes before aggregation.

**Fix.** Normalize at the source: divide rubric score by 5.0 so every `task_score` is 0–1, OR store a separate normalized column. Then any `AVG(task_score)` is meaningful. One-line change in the evaluator plus a one-time DB backfill.

### 6. Token accounting is effectively absent → `resource_exhaustion` measures GPU latency

**Evidence.** `total_api_tokens` is ~0 across the board, so the token branch of `resource_exhaustion` (`resource_exhaustion.py:89-94`) never fires. Detection then falls to **time** and **raw message count**.

**Root causes.**
- Only LangGraph even attempts to read API tokens (`langgraph_runner.py:76-79`), and Ollama's OpenAI-compat endpoint often omits `usage`, so it's `None`. CrewAI and AutoGen never set `api_token_count` at all.
- `tiktoken` backfills `content_token_count` (`base_runner.py:142-149`) but `context_overhead_ratio` (`trace.py:204-216`) divides by zero-ish API tokens → 0.
- The time baseline used online is the Redis default **120s** (`db.py:343-347`) because <20 samples exist, while real runs hit the **600s** timeout → ratio 5× → flagged `MAJOR`. So "resource exhaustion" is largely "the GPU was slow / the run timed out."
- `resource_exhaustion.py:105-111` counts `len(trace.messages)` — **including artifact messages** — so the message-count signal is instrumentation-dependent (see #3).

**Fix.** (a) Capture real token usage per message in every runner (or compute consistently with tiktoken for *all* of them and stop pretending it's API usage); (b) base resource-exhaustion on **tokens and message count relative to a per-task baseline**, not wall-clock (wall-clock is hardware-bound and not portable); (c) count `content_messages`, not all messages.

---

## Tier 2 — Taxonomy support

### 7. `delegation_loop` and `role_violation` can't fire

**Evidence.** 1 annotation each across 757 runs — two of the marquee "multi-agent" failure modes.

**Root cause.** Partly real (these tasks rarely loop), partly structural: LangGraph is linear (loops impossible, #3), and CrewAI's synthetic routing (#3) means delegation/role signals are reading fabricated source/target pairs. The detectors may be fine; the *traces don't contain the signal*.

**Implication for the paper.** A 7-category taxonomy where 4 categories barely appear is not well-supported by the data. Either (a) design tasks/runners that genuinely exercise loops and role boundaries (multi-round review tasks, delegation-enabled agents), or (b) narrow the taxonomy to the modes you can actually evidence and measure. Don't ship a taxonomy the data doesn't back.

### 8. Silent-failure double-evaluates (cost + drift)

**Root cause.** `silent_failure.py:63-66` calls `evaluator.evaluate(trace)` again, after the orchestrator already evaluated and stored a score (`orchestrator.py:148`). Two LLM evaluation passes per trace = double cost and a chance of disagreeing scores.

**Fix.** Pass the already-computed score into the detector instead of re-evaluating.

### 9. MetaGPT is dead weight

4 runs, score 0, dropped from `pyproject.toml` but still in the runner registry (`runners/__init__.py:15`) and the docs. Delete the runner, the registry entry, and the DB rows, or it pollutes every aggregate.

---

## What's genuinely salvageable (the good news)

- The **data model** (`trace.py`) is sound and frameworks-agnostic — keep it; it's also the right interface for ingesting *external* traces (see PRODUCT_SPEC).
- The **pipeline** (orchestrate → evaluate → detect → store), **checkpoint/resume**, and **Docker stack** are solid and reusable.
- A **full benchmark run exists** — once the Tier-0/1 fixes land, a re-run is a few hundred tasks, not a from-scratch effort.
- The **detectors are a real asset** once they read normalized traces and are validated against ground truth — that's the product's engine.

---

## Re-run plan (after fixes)

1. Land Tier-0 (#1–3) and Tier-1 (#4–6) fixes.
2. Wipe and re-run on **normalized runners** with the **Claude judge** for eval + annotation. Consider running the under-test agents on a named API model (gpt-4o-mini / claude-haiku) for reproducibility — "llama3.1:8b on a GTX 1650" is a weak, unreproducible setup for a paper.
3. Hand-label the validation set; compute Kappa + P/R/F1.
4. Only then compute metrics / leaderboard / paper tables.

Estimated at 10–15 hrs/week: **~5–7 weeks** to a valid dataset + detection metrics.
