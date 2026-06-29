# AgentFailDB

**An open, fully-local reproduction and extension of multi-agent LLM failure analysis — zero API cost on Llama 3.1 8B via Ollama, with a lightweight rule-based detection layer.**

AgentFailDB runs identical tasks across multiple multi-agent frameworks (CrewAI, AutoGen, LangGraph), captures every inter-agent message into a normalized trace, and applies cheap rule-based detectors for common failure patterns — all running locally on a single consumer GPU at no API cost.

## Relation to prior work

The canonical, validated taxonomy of multi-agent LLM failures already exists:
**MAST** — Cemri et al., *"Why Do Multi-Agent LLM Systems Fail?"* (NeurIPS 2025) —
which defines **14 failure modes across 7 frameworks with human-annotated ground truth**.
MAST is the reference. AgentFailDB does **not** claim to supersede, replace, or
"be the first" anything — MAST owns that lane.

AgentFailDB is a complementary, practical thing: a **fully-local, zero-cost reproduction
harness** for studying how these failures show up on small open models you can run
yourself. The question it actually chases is *how far cheap, local models can be pushed
toward frontier-grade reliability* — not "what is the taxonomy of failures."

## Why This Exists

Multi-agent LLM systems fail in ways single-agent systems don't — cascading
hallucinations, delegation loops, context degradation across handoffs, and
confident-but-wrong outputs. Studying these usually means paid API calls across many
frameworks. AgentFailDB makes that reproducible on a single consumer GPU at zero API
cost, so anyone can poke at it without a budget.

## Failure Taxonomy

| # | Category | Description |
|---|----------|-------------|
| 1 | **Cascading Hallucination** | One agent hallucinates, downstream agents build on it |
| 2 | **Infinite Delegation Loop** | Agents pass tasks back and forth without progress |
| 3 | **Context Degradation** | Information is lost or corrupted across agent handoffs |
| 4 | **Conflicting Agent Outputs** | Agents produce contradictory results for the same input |
| 5 | **Role Boundary Violation** | Agents act outside their assigned roles |
| 6 | **Silent Confident Failure** | Agents produce wrong output with no indication of uncertainty |
| 7 | **Resource Exhaustion** | Excessive token usage, time, or message count |

> These are coarse, rule-detectable categories — a simplification of MAST's finer,
> human-validated 14-mode taxonomy, chosen so they can be flagged cheaply without a
> human annotator. See **Limitations**.

## Architecture

```
Task Definitions (250 tasks: 50 per category × 5 categories)
    ↓
Framework Runners (CrewAI, AutoGen, LangGraph)
    ↓
Normalized Execution Traces (TaskTrace → PostgreSQL)
    ↓
Rule-based Detection Engine (7 heuristic detectors; optional LLM annotator)
    ↓
Analysis → leaderboard / exportable dataset
```

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.11+
- NVIDIA GPU (optional, for faster inference)

### Setup

```bash
# Clone
git clone https://github.com/JeevaAnanthV/agentfaildb.git
cd agentfaildb

# Configure
cp .env.example .env
# Edit .env with your port mappings if needed

# Start infrastructure
docker compose up -d

# Install (core only — fast)
pip install -e .

# Install with ML detection support
pip install -e ".[ml]"

# Install with framework runners
pip install -e ".[frameworks]"

# Install everything
pip install -e ".[full]"

# Run tests
pip install -e ".[dev]"
pytest
```

### Run a Benchmark

```bash
# Single task smoke test
python -m agentfaildb.harness.orchestrator \
  --frameworks langgraph \
  --category collaborative_research \
  --difficulty easy \
  --count 1

# Full benchmark (runs all tasks on all frameworks)
python -m agentfaildb.harness.orchestrator

# Check progress
python -m agentfaildb.harness.orchestrator --status

# Resilient auto-resume runner (survives restarts)
./agentfaildb-runner.sh
```

### Launch the Leaderboard

```bash
pip install -e ".[analysis]"
streamlit run agentfaildb/leaderboard/app.py
```

### Export Dataset

```bash
python -m agentfaildb.analysis.export_hf_dataset --split 80/20 --out-dir data/hf_export
```

## Project Structure

```
agentfaildb/
├── trace.py              # Core data model (Pydantic v2)
├── config.py             # Settings (pydantic-settings)
├── detector.py           # Failure detection engine
├── annotator.py          # LLM-assisted annotation
├── evaluator.py          # Ground truth evaluation (3-tier)
├── metrics.py            # Statistical analysis
├── runners/              # Framework-specific runners
│   ├── base_runner.py    # Abstract base with timeout/retry
│   ├── crewai_runner.py
│   ├── autogen_runner.py
│   └── langgraph_runner.py
├── tasks/                # 50 benchmark tasks
├── patterns/             # 7 failure pattern detectors
├── harness/              # Orchestrator, DB, API, trace collector
├── analysis/             # HuggingFace export, metrics computation
└── leaderboard/          # Streamlit dashboard
```

## Infrastructure

- **LLM**: Ollama with llama3.1:8b (local, no API costs)
- **Database**: PostgreSQL for trace storage
- **Cache**: Redis for pattern signatures and checkpoints
- **Detection**: Rule-based patterns + sentence-transformers embeddings + LLM annotation

## Limitations

Read these before drawing conclusions from anything here:

- **The detectors are heuristic signals, not validated labels.** They are cheap
  rule-based (plus optional embedding) checks. There is **no human-annotated ground
  truth** in this repo, so **no detection accuracy (precision / recall / Cohen's Kappa)
  is claimed**. For validated labels, use MAST (see *Relation to prior work*).
- **Single small model.** All runs use Llama 3.1 8B locally. An 8B model's failure
  behavior does not necessarily generalize to frontier models.
- **Framework coverage is best-effort.** Each runner targets a specific framework
  version; framework API drift can change or break a runner — which is itself a finding
  about local reproducibility, not a measured property of the framework.
- **Findings are descriptive, not causal.** Treat any numbers as directional.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Make your changes
4. Run tests (`pytest`)
5. Submit a pull request

All PRs must pass CI (lint + tests) before merging.

## License

[MIT](LICENSE)
