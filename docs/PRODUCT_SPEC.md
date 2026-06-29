# AgentFailDB — Product Spec

How the benchmark becomes income. Companion to `REHAUL_DIAGNOSTIC.md` (which makes the detection engine *valid*) — this doc turns that engine into something people pay for.

Constraints this is designed around: ~10–15 hrs/week, goals = paper + product + job credibility. So the plan favors a **revenue surface that doubles as a portfolio piece and shares code with the paper**, not a separate startup.

---

## 1. Positioning

**One-liner:** *Sentry for multi-agent AI — catch the failure modes (cascading hallucination, delegation loops, silent wrong answers, runaway cost) that generic LLM-eval tools miss.*

**The wedge.** The market (LangSmith, Langfuse, Arize Phoenix, Braintrust, AgentOps, Galileo) does tracing + generic evals well. None ship a **validated, multi-agent-specific failure taxonomy with detectors**. That taxonomy + the precision/recall numbers from the paper are the differentiator and the moat. You are not competing on tracing UI; you are competing on *"we can tell you, with measured accuracy, which of 7 named failure modes your agent system hit and why."*

**Why benchmark + product reinforce each other:**
- The open benchmark + leaderboard = top-of-funnel marketing and credibility.
- The paper = the proof that the detectors actually work (precision/recall) — this is the sales claim.
- The product = the detectors run on the customer's own traces.

---

## 2. Three packagings (easiest-revenue-first)

### Tier A — Reliability Audit (service; revenue this month)
**What:** A fixed-scope engagement: the customer points you at their agent system (or shares traces); you run it through the harness + detectors and deliver a **Failure Report** — which modes fired, where, severity, cost impact, and concrete fixes.
**Deliverable:** a 10–15 page PDF/notebook: per-failure-mode findings with trace excerpts, a framework/cost comparison, prioritized remediations.
**Price:** $2–10k per engagement.
**Why first:** zero product-polish required (the harness already does this), it pays for the rest, and — critically — it tells you *which failure modes customers actually care about*, so you build the right thing. Each audit also becomes an anonymized case study and more labeled data for the paper.
**Effort:** low — mostly already built. Needs a report template + a BYO-trace path (§4).

### Tier B — CI / Regression Gate (product; clearest fit)
**What:** "pytest for agent reliability." A GitHub Action + small cloud service: on each PR, run the agent against a failure-mode suite, diff against the baseline, and **block the merge on a reliability regression** (e.g. new delegation loop, silent-failure rate up, cost per task +30%).
**Deliverable:** `agentfaildb-ci` GitHub App + a hosted dashboard of trend lines per repo.
**Price:** seat/repo SaaS — ~$50–200/repo/mo; free tier for OSS (funnel).
**Why:** recurring revenue, natural pairing with the open benchmark, and it's the most defensible "developer tool" story for recruiters.
**Effort:** medium — needs the engine hardened (diagnostic Phase 0–1) + a thin CI wrapper + baseline storage.

### Tier C — Production Observability (biggest, hardest)
**What:** continuous monitoring of live agent traces (ingest from OTel GenAI / LangSmith export), alerting on failure modes in production.
**Price:** usage-based, $X per million spans, like the incumbents.
**Why caution:** this is the crowded, well-funded arena. Only enter once the paper gives you a defensible "we detect things they don't" claim and Tier A/B have proven demand. Don't start here.

**Recommended sequence:** A (now, funds everything) → B (the product) → C (only if A/B prove demand).

---

## 3. MVP definition (what to actually build first)

Given the time budget, the MVP is **Tier A's engine + a Tier B skeleton**, sharing one core:

1. **Hardened detector engine** (from `REHAUL_DIAGNOSTIC.md` Phase 0–1): normalized traces, fixed scoring, validated precision/recall. *Non-negotiable — without this the report's claims aren't trustworthy.*
2. **BYO-trace ingestion** (§4): turn an external agent trace into a `TaskTrace` so the detectors run on customer data, not just your benchmark tasks.
3. **Report generator**: `TaskTrace[] → Failure Report` (Markdown/HTML/PDF) — reuse the leaderboard/metrics code.
4. **CLI**: `agentfaildb scan traces.jsonl --report out.html` — the same command powers an audit and (wrapped) the CI gate.

