# AgentFailDB — Complete Architecture Plan

## Table of Contents

1. System Overview
2. Layer 1 — Input System
3. Layer 2 — Orchestration Engine
4. Layer 3 — Execution Layer (Hot Path)
5. Layer 4 — Storage Layer
6. Layer 5 — Detection & Annotation Engine
7. Layer 6 — Metrics & Statistical Analysis
8. Layer 7 — Output Surfaces
9. Cross-Cutting Concerns
10. Docker Infrastructure
11. Data Models (Complete)
12. Integration Contracts Between Components
13. Configuration System
14. Error Handling & Recovery
15. Testing Strategy
16. Build Sequence (What Depends on What)

---

## 1. System Overview

### 1.1 What the System Does in One Paragraph

AgentFailDB accepts a structured task definition, executes it across four multi-agent LLM frameworks (CrewAI, AutoGen, LangGraph, MetaGPT) using a locally-served open-source model (Llama 3.1 8B via Ollama), captures a complete trace of every inter-agent message during execution, runs that trace through a three-track failure detection engine (structural pattern matching, semantic NLP analysis, output validation), produces annotated failure labels with severity and root cause attribution, computes aggregate metrics across all traces, and publishes the results as a HuggingFace dataset, a Streamlit leaderboard, a pip-installable detection library, and an ArXiv paper.

### 1.2 Seven Layers

The system is organized into seven layers, each with a single responsibility:

Layer 1 (Input) owns task definitions, role specifications, and ground truth. It is the only layer that knows what tasks exist and what correct outputs look like.

Layer 2 (Orchestration) owns scheduling. It decides which task runs on which framework in what order, enforces resource limits, and handles retries.

Layer 3 (Execution) owns running frameworks and capturing traces. It is the hot path — the only layer that touches framework code and the LLM. It must be fast and non-intrusive.

Layer 4 (Storage) owns persistence. PostgreSQL for durable relational data, Redis for ephemeral cache and pattern signatures, filesystem for raw JSON backup.

Layer 5 (Detection) owns failure analysis. Three independent detection tracks run in parallel, an aggregator merges results, and an optional LLM verifier reviews ambiguous cases.

Layer 6 (Metrics) owns statistical computation. It reads annotated traces from PostgreSQL and produces failure rates, distributions, significance tests, and inter-annotator agreement scores.

Layer 7 (Output) owns publication. It formats data for HuggingFace, builds the Streamlit leaderboard, generates LaTeX tables for the paper, and exports the pip-installable library.

### 1.3 What Crosses Layer Boundaries

Data flows strictly downward through the layers during normal operation. Upward flow only occurs in one case: the orchestrator (Layer 2) reads detection results (Layer 5) to decide whether to re-run a task — this is the feedback loop for adversarial task calibration.

The only shared state between layers is PostgreSQL and Redis (Layer 4). No layer calls another layer's functions directly. They communicate through the database. This means any layer can be re-run independently: you can re-run detection on existing traces without re-executing frameworks, or recompute metrics without re-running detection.

---

## 2. Layer 1 — Input System

### 2.1 What It Contains

Three data structures feed the system:

**Task Registry** — A collection of 500 task definitions stored as JSON files organized by category and difficulty. Each task definition is a self-contained unit that fully specifies what the agents should do.

**Role Specification Library** — Canonical role definitions in plain English, plus per-framework translations. The canonical spec defines what the role does. The per-framework translations encode that role in each framework's native syntax.

**Ground Truth Library** — Expected outputs or evaluation rubrics paired with each task. Organized by the three-tier system (deterministic, claim-list, rubric).

### 2.2 Task Definition Structure

Every task definition contains these fields:

`task_id` — Globally unique identifier. Format: `{category}_{difficulty}_{sequential_number}`. Example: `research_hard_017`.

`category` — One of: `collaborative_research`, `code_generation`, `debate_reasoning`, `planning`, `data_analysis`.

`difficulty` — One of: `easy`, `medium`, `hard`, `adversarial`.

`description` — The exact natural language text sent to the agents. This string is identical across all four frameworks. It is the primary invariant of the experimental design.

`agent_count` — How many agents are required. Range: 2-5.

`agent_roles` — Ordered list of role identifiers. Each identifier maps to a canonical role spec and per-framework translations. Example: `["researcher", "writer", "reviewer"]`.

`orchestration_hint` — Describes the expected interaction pattern. One of: `sequential` (Agent A → B → C), `iterative` (A ↔ B repeated), `hierarchical` (manager delegates to workers), `collaborative` (all agents in group discussion). Frameworks interpret this hint using their native orchestration mechanism.

`ground_truth_type` — One of: `deterministic`, `claim_list`, `rubric`.

`ground_truth` — The evaluation data. Structure varies by type:

For `deterministic`: A list of assertion objects. Each assertion has a `test` string (evaluable Python expression or string match), a `weight` (0-1, summing to 1 across all assertions), and a `description` (human-readable explanation of what is being checked).

For `claim_list`: A list of claim objects. Each claim has a `statement` (natural language claim that a correct output should address), a `weight` (0-1), and a `required` boolean (if true, the claim must be present for the task to be considered successful regardless of total score).

For `rubric`: A list of dimension objects. Each dimension has a `name` (e.g., "argument_coherence"), a `description` (what scores 1-5 mean for this dimension), and a `weight` (0-1).

`adversarial_target` — Only present for adversarial tasks. Specifies which failure category this task is designed to trigger. One of the seven failure categories. This field is NOT used during execution or detection — it is used during analysis to measure "did the adversarial task succeed in triggering its target failure?"

`max_tokens` — Per-task token budget override. Defaults to 10,000 if not specified.

`max_time_seconds` — Per-task time budget override. Defaults to 180 if not specified.

`max_messages` — Per-task message count limit. Defaults to 30 if not specified.

### 2.3 Role Specification Structure

Each role has a canonical spec and framework translations:

**Canonical spec fields:**
`role_id` — Unique identifier. Example: `researcher`.
`name` — Human-readable name. Example: `Research Agent`.
`objective` — What this agent is trying to accomplish. 1-3 sentences.
`constraints` — What this agent must NOT do. 1-3 sentences.
`output_format` — What the agent's output should look like. Example: "A structured report with sections and citations."
`tools_allowed` — List of tool categories this agent may use. Example: `["web_search", "document_retrieval"]`. (Note: for the Ollama-based setup, most tool use is text-simulated rather than actual function calling.)

**Framework translations:** For each of the four frameworks, a translation object that encodes the canonical spec in that framework's syntax:
- CrewAI: `role`, `goal`, `backstory`, `allow_delegation`, `verbose` fields
- AutoGen: `system_message` string, `human_input_mode`, `code_execution_config`
- LangGraph: Node function prompt template, state schema
- MetaGPT: `Role` subclass config, `_act` behavior, `cause_by`/`send_to` mappings

The translations are stored in a `roles/` directory with one file per role, containing the canonical spec and all four translations. This is the "Appendix A" artifact for the paper.

### 2.4 Ground Truth Design Per Task Category

**Collaborative Research (claim-list):**
Every research task has 4-6 claims. Examples for "Summarize battery technology for EVs":
- "Mentions lithium-ion as currently dominant" (weight 0.25, required: true)
- "Discusses at least one emerging alternative" (weight 0.20)
- "Includes a quantitative metric" (weight 0.20)
- "Acknowledges environmental or supply chain challenges" (weight 0.20)
- "Citations or source attributions present" (weight 0.15)

