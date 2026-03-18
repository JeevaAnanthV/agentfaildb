# AgentFailDB

**A systematic benchmark for failure modes in multi-agent LLM systems.**

AgentFailDB studies how multi-agent frameworks (CrewAI, AutoGen, LangGraph, MetaGPT) fail when agents collaborate on complex tasks. It captures execution traces, detects failure patterns, and produces annotated datasets for research.

## Why This Exists

Multi-agent LLM systems fail in ways that single-agent systems don't — cascading hallucinations, infinite delegation loops, context degradation across handoffs, and agents silently producing confident but wrong outputs. These failures are poorly understood because nobody has systematically collected and categorized them.

AgentFailDB fills that gap.

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

## Architecture

```
Task Definitions (50 tasks × 5 categories)
    ↓
Framework Runners (CrewAI, AutoGen, LangGraph, MetaGPT)
    ↓
Normalized Execution Traces (TaskTrace → PostgreSQL)
    ↓
Pattern Detection Engine (7 detectors + LLM annotator)
    ↓
Analysis → HuggingFace Dataset + Leaderboard
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
│   ├── langgraph_runner.py
│   └── metagpt_runner.py
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

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Make your changes
4. Run tests (`pytest`)
5. Submit a pull request

All PRs must pass CI (lint + tests) before merging.

## License

[MIT](LICENSE)