That's the whole MVP. Tier B = wrap #4 in a GitHub Action + store baselines. Tier C = stream #2 continuously.

---

## 4. BYO-trace ingestion (the key technical unlock)

Today the detectors only see traces your runners produce. To sell, they must run on **the customer's** traces. Good news: `trace.py`'s `TaskTrace`/`AgentMessage` model is already the right neutral target — you just need adapters *into* it.

**Adapters to build (priority order):**
1. **OpenTelemetry GenAI spans** — the emerging standard; `gen_ai.*` span attributes map cleanly to `AgentMessage` (source/target = span/parent, content = input/output, tokens = `gen_ai.usage.*`). Framework-agnostic → widest reach.
2. **LangSmith / LangGraph run export** — huge installed base; their run tree → messages.
3. **A 5-line SDK** — `from agentfaildb import trace_agent` context manager the customer drops into their app to emit `AgentMessage`s directly. Lowest-friction for Tier A pilots.

**Design note tied to the diagnostic:** §3 of `REHAUL_DIAGNOSTIC.md` (the instrumentation-bias bug) is the *same problem* in product form — a "message" must mean the same thing regardless of source. Fix it once in the runner contract and the ingestion adapters inherit it. The detectors must also degrade gracefully when a field (real source/target, token counts) is absent in customer data.

---

## 5. Pricing summary

| Tier | Model | Price | When |
|---|---|---|---|
| A — Audit | per engagement | $2–10k | now |
| B — CI gate | per repo / seat | $50–200/repo/mo (free OSS tier) | after Phase 0–1 |
| C — Observability | usage (per M spans) | market rate | only if A/B validate |

Open benchmark, dataset, leaderboard, and the `agentfaildb` detector library stay **free/OSS** — they are the funnel and the paper's artifact, not the product.

---

## 6. Go-to-market (first 5 customers)

- **Who hurts most:** teams running multi-agent systems in production — customer-support agents, coding agents, research/analrysis pipelines, RAG-with-agents. They already feel loops, hallucination cascades, and runaway cost.
- **Where they are:** LangChain/CrewAI/AutoGen Discords, r/LLMDevs, the "I shipped agents and they're flaky" crowd on X/LinkedIn.
- **The hook:** publish the benchmark finding ("X% of multi-agent runs hit ≥1 failure mode; here's the breakdown by framework") → it travels → inbound for audits. This is the original blueprint's LinkedIn arc, but now pointed at *selling audits* instead of just visibility.
- **First audits:** offer 2–3 free/cheap audits to teams with real systems in exchange for a testimonial + permission to use anonymized findings. Converts to paid + case studies + paper data.

---

## 7. How this ties back to the paper and the job goal

- **Paper:** the validated detectors (precision/recall vs. human labels) ARE the product's core claim. Writing the paper *is* building the product's credibility. One body of work, two outputs.
- **Job credibility:** "published a paper on multi-agent reliability + shipped an OSS detector library + ran paid audits" is a far stronger signal than any one of those alone. It's the exact profile AI-infra teams hire.
- **Sequencing with the rehaul:** Diagnostic Phase 0–1 (validity + ground truth) is the shared prerequisite. Do it once; it unlocks the paper, the audit report's credibility, and the CI product simultaneously.

---

## 8. Reality check / risks

- **Don't out-build the incumbents on tracing/UI** — you'll lose. Stay on the failure-taxonomy wedge.
- **A dataset/benchmark alone is ~$0** — revenue is the audit/tool, full stop.
- **Selling needs proof** — ship Phase 0–1 before charging, or the report's numbers won't survive scrutiny.
- **Time:** at 10–15 hrs/wk, this is a ~3–4 month arc to "valid engine + first paid audit + workshop paper submitted," not weeks. Sequence ruthlessly; don't gold-plate (see: the dashboard).