**Code Generation (deterministic):**
Every coding task has 3-8 test assertions. Examples for "Implement rate limiter":
- `assert rate_limiter.allow("user1") == True` (weight 0.2)
- `assert rate_limiter.allow("user1") == True` (after 10 calls within window) == False (weight 0.3)
- Code contains error handling (string match for try/except or similar) (weight 0.2)
- Function runs without exceptions (weight 0.3)

**Debate/Reasoning (rubric):**
4 dimensions, each scored 1-5:
- `argument_coherence` (weight 0.30): Are conclusions logically supported?
- `evidence_usage` (weight 0.25): Are claims backed by reasoning?
- `balance` (weight 0.25): Were multiple perspectives considered?
- `resolution` (weight 0.20): Was a synthesis or conclusion reached?

**Planning (claim-list):**
5-7 claims. Examples for "Plan a 3-day Tokyo trip":
- "Includes accommodation recommendations" (weight 0.2)
- "Covers at least 3 distinct areas/neighborhoods" (weight 0.2)
- "Includes time estimates or a daily schedule" (weight 0.2)
- "Considers logistics/transit between locations" (weight 0.2)
- "Addresses budget or cost considerations" (weight 0.2)

**Data Analysis (deterministic):**
3-5 assertions against known data. You create the input CSV with known statistics, so you know the correct answers in advance. Examples:
- "Top product by revenue is Widget X" (weight 0.3)
- "Total Q3 revenue is between $1.15M and $1.25M" (weight 0.3, allows rounding)
- "Identifies the YoY decline in Category B" (weight 0.4)

### 2.5 Task Count Distribution

| Category | Easy | Medium | Hard | Adversarial | Total |
|---|---|---|---|---|---|
| Collaborative Research | 25 | 25 | 25 | 25 | 100 |
| Code Generation | 25 | 25 | 25 | 25 | 100 |
| Debate/Reasoning | 25 | 25 | 25 | 25 | 100 |
| Planning | 25 | 25 | 25 | 25 | 100 |
| Data Analysis | 25 | 25 | 25 | 25 | 100 |
| **Total** | 125 | 125 | 125 | 125 | **500** |

Each task runs across 4 frameworks = 2,000 total executions.

### 2.6 Adversarial Task Design Logic

Each adversarial task targets a specific failure category:

| Target Failure | Design Strategy | Example |
|---|---|---|
| Cascading hallucination | Include references to non-existent entities | "Summarize the findings of the 2024 Stanford XR-7 study on quantum memory" (this study does not exist) |
| Delegation loop | Ambiguous completion criteria | "Write code that is both maximally performant AND maximally readable" (conflicting goals cause reviewer to never approve) |
| Context degradation | Output with critical caveats and qualifiers | "Analyze this data, noting that Column B values after row 50 are provisional estimates" (the caveat must survive all agent handoffs) |
| Conflicting outputs | Genuinely debatable topic | "Should this company expand or consolidate?" with balanced evidence for both positions |
| Role violation | Task that tempts agents to overstep | Code review task where the code has an obvious one-line fix (tempts the reviewer to just fix it instead of reviewing) |
| Silent failure | Subtly corrupted input data | CSV with a swapped column header that produces plausible-but-wrong results if unnoticed |
| Resource exhaustion | Very broad, under-specified task | "Research everything about AI" (no clear stopping condition) |

Approximately 4 adversarial tasks per failure category × 5 task categories = 20 adversarial tasks per category, 100 total. The remaining 5 adversarial tasks per category are mixed-target (designed to potentially trigger multiple failure types simultaneously).

---

## 3. Layer 2 — Orchestration Engine

### 3.1 What It Does

The orchestrator is the system's scheduler and supervisor. It reads task definitions from Layer 1, dispatches them to framework runners in Layer 3, enforces execution limits, handles failures and retries, and records run metadata.

### 3.2 Scheduling Strategy

**Execution order:** Tasks are executed in batches. Each batch contains one task run across all four frameworks. This ensures that if the system stops mid-execution (crash, power loss, manual interrupt), you have partial results that are balanced across frameworks rather than 500 traces for CrewAI and 0 for the others.

**Batch structure:** Each batch = 1 task × 4 frameworks = 4 executions. Batches are ordered: all easy tasks first (across all categories), then medium, then hard, then adversarial. This ordering lets you start analysis on easy/medium results while hard/adversarial tasks are still running.

**Within a batch:** Frameworks run sequentially, not in parallel. This is a deliberate choice for the Ollama setup. Ollama serves one model instance. If two frameworks send concurrent requests, Ollama serializes them anyway (or runs out of memory with concurrent inference). Sequential execution is simpler, more predictable, and produces cleaner timing measurements.

**Exception:** If you have a GPU with enough VRAM for concurrent inference, you can switch to parallel batch execution. This is a config flag: `PARALLEL_FRAMEWORKS=false` in `.env`.

### 3.3 Execution Limits (Circuit Breakers)

Three limits per run, enforced by the orchestrator:

**Token limit:** The orchestrator monitors the running token count during execution (updated by the TraceCollector after each agent message). If the count exceeds `max_tokens` for this task, the orchestrator sends a cancellation signal to the framework runner. The trace is saved as-is with `task_success=False` and a resource exhaustion annotation is automatically added.

How the cancellation signal works: The orchestrator sets a threading Event (`cancel_event`). The framework runner checks this event between agent turns. If set, the runner calls the framework's stop method (CrewAI: raises StopIteration in the callback, AutoGen: sets `groupchat.max_round=0`, LangGraph: sets a halt condition in the state, MetaGPT: sets `Environment.is_idle=True`).

**Time limit:** An asyncio timeout wrapper around the entire run. If the run exceeds `max_time_seconds`, the wrapper cancels the task. The partial trace is saved. Implementation: `asyncio.wait_for(runner.execute(...), timeout=max_time)`.

**Message limit:** Tracked by the TraceCollector. After `max_messages` content messages (artifact messages do not count), the cancellation signal fires.

### 3.4 Retry Logic

Not every failure should be retried. The orchestrator distinguishes:

**Retryable failures:** Framework crashes (Python exceptions), Ollama connection errors (service temporarily unavailable), timeout due to Ollama being slow (not a framework failure). These get up to 2 retries with exponential backoff (10s, 30s).

**Non-retryable failures:** Token limit exceeded (this IS the data — resource exhaustion), message limit exceeded (potential delegation loop — this IS the data), task produced output (even if wrong — silent failure IS the data). These are saved as single runs with no retry.

**Crash-vs-failure distinction:** If the framework runner throws a Python exception (import error, API error, unhandled exception), that is a crash. The trace (if partial) is saved with a `crash` metadata flag and the run is retried. If the framework completes but produces a bad output, that is a failure and it is exactly what you are studying.

### 3.5 Progress Tracking

The orchestrator maintains a run manifest in PostgreSQL:

`run_manifest` table (not in the main schema — this is orchestrator-internal):
- `task_id` + `framework` = composite unique key
- `status` = `pending | running | completed | failed | skipped`
- `attempt_count` = how many times this run has been attempted
- `started_at`, `completed_at`
- `error_message` (if failed)

On startup, the orchestrator reads the manifest and resumes from where it left off. This makes the entire pipeline idempotent — you can stop and restart at any time without data loss or duplicate runs.

### 3.6 Orchestrator ↔ FastAPI Integration

The FastAPI service (which runs in the same container) provides HTTP endpoints that trigger orchestrator actions:

`POST /run/batch` — Run the next N tasks across all frameworks. Body: `{"count": 10}`. Returns immediately with a batch ID. Execution happens asynchronously.

`POST /run/single` — Run a single task on a single framework. Body: `{"task_id": "research_hard_017", "framework": "crewai"}`. Useful for debugging and re-runs.

`GET /run/status` — Returns the current manifest state: how many tasks are completed, running, pending, failed.

`POST /run/cancel` — Cancel all running executions gracefully.

These endpoints are for your use during development and benchmarking. The Streamlit leaderboard does NOT trigger runs — it only reads results.

---

## 4. Layer 3 — Execution Layer (Hot Path)

### 4.1 Why This Layer Is Special

This is the only layer that touches framework code and makes LLM calls. It must be:
- **Non-intrusive:** Trace collection must not alter framework behavior. No network calls, no blocking I/O, no added latency during agent execution.
- **Framework-agnostic:** The TraceCollector does not know which framework produced a message. It works with a normalized message format.
- **Fail-safe:** If trace collection itself crashes, the framework run should still complete. Better to lose trace data than to crash the framework (though both are undesirable).

### 4.2 Component: BaseRunner

The abstract base class that all framework runners inherit from. It defines the execution lifecycle:

**`async setup(task_config, role_specs)`** — Initialize the framework with the task's agent configuration. Creates agent instances, applies role specifications, configures the LLM connection to Ollama.

**`async execute(task_description) -> tuple[str, list[AgentMessage]]`** — Run the task and return the final output plus the complete message trace. This method is framework-specific — each runner implements it differently.

**`async teardown()`** — Clean up framework resources. Close connections, free memory, reset state for the next run.

**`async run(task_config, role_specs, task_description) -> TaskTrace`** — The public method. Calls setup → execute → teardown, wraps the result in a TaskTrace object, and handles exceptions. This method is NOT overridden by subclasses.

The `run` method also handles the cancellation signal from the orchestrator. It passes the `cancel_event` to the TraceCollector, which checks it between message recordings.

### 4.3 Component: Framework Adapters (One Per Framework)

Each adapter does two things: configures the framework to use Ollama, and hooks into the framework's message system to feed the TraceCollector.

**CrewAI Adapter**

Configuration: Creates a `crewai.LLM` object pointing to Ollama's OpenAI-compatible endpoint. Creates `crewai.Agent` instances with role specs translated into CrewAI's `role`/`goal`/`backstory` format. Creates a `crewai.Crew` with the agents and task.

Trace capture: Uses two hooks. `step_callback` fires after each agent step, providing the agent name and output — this becomes a `response` message. `task_callback` fires after each task completes — this captures the final result. CrewAI's `verbose=True` output is parsed for additional detail: delegation decisions become `task_delegation` messages, tool usage becomes `tool_call`/`tool_result` messages.

Message type tagging: CrewAI's internal "thinking" output (the reasoning before the final answer, visible in verbose mode) is tagged as `internal_reasoning`. The final agent output per step is tagged as `response`. Delegation decisions ("I'll delegate this to the Researcher") are tagged as `task_delegation`.

Known quirk: CrewAI sometimes merges multiple logical steps into a single callback invocation. The adapter uses heuristic parsing (looking for "Action:", "Thought:", "Final Answer:" markers in the output) to split these into separate messages.

**AutoGen Adapter**

Configuration: Creates `autogen.AssistantAgent` instances with `llm_config` pointing to Ollama. Creates a `autogen.GroupChat` and `GroupChatManager`. The system message for each agent comes from the role specification translation.

Trace capture: Post-hoc extraction from `groupchat.messages`. After the group chat completes, the adapter iterates through the message list. Each message has a `name` (sender), `content`, and implicitly a `role`. The adapter maps these to AgentMessage records.

Message type tagging: Messages from the `GroupChatManager` that are speaker selection announcements ("Next speaker: Agent_X") are tagged as `system_control`. The `TERMINATE` signal is tagged as `system_control`. `UserProxyAgent` auto-replies that are empty template text are `system_control`. Code execution requests are `tool_call` and their results are `tool_result`. Everything else is `response`.

Known quirk: AutoGen's `TERMINATE` keyword can appear mid-message if an agent includes it in its response text accidentally. The adapter checks whether TERMINATE is the sole content or embedded in a longer response and tags accordingly.

**LangGraph Adapter**

Configuration: Creates a `StateGraph` with nodes for each agent role. Each node is a function that calls the Ollama LLM via `langchain_openai.ChatOpenAI`. Edges between nodes encode the orchestration pattern from the task's `orchestration_hint`. Conditional edges are used for iterative patterns (reviewer → writer loop with a condition on review outcome).

Trace capture: Registers a custom callback handler that subclasses `langchain.callbacks.base.BaseCallbackHandler`. Overrides `on_chain_start` (captures which node is starting), `on_llm_end` (captures the LLM response and token usage), and `on_chain_end` (captures the node's final output). The callback feeds messages to the TraceCollector in real-time during execution.

Message type tagging: Node transitions are tagged based on the edge type. A normal edge from writer to reviewer is a `response`. A conditional back-edge from reviewer to writer is `feedback`. State checkpoint saves (LangGraph's built-in persistence) are tagged as `checkpoint`.

Known quirk: LangGraph's state is a dictionary, not a message list. The "message" between agents is the state diff. The adapter computes this diff by comparing state before and after each node execution, and uses the diff content as the message content.

**MetaGPT Adapter**

Configuration: Creates `metagpt.roles.Role` subclasses for each agent. Maps generic roles to MetaGPT's role system as defined in the comparability contract: Researcher → modified ProductManager, Writer → Engineer, Reviewer → QAEngineer, etc. Configures MetaGPT's LLMConfig to point to Ollama.

Trace capture: Monkey-patches `metagpt.environment.Environment.publish_message`. The patched method calls the original `publish_message` (so MetaGPT behavior is unchanged) AND records the message in the TraceCollector. The message includes `sent_from`, `send_to`, `content`, and `cause_by` fields from MetaGPT's message object.

Message type tagging: Messages caused by SOP actions (`WritePRD`, `WriteDesign`, `WriteCode`, `WriteCodeReview`, etc.) are tagged as `response` — they ARE the agent's real work product. Environment subscription routing metadata (the pub/sub infrastructure messages) are tagged as `subscription_routing`. Messages between the same agent (self-messages for state management) are tagged as `internal_reasoning`.

Known quirk: MetaGPT's SOP system can add extra agents beyond what you configured. For example, it may automatically inject a ProjectManager even if your task only specifies 3 agents. The adapter records all agents that participate, even unexpected ones. If an unexpected agent appears, it is logged in the trace metadata as `extra_agents`.

### 4.4 Component: TraceCollector

The TraceCollector is a singleton instantiated per-run. It lives in-process with the framework runner.

**Interface:**

`record(source_agent, target_agent, content, msg_type, api_token_count, metadata)` — Append a message to the in-memory buffer. Also computes `content_token_count` via tiktoken. Checks the `cancel_event` from the orchestrator. Increments running counters for total tokens and total messages.

`get_trace() -> list[AgentMessage]` — Returns the buffered messages. Called once after the run completes.

`flush(trace_id) -> None` — Batch-inserts all buffered messages into PostgreSQL's `messages` table in a single transaction. Also writes the summary to the `traces` table.

**Message tagging logic:**

Every message passes through a tagger before being buffered. The tagger applies the framework adapter's tagging rules (Decision 7) and assigns one of the 9 message types: `task_delegation`, `response`, `feedback`, `tool_call`, `tool_result`, `system_control`, `subscription_routing`, `internal_reasoning`, `checkpoint`.

The tagger also computes both token counts:
- `api_token_count`: Read from the Ollama API response's `usage` field (if this message triggered an API call). Null for messages that did not trigger an API call (e.g., tool results, system control messages).
- `content_token_count`: Computed via tiktoken's `cl100k_base` encoding on the `content` string. Always present.

**Buffer management:**

Messages are stored in a Python list (not a deque or queue — order matters, and append-only access is O(1)). The buffer is per-run. After flush, the buffer is cleared. If the process crashes before flush, the buffer is lost — but the framework run is also lost, so this is acceptable. There is no partial flush.

**Error isolation:**

The `record()` method is wrapped in a try/except that catches all exceptions. If recording fails (e.g., tiktoken crashes on unusual Unicode), the error is logged but the framework continues executing. A `trace_errors` field in the trace metadata records any recording failures.

### 4.5 Component: Ollama Client

All four frameworks communicate with Ollama through its OpenAI-compatible API. The frameworks' native OpenAI clients handle this — you do not need a custom Ollama client for execution.

However, you DO need a dedicated Ollama client for two purposes:

**Health checking:** The orchestrator checks `GET /api/tags` on startup to verify Ollama is running and the required model is loaded.

**Context window configuration:** On first run, the orchestrator sends a model configuration request to set `num_ctx: 8192`. This ensures the model has enough context for multi-agent conversations. Without this, Ollama defaults to 2048 tokens, which is too small for most multi-agent interactions.

**Token usage extraction:** When frameworks use streaming, the Ollama response's `usage` field may be in the final stream chunk. The framework adapter must ensure usage data is captured regardless of streaming mode. The safest approach: configure all frameworks with `stream=False` for the benchmark.

---

## 5. Layer 4 — Storage Layer

### 5.1 PostgreSQL (Primary Store)

**Tables:**

`traces` — One row per task execution. Contains: task metadata (id, category, difficulty, description), execution metadata (framework, model, timing, token counts, agent config), output data (actual output, task success), and ground truth reference.

`messages` — One row per inter-agent message. Contains: the message content, source/target agents, message type, token counts, timestamps, and optional tool call data. Foreign key to `traces`. Ordered by `message_index` within a trace.

`annotations` — One row per detected failure. Contains: failure category, severity, root cause agent, failure point (message index where failure started), description, confidence score, and annotation source (rule-based, LLM, human). Foreign key to `traces`. A trace can have 0 to N annotations.

`run_manifest` — Orchestrator internal. Tracks which tasks have been run, their status, and attempt count.

**Query patterns the schema is optimized for:**

1. "Failure rate per framework" — `SELECT framework, COUNT(*) FILTER (WHERE trace_id IN (SELECT trace_id FROM annotations WHERE category != 'none')) / COUNT(*) FROM traces GROUP BY framework`

2. "Failure distribution by category" — `SELECT category, COUNT(*) FROM annotations GROUP BY category`

3. "Reconstruct a trace" — `SELECT * FROM messages WHERE trace_id = $1 ORDER BY message_index`

4. "Find traces with specific failure" — `SELECT t.* FROM traces t JOIN annotations a ON t.trace_id = a.trace_id WHERE a.category = 'delegation_loop' AND a.severity = 'critical'`

5. "Compare frameworks on a specific task" — `SELECT * FROM traces WHERE task_id = $1 ORDER BY framework`

6. "Compute resource exhaustion baselines" — `SELECT task_category, task_difficulty, PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY total_api_tokens) FROM traces WHERE task_success = true GROUP BY task_category, task_difficulty`

### 5.2 Redis (Cache & Patterns)

**Key spaces:**

`pattern:{signature_hash}` — Maps a failure pattern signature (hash of failure category + framework + task category + message type sequence) to its full annotation. TTL: 3600s. Used by the detection engine for fast lookup of known patterns.

`baseline:{task_category}:{task_difficulty}:tokens` — Sorted set containing the last 50 successful run token counts for this task group. Used by the resource exhaustion detector in online mode (the detection library, not the benchmark).

`baseline:{task_category}:{task_difficulty}:time` — Same, for time.

`baseline:{task_category}:{task_difficulty}:messages` — Same, for message count.

`trace_cache:{trace_id}` — Full serialized trace cached for 300s after first read. The detection engine reads each trace once from PostgreSQL and caches it in Redis. Subsequent detectors read from cache.

`embedding:{content_hash}` — Cached embedding vector for a message content string. TTL: 7200s. Avoids recomputing sentence-transformer embeddings for identical messages (common when agents repeat themselves in loops).

### 5.3 Filesystem (Backup)

Every trace is also written to `data/traces/{framework}/{task_id}.json` as a complete JSON dump. This serves as a backup that does not require PostgreSQL to access, and as the export format for the HuggingFace dataset. The JSON includes the full trace, all messages, and all annotations.

---

## 6. Layer 5 — Detection & Annotation Engine

### 6.1 Pipeline Flow

```
Raw trace from PostgreSQL
        │
        ▼
┌─ Preprocessor ─────────────────────────┐
│  1. Filter to content messages only     │
│  2. Compute embeddings (MiniLM-L6)      │
│  3. Extract entities (spaCy or regex)   │
│  4. Build message graph (agent pairs)   │
│  5. Compute pairwise similarities       │
└─────────────────────────────────────────┘
        │
        ├──────────────┬──────────────┐
        ▼              ▼              ▼
   Track A:       Track B:       Track C:
   Structural     Semantic       Output
   (rules)        (ML models)    (ground truth)
        │              │              │
        └──────┬───────┴──────┬───────┘
               ▼              │
        ┌─ Aggregator ────────┘──────────┐
        │  Merge detections               │
        │  Resolve conflicts              │
        │  Assign final severity          │
        │  Deduplicate overlapping labels │
        └──────────────────────────────────┘
               │
               ▼ (if confidence 0.5-0.8)
        ┌─ LLM Verifier (Optional) ──────┐
        │  Send ambiguous cases to Ollama │
        │  or Claude for final judgment   │
        └──────────────────────────────────┘
               │
               ▼
        Write annotations to PostgreSQL
```

### 6.2 Preprocessor

The preprocessor runs once per trace and produces derived data structures that all three tracks consume.

**Content message filter:** Extract only messages with `msg_type` in (`task_delegation`, `response`, `feedback`, `tool_call`, `tool_result`). Store in an ordered list called `content_messages`.

**Embedding computation:** For each content message, compute a 384-dimensional embedding using `sentence-transformers/all-MiniLM-L6-v2`. Check Redis cache first (`embedding:{sha256(content)[:16]}`). On cache miss, compute and cache. Store embeddings in a numpy array aligned with `content_messages` by index.

**Entity extraction:** Extract named entities (people, organizations, numbers, percentages, dates) from each message using either spaCy's `en_core_web_sm` or simple regex patterns for common entity types. Store as a list of entity sets, one per message.

**Message graph:** Build a directed graph where nodes are agent names and edges are messages between them. Edge attributes: count (number of messages on this edge), total tokens, average embedding similarity between consecutive messages on this edge. This graph is used by the structural detectors.

**Pairwise similarity matrix:** Compute cosine similarity between all pairs of content message embeddings. Store as an NxN numpy matrix. This is used by the semantic detectors for context degradation and hallucination cascading analysis.

### 6.3 Track A: Structural Detection (7 Detectors)

**A1: Delegation Loop Detector**

Input: Message graph.

Logic: For each pair of agents (A, B), count the number of back-and-forth exchanges: messages from A→B followed by B→A, repeated. If this count exceeds the threshold (default: 5), flag a delegation loop.

Secondary check: For the same agent pair, compute the average cosine similarity between consecutive messages from the same agent. If similarity > 0.85 for 3+ consecutive messages, the agent is repeating itself — strong loop signal even below the count threshold.

Output: `FailureAnnotation` with category=delegation_loop, severity based on loop count, root_cause_agent=the agent that initiates the most repetitive messages, failure_point_index=the message where the repetition starts.

**A2: Resource Exhaustion Detector**

Input: Trace metadata (total_api_tokens, total_time_seconds, message count).

Logic: Compare against baselines. For the benchmark: baselines are post-hoc medians per (task_category, task_difficulty) group. For the library: baselines are rolling Redis medians.

Thresholds: token_ratio = actual / baseline_median. Time_ratio = actual / baseline_median. Message_ratio = actual / baseline_median. If any ratio > 3x, flag resource exhaustion.

Severity: max(token_ratio, time_ratio, message_ratio) in [3,5) → minor, [5,10) → major, ≥10 → critical.

Output: Annotation with the specific ratios in the description.

**A3: Role Violation Detector**

Input: Content messages with their source agents and the role specifications.

Logic: For each agent, compare its output characteristics against its role specification's expected output format. Checks:
- Does a reviewer agent produce code blocks longer than 5 lines? (Should be reviewing, not writing code)
- Does a researcher agent include action recommendations? (Should report facts only)
- Does a planner agent start implementing instead of planning?

Implementation: A set of role-specific rules. Each role has a list of "violation patterns" — regex or keyword patterns that indicate role overstepping. Example: For the "reviewer" role, the violation pattern is `code_block_length > 5 AND code_block_content differs from input code` (meaning the reviewer wrote new code, not just quoted the input).

Severity: Based on how much of the agent's output is outside its role. If >50% of the output is role-violating, it is critical.

### 6.4 Track B: Semantic Detection (3 Detectors)

**B1: Context Degradation Detector**

Input: Content messages, entity sets, embeddings, pairwise similarity matrix.

Logic: Track information fidelity through the agent chain.

Step 1: Identify the "source of truth" — typically the first agent's output or the task input. Extract the set of claims/entities from this source: `source_entities`.

Step 2: For each subsequent agent in the chain, compute the retention rate: `|source_entities ∩ agent_entities| / |source_entities|`. This measures what fraction of the original information survives each handoff.

Step 3: Plot the retention rate across the chain. If it drops below 0.7 at any point, flag context degradation.

Step 4: Specifically check for qualifier loss — look for hedge words ("approximately", "estimated", "preliminary", "one-time") in the source that disappear in later agents. These are critical for meaning preservation.

Severity: Retention 0.5-0.7 = minor, 0.3-0.5 = major, <0.3 = critical.

**B2: Cascading Hallucination Detector**

Input: Content messages, embeddings, the task description (as grounding set).

Logic:

Step 1: Build the grounding set — all factual claims extractable from the task description and any provided input data. These are "things the agents were told."

Step 2: For each agent's output, extract factual claims (specific names, numbers, dates, citations, API references). Check each claim against the grounding set using embedding similarity. Claims with similarity < 0.5 to any grounding claim are potential hallucinations.

Step 3: For each potential hallucination from Agent A, check if any subsequent agent (B, C, etc.) includes the same or an elaborated version (similarity > 0.8). If yes, the hallucination has cascaded.

Step 4: Compute cascading depth = number of agents the hallucination passes through.

Severity: Depth 1 (hallucination stays in one agent, not cascaded) = minor. Depth 2 = major. Depth 3+ or hallucination reaches final output = critical.

**B3: Conflicting Output Detector**

Input: Content messages, embeddings, DeBERTa NLI model.

Logic:

Step 1: Identify all agent outputs that address the same subtopic. Use embedding similarity between full outputs — outputs with cosine similarity > 0.6 are addressing related topics.

Step 2: For each pair of related outputs, extract claim segments (sentences or paragraphs that make a specific assertion).

Step 3: Run each pair of claim segments through DeBERTa-v3 (MNLI-trained) to classify the relationship as entailment, neutral, or contradiction.

Step 4: If contradiction is detected with confidence > 0.7, check whether the orchestration system resolved it (the final output should acknowledge or resolve the contradiction). If the final output simply concatenates contradictory claims, the failure is confirmed.

Severity: Contradictions on style/format = minor. Contradictions on factual claims = major. Contradictions on action items or recommendations = critical.

### 6.5 Track C: Output Validation (1 Detector)

**C1: Silent Failure Detector**

Input: Actual output, ground truth (from task definition), ground truth type.

Logic varies by ground truth tier:

**Tier 1 (deterministic):** Run each assertion against the actual output. Compute weighted pass rate. If pass_rate < 0.4 AND no other failure was detected by Tracks A or B, flag as silent failure.

**Tier 2 (claim-list):** Send each claim + actual output to the LLM annotator (Ollama or Claude). Ask "Does this output contain or address this claim?" Compute claim coverage score. If score < 0.4 AND no other failure was detected, flag as silent failure.

**Tier 3 (rubric):** Send the task description + actual output + rubric to the LLM annotator. Get scores per dimension. If average score < 2.0 AND no other failure was detected, flag as silent failure.

The "AND no other failure" condition is critical. If the output is wrong because of a delegation loop (agents never completed the task), that is a delegation loop failure, not a silent failure. Silent failure specifically means: the system appeared to work correctly (no visible anomalies) but produced a wrong output.

### 6.6 Aggregator

The aggregator receives lists of FailureAnnotation objects from all three tracks and produces the final annotation set for the trace.

**Deduplication rule:** If Track A and Track B both flag the same category on the same trace, keep the one with higher confidence. They are detecting the same failure through different methods.

**Multi-label rule:** Different categories are kept independently. A trace can have both `delegation_loop` (from Track A) and `resource_exhaustion` (from Track A) — these are co-occurring but distinct failures.

**Severity resolution:** If the same failure is detected at different severities by different tracks, take the higher severity. (Conservative: better to over-flag than under-flag.)

**"None" assignment:** If no track flags any failure AND the task was successful (per ground truth evaluation), the trace receives a single annotation with `category=none, severity=none`.

### 6.7 LLM Verification Layer

For annotations with confidence between 0.5 and 0.8, the aggregator queues them for LLM verification.

The verifier sends a structured prompt to Ollama (or Claude, if API key is present):

```
You are reviewing a multi-agent trace for the following potential failure:
Category: {category}
Preliminary severity: {severity}
Evidence: {description}

Task description: {task_description}
Message trace (content messages only):
{formatted_messages}

Question: Is this failure classification correct?
Respond in JSON: {"confirmed": true/false, "revised_category": "...", "revised_severity": "...", "reasoning": "..."}
```

If confirmed, the annotation stands. If overridden, the annotation is updated. The verification source is recorded (`llm_ollama` or `llm_claude`).

Batch size for verification: 5 traces per LLM call (to stay within context window limits for 8B models). This is the most expensive step in the detection pipeline — budget approximately 1 Ollama inference per ambiguous trace.

---

## 7. Layer 6 — Metrics & Statistical Analysis

### 7.1 Primary Metrics

**M1: Overall failure rate per framework.** `failed_traces / total_traces` per framework. Include 95% confidence interval computed via Wilson score interval (better than normal approximation for proportions near 0 or 1).

**M2: Failure rate per category per framework.** A 7×4 matrix (7 failure categories × 4 frameworks). This is the core result table of your paper.

**M3: Failure rate by difficulty level.** A 4×4 matrix (4 difficulty levels × 4 frameworks). Shows complexity scaling.

**M4: Severity distribution per framework.** For each framework, the proportion of failures that are minor/major/critical. A framework with 40% failure rate but mostly minor is better than one with 30% failure rate but mostly critical.

**M5: Cascading depth distribution.** For cascading hallucination and context degradation failures, histogram of depth values. Mean, median, max.

**M6: Cost of failure.** Mean token count for failed vs. successful runs, per failure category. This quantifies the economic impact: "Delegation loops cost on average X additional tokens compared to successful runs."

**M7: Detection accuracy.** Per-category precision, recall, F1 against the 100-trace human-validated set.

**M8: Inter-annotator agreement.** Per-category binary Cohen's Kappa between LLM annotations and human annotations.

### 7.2 Statistical Tests

**Chi-squared test:** For comparing failure rates between frameworks. "Is CrewAI's failure rate significantly different from AutoGen's?" The test uses a 2×2 contingency table (failed/succeeded × framework A/framework B). Report χ² statistic and p-value. Apply Bonferroni correction for multiple comparisons (6 pairwise comparisons across 4 frameworks).

**Mann-Whitney U test:** For comparing token usage distributions between failed and successful runs. Non-parametric (does not assume normal distribution, which token counts definitely are not). Report U statistic and p-value.

**Fisher's exact test:** For comparing failure rates on adversarial tasks specifically (small sample sizes — 25 adversarial tasks per category per framework). More appropriate than chi-squared for small counts.

### 7.3 Output Formats

The metrics engine produces three output formats from the same computation:

**JSON:** Machine-readable results consumed by the Streamlit leaderboard. Written to `data/metrics/results.json`.

**CSV:** Tabular results consumable by pandas, Excel, or Google Sheets. Written to `data/metrics/` with one CSV per metric.

**LaTeX:** Formatted tables for direct inclusion in the paper. Written to `paper/tables/`. Each table is a standalone `.tex` file that can be `\input{}` into the main paper.

---

## 8. Layer 7 — Output Surfaces

### 8.1 HuggingFace Dataset

**Format:** Parquet files with two splits:
- `train` (400 traces): For training failure detectors. Includes all annotations.
- `test` (100 traces): Human-validated. For evaluating failure detectors.

**Schema per row:**
`trace_id`, `framework`, `task_category`, `task_difficulty`, `task_description`, `model_used`, `actual_output`, `expected_output`, `task_success`, `messages` (JSON list), `annotations` (JSON list), `total_api_tokens`, `total_content_tokens`, `total_time_seconds`, `num_agents`, `agent_roles`.

**Dataset card:** Includes benchmark description, taxonomy summary, intended uses, limitations, citation BibTeX, and loading example:
```python
from datasets import load_dataset
ds = load_dataset("jeevaananth/agentfaildb")
```

### 8.2 Streamlit Leaderboard

**Pages:**

Page 1 — Overview: Framework comparison table (failure rates, severity distributions). Bar chart of failure rates. Radar chart of per-category failures per framework.

Page 2 — Explorer: Filterable trace viewer. Select framework, category, severity, difficulty. View individual traces with highlighted failure points.

Page 3 — Methodology: Taxonomy definitions, annotation protocol, statistical methods. Links to paper and repo.

Page 4 — Submit: Instructions for running the benchmark on a new framework and submitting results.

**Data source:** Reads from `data/metrics/results.json` and `data/traces/` directory. Does NOT connect to PostgreSQL — the leaderboard is a static viewer of pre-computed results. This means it can be deployed on HuggingFace Spaces (which does not support PostgreSQL) without any changes.

### 8.3 GitHub Repository + PyPI Package

**The pip-installable package (`agentfaildb`)** contains only the detection engine — Layers 5.2-5.7. It does NOT include the benchmark harness, framework runners, or orchestrator. Users install it to detect failures in their own agent systems:

```python
from agentfaildb import FailureDetector
from agentfaildb.trace import TaskTrace, AgentMessage

detector = FailureDetector()
annotations = detector.analyze(trace)
```

**What the package includes:**
- `agentfaildb/detector.py` — Main detection engine
- `agentfaildb/patterns/` — All 7 pattern detectors
- `agentfaildb/trace.py` — Data models (Pydantic)
- `agentfaildb/metrics.py` — Statistical utilities
- `agentfaildb/annotator.py` — LLM annotation helper

**What the repo includes (in addition to the package):**
- `runners/` — Framework-specific runners (not pip-installable)
- `tasks/` — Task definitions (not pip-installable)
- `harness/` — Orchestrator and FastAPI service (not pip-installable)
- `leaderboard/` — Streamlit app
- `paper/` — LaTeX source
- `docker-compose.yml` — Full infrastructure

### 8.4 ArXiv Paper

Structure, length, and positioning as defined in previous documents. The paper references the dataset (by HuggingFace URL), the library (by GitHub URL), and the leaderboard (by HuggingFace Spaces URL).

---

## 9. Cross-Cutting Concerns

### 9.1 Logging

All components use `structlog` for structured JSON logging. Log levels:

- `DEBUG`: Individual message recordings, detector intermediate values, SQL queries
- `INFO`: Task start/complete, batch start/complete, detection results, annotation results
- `WARNING`: Retry attempts, partial trace recovery, low-confidence detections
- `ERROR`: Framework crashes, database connection failures, Ollama unavailable

Logs are written to stdout (visible via `docker compose logs`) and to `data/logs/{date}.jsonl` for persistence.

### 9.2 Configuration

All configuration flows through environment variables, read by `pydantic-settings.BaseSettings`:

```
# Infrastructure
DATABASE_URL, REDIS_URL, OLLAMA_BASE_URL

# Models
TASK_MODEL, ANNOTATION_MODEL, OPENAI_API_BASE, OPENAI_API_KEY

# Execution limits
MAX_TOKENS_PER_RUN, MAX_TIME_PER_RUN, MAX_MESSAGES_PER_RUN

# Detection thresholds
LOOP_COUNT_THRESHOLD (default: 5)
LOOP_SIMILARITY_THRESHOLD (default: 0.85)
EXHAUSTION_RATIO_THRESHOLD (default: 3.0)
DEGRADATION_RETENTION_THRESHOLD (default: 0.7)
HALLUCINATION_GROUNDING_THRESHOLD (default: 0.5)
CONTRADICTION_CONFIDENCE_THRESHOLD (default: 0.7)
SILENT_FAILURE_SCORE_THRESHOLD (default: 0.4)

# Pipeline
PARALLEL_FRAMEWORKS (default: false)
LLM_VERIFICATION_ENABLED (default: true)
ANNOTATION_MAJORITY_VOTING_PASSES (default: 3)
```

Every threshold used by a detector is configurable. This lets users tune the detection engine for their use case (stricter or looser detection) and lets you experiment with thresholds during the benchmark.

### 9.3 Error Handling

**Framework crash:** Catch all exceptions in `BaseRunner.run()`. Save partial trace. Log error. Mark run as `failed` in manifest. Retry if retryable.

**Ollama unavailable:** Exponential backoff with 3 retries. If still unavailable, pause the orchestrator and alert via log. Resume automatically when Ollama returns (health check polling).

**PostgreSQL unavailable:** Buffer traces in memory and retry flush. After 3 failed flushes, write to filesystem as JSON fallback. The orchestrator pauses until PostgreSQL is available.

**Redis unavailable:** All Redis operations are wrapped in try/except. If Redis is down, the system operates without cache — slower but functional. Detection uses PostgreSQL directly.

**Detection engine crash:** If a single detector throws an exception, catch it, log it, and continue with the other detectors. A crash in the delegation loop detector should not prevent the context degradation detector from running.

### 9.4 Idempotency

Every operation is designed to be safely re-runnable:

- The orchestrator's manifest prevents duplicate runs. Re-running `POST /run/batch` skips already-completed tasks.
- The detection engine checks for existing annotations before running. Re-analyzing a trace replaces existing annotations (upsert behavior).
- The metrics engine always recomputes from scratch — it is a pure function of the annotations table.
- The HuggingFace export always regenerates from the current database state.

---

## 10. Docker Infrastructure

### 10.1 Service Map

| Service | Image | Port | Volume | Healthcheck |
|---|---|---|---|---|
| postgres | postgres:16-alpine | 5432 | postgres_data | pg_isready |
| redis | redis:7-alpine | 6379 | redis_data | redis-cli ping |
| ollama | ollama/ollama:latest | 11434 | ollama_data | curl /api/tags |
| ollama-init | curlimages/curl | — | — | (exits after model pull) |
| app | Custom Dockerfile | 8000 | ./ (bind mount) | curl /health |

### 10.2 Network

All services share the default Docker Compose network (`agentfaildb_default`). They communicate via service names as hostnames: the app connects to `postgres:5432`, `redis:6379`, and `ollama:11434`.

No service is exposed to the internet. Ports are mapped to localhost only for development access.

### 10.3 Startup Sequence

1. `docker compose up -d` starts all services.
2. PostgreSQL starts and runs `init.sql`. Healthcheck passes when `pg_isready` succeeds.
3. Redis starts. Healthcheck passes when `redis-cli ping` returns PONG.
4. Ollama starts. Healthcheck passes when `/api/tags` returns HTTP 200.
5. ollama-init starts (depends on ollama healthy). Pulls the task model. Exits.
6. app starts (depends on postgres, redis, ollama all healthy). Runs uvicorn.

Total startup time: ~30s if models are cached, ~10-20 minutes on first run (model download).

---

## 11. Data Models (Complete)

### 11.1 TaskConfig (Input)

```
task_id: str
category: enum(collaborative_research, code_generation, debate_reasoning, planning, data_analysis)
difficulty: enum(easy, medium, hard, adversarial)
description: str
agent_count: int (2-5)
agent_roles: list[str]
orchestration_hint: enum(sequential, iterative, hierarchical, collaborative)
ground_truth_type: enum(deterministic, claim_list, rubric)
ground_truth: dict (structure varies by type)
adversarial_target: optional[failure_category]
max_tokens: int (default 10000)
max_time_seconds: int (default 180)
max_messages: int (default 30)
```

### 11.2 RoleSpec (Input)

```
role_id: str
name: str
objective: str
constraints: str
output_format: str
tools_allowed: list[str]
translations:
  crewai: {role, goal, backstory, allow_delegation, verbose}
  autogen: {system_message, human_input_mode, code_execution_config}
  langgraph: {prompt_template, state_schema}
  metagpt: {role_class, act_behavior, cause_by, send_to}
```

### 11.3 AgentMessage (Trace)

```
message_id: uuid
trace_id: uuid (FK)
message_index: int
timestamp: datetime
source_agent: str
target_agent: str
content: str
msg_type: enum(task_delegation, response, feedback, tool_call, tool_result,
               system_control, subscription_routing, internal_reasoning, checkpoint)
api_token_count: optional[int]
content_token_count: int
model_used: optional[str]
tool_calls: optional[json]
metadata: json
```

### 11.4 TaskTrace (Trace)

```
trace_id: uuid
framework: str
task_category: str
task_difficulty: str
task_id: str
task_description: str
ground_truth_type: enum
ground_truth: json
actual_output: str
total_api_tokens: int
total_content_tokens: int
total_time_seconds: float
num_agents: int
agent_roles: json
task_success: optional[bool]
model_used: str
run_timestamp: datetime
run_config: json
messages: list[AgentMessage]
annotations: list[FailureAnnotation]
```

### 11.5 FailureAnnotation (Detection)

```
annotation_id: uuid
trace_id: uuid (FK)
category: enum(cascading_hallucination, delegation_loop, context_degradation,
               conflicting_outputs, role_violation, silent_failure, 
               resource_exhaustion, none)
severity: enum(none, minor, major, critical)
root_cause_agent: optional[str]
failure_point_index: optional[int]
description: str
confidence: float (0-1)
source: enum(rule_based, llm_ollama, llm_claude, human)
annotator_id: optional[str]
created_at: datetime
```

---

## 12. Integration Contracts Between Components

### 12.1 Orchestrator → BaseRunner

The orchestrator calls `runner.run(task_config, role_specs, task_description)` and receives a `TaskTrace`. The orchestrator does not know which framework is running — it only knows the runner interface.

Contract: The runner MUST return a TaskTrace within `max_time_seconds + 10s` (10s grace period for teardown). If it does not, the orchestrator kills the runner process and saves a partial trace.

### 12.2 Framework Adapter → TraceCollector

The adapter calls `collector.record(source, target, content, msg_type, api_tokens, metadata)` for each inter-agent message. The collector MUST NOT throw exceptions that propagate to the adapter. The collector MUST NOT make network calls.

Contract: `record()` completes in < 1ms. It is an in-memory append operation. Any processing (token counting, tagging) that takes longer is deferred to the `flush()` call.

### 12.3 TraceCollector → PostgreSQL

The collector calls `flush(trace_id)` after the run completes. This performs a single database transaction: INSERT into `traces` + batch INSERT into `messages`.

Contract: If the database is unavailable, `flush()` writes to filesystem as fallback and returns a warning. It does NOT throw an exception.

### 12.4 Detection Engine → PostgreSQL

The engine reads traces via `SELECT * FROM traces WHERE trace_id = $1` + `SELECT * FROM messages WHERE trace_id = $1 ORDER BY message_index`. It writes annotations via batch INSERT into `annotations`.

Contract: The engine operates on one trace at a time. It does not hold database locks between reads and writes. Concurrent detection on different traces is safe.

### 12.5 Detection Engine → Redis

Reads: `GET pattern:{hash}`, `GET embedding:{hash}`, `GET trace_cache:{id}`.
Writes: `SET pattern:{hash}`, `SET embedding:{hash}`, `SETEX trace_cache:{id}`.

Contract: All Redis operations have a 100ms timeout. If Redis is slow or down, the operation returns None and the engine proceeds without cache.

### 12.6 Metrics Engine → PostgreSQL

Reads only. Complex aggregate queries with GROUP BY, JOIN, and window functions. No writes.

Contract: The metrics engine assumes annotations are complete. Do not run metrics while detection is still in progress.

### 12.7 Metrics Engine → Output Surfaces

Writes JSON, CSV, and LaTeX to the filesystem. The Streamlit leaderboard reads JSON on startup. The paper LaTeX \input{}s the table files.

Contract: Output files are atomic — written to a temp file first, then renamed. This prevents the leaderboard from reading a partially-written file.

---

## 13. Configuration System

### 13.1 Configuration Hierarchy

Three levels, each overriding the previous:

1. **Defaults** (hardcoded in `pydantic-settings` model): Sensible defaults for all parameters. The system runs with zero configuration.

2. **`.env` file**: Project-level overrides. Committed to the repo as `.env.example` with documentation.

3. **Environment variables**: Runtime overrides. Set in `docker-compose.yml` or on the command line.

### 13.2 Configuration Categories

**Infrastructure config** (set once, rarely changes):
DATABASE_URL, REDIS_URL, OLLAMA_BASE_URL, ports, volume paths.

**Model config** (may change per experiment):
TASK_MODEL, ANNOTATION_MODEL. Changing these re-runs the benchmark with a different model.

**Execution config** (may change per experiment):
MAX_TOKENS_PER_RUN, MAX_TIME_PER_RUN, MAX_MESSAGES_PER_RUN, PARALLEL_FRAMEWORKS.

**Detection config** (tunable):
All threshold values for all 7 detectors. These are the parameters you tune during development and report in the paper.

**Feature flags:**
LLM_VERIFICATION_ENABLED, ANNOTATION_MAJORITY_VOTING_PASSES, EXPORT_RAW_JSON.

---

## 14. Error Handling & Recovery

### 14.1 Error Categories

**Transient errors:** Ollama temporarily overloaded, database connection dropped, Redis timeout. Strategy: retry with exponential backoff.

**Permanent errors:** Invalid task config, unsupported framework, model not loaded. Strategy: fail immediately with a clear error message. Do not retry.

**Data errors:** Corrupt trace data, malformed JSON in metadata, encoding issues. Strategy: log the error, skip the affected record, continue processing. Never crash the pipeline because of bad data.

**Resource errors:** Out of memory (Ollama or Python process), disk full. Strategy: graceful degradation. The orchestrator detects resource pressure (via psutil) and pauses execution until resources free up.

### 14.2 Recovery Scenarios

**Scenario: Docker host restarts mid-benchmark.**
Recovery: `docker compose up -d`. PostgreSQL data is on a volume — no data loss. The orchestrator reads the manifest and resumes from the last incomplete task.

**Scenario: Ollama crashes mid-run.**
Recovery: Ollama restarts automatically (`restart: unless-stopped`). The current run is lost (partial trace saved if any messages were recorded). The orchestrator retries the task after Ollama's healthcheck passes.

**Scenario: You need to change the model mid-benchmark.**
Recovery: Update `TASK_MODEL` in `.env`. Run `docker compose up -d` to restart the app. The orchestrator resumes from where it left off, now using the new model. Traces from the old model have the model name recorded in `model_used` — they remain valid but are a different experimental condition. The metrics engine can filter by `model_used`.

---

## 15. Testing Strategy

### 15.1 Unit Tests

Each detector has unit tests with synthetic traces designed to trigger (or not trigger) specific failure patterns.

Example: The delegation loop detector has tests for:
- Trace with no loop (should return None)
- Trace with 3-turn back-and-forth (below threshold, should return None)
- Trace with 10-turn back-and-forth (above threshold, should return annotation)
- Trace with semantic repetition but no structural loop (should detect via similarity)

### 15.2 Integration Tests

Test the full pipeline on 5 hand-crafted traces (one per task category) with known failure labels. Verify that the detection engine produces the expected annotations. Run these as part of CI on every commit.

### 15.3 Framework Smoke Tests

Before running the full benchmark, run a single easy task on each framework to verify: (1) the framework connects to Ollama successfully, (2) the adapter captures messages correctly, (3) the TraceCollector produces a valid trace, and (4) the trace can be written to PostgreSQL.

### 15.4 End-to-End Test

Run 1 task × 4 frameworks × full detection pipeline × metrics computation. Verify that the complete pipeline produces a valid JSON output that the Streamlit leaderboard can read.

---

## 16. Build Sequence (What Depends on What)

This is the critical path — the order in which you must build components.

**Block 1: Infrastructure + Data Models**
Build: docker-compose.yml, Dockerfile, init.sql, Pydantic models (trace.py)
Test: `docker compose up`, verify all services healthy, verify tables created
Depends on: Nothing
Unlocks: Everything else

**Block 2: TraceCollector + First Runner (CrewAI)**
Build: TraceCollector (in-memory buffer + flush), CrewAI adapter
Test: Run 1 task through CrewAI, verify trace in PostgreSQL
Depends on: Block 1
Unlocks: Blocks 3, 4

**Block 3: Remaining Runners (AutoGen, LangGraph, MetaGPT)**
Build: Three more adapters
Test: Run 1 task through each, verify traces
Depends on: Block 2
Unlocks: Block 6

**Block 4: Detection Engine (Structural Track)**
Build: Preprocessor, delegation loop detector, resource exhaustion detector, role violation detector
Test: Run on synthetic traces with known failures
Depends on: Block 2 (needs trace format)
Unlocks: Block 5

**Block 5: Detection Engine (Semantic + Output Tracks)**
Build: Embedding computation, context degradation, cascading hallucination, conflicting outputs, silent failure detectors. Aggregator. LLM verifier.
Test: Run on synthetic traces
Depends on: Block 4
Unlocks: Block 7

**Block 6: Orchestrator + Task Registry**
Build: Orchestrator (scheduling, limits, retries, manifest), Task definitions (all 500), Role specifications
Test: Run a batch of 10 tasks across all frameworks
Depends on: Blocks 2, 3
Unlocks: Block 8

**Block 7: Metrics Engine**
Build: SQL queries, statistical tests, output formatters (JSON, CSV, LaTeX)
Test: Run on detection results from Block 5
Depends on: Block 5
Unlocks: Block 9

**Block 8: Full Benchmark Run**
Execute: All 500 tasks × 4 frameworks. Annotate all traces.
Depends on: Blocks 6, 5
Unlocks: Block 9

**Block 9: Output Surfaces**
Build: HuggingFace dataset export, Streamlit leaderboard, paper tables/figures, pip package setup
Depends on: Blocks 7, 8
Unlocks: Block 10

**Block 10: Paper + Launch**
Write: ArXiv paper. Polish README. Deploy leaderboard to HF Spaces. Submit.
Depends on: Block 9
