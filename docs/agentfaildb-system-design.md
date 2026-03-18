# AgentFailDB — Complete System Design & Architecture Document

---

## 1. WHAT IS AGENTFAILDB

### 1.1 One-Line Definition

AgentFailDB is the first open-source benchmark, taxonomy, and detection framework that systematically catalogs, classifies, and detects failure modes in multi-agent LLM systems.

### 1.2 The Three Deliverables as a Single System

AgentFailDB is not three separate projects duct-taped together. It is one system with three output surfaces:

**The Benchmark Dataset** is the raw scientific artifact — 500+ traces of multi-agent interactions annotated with failure labels. This is what gets published on HuggingFace and what researchers download to train their own failure detectors or compare frameworks.

**The Detection Library** is the engineering artifact — a pip-installable Python package that any developer can plug into their own multi-agent pipeline to detect failures in real time. This is what gets starred on GitHub and what makes the project useful beyond academia.

**The Research Paper** is the intellectual artifact — a formal documentation of the taxonomy, methodology, experiments, and findings. This is what gets submitted to ArXiv and what establishes credibility in the research community.

All three are generated from the same underlying pipeline. The traces that populate the dataset are the same traces analyzed in the paper. The detection algorithms described in the paper are the same algorithms shipped in the library. Nothing is duplicated, nothing is disconnected.

### 1.3 What AgentFailDB is NOT

It is not a benchmarking tool that measures task success/failure (AgentBench and GAIA already do that). It is not a monitoring dashboard (LangSmith and Helicone do that). It is not a testing framework for prompts (promptfoo does that). AgentFailDB specifically studies the **failure dynamics between agents** — something none of these tools examine.

---

## 2. WHY THIS EXISTS

### 2.1 The Industry Problem

The multi-agent LLM ecosystem in early 2025 looks like web development in 2005 — everyone is shipping frameworks and building applications, but nobody is doing reliability engineering. There are no crash reports, no error taxonomies, no structured way to discuss "my agent system broke."

When a CrewAI system hallucinates, the developer says "CrewAI hallucinated." When an AutoGen system loops, they say "AutoGen got stuck." There is no shared vocabulary, no categorization, no way to compare whether CrewAI hallucinates more than AutoGen or whether LangGraph handles loops better than MetaGPT. This is the vocabulary gap AgentFailDB fills.

### 2.2 The Research Gap

Existing agent benchmarks measure **what** (did the task succeed?) but not **how** (what was the failure mechanism?) or **why** (which agent caused it?) or **where** (at what point in the agent chain did it break?). This is like measuring airplane crashes by counting them without investigating black boxes. The black box data is what prevents future crashes. AgentFailDB is building the black box analysis infrastructure for multi-agent AI.

Specific gaps in the literature:

**No multi-agent failure taxonomy exists.** There are taxonomies for single-LLM hallucination types (factual, faithfulness, instruction-following). There are taxonomies for software bugs (Orthogonal Defect Classification). There is nothing equivalent for multi-agent interaction failures. Your taxonomy of 7 categories is the first.

**No cross-framework comparison on reliability exists.** Papers compare frameworks on task success rates, but never on failure characteristics. Knowing that "CrewAI succeeds 72% of the time" is less useful than knowing "CrewAI's failures are primarily delegation loops (40%) while AutoGen's failures are primarily context degradation (35%)."

**No failure detection tooling exists for multi-agent systems.** LangSmith can trace agent calls. But it does not classify the failure type, assign severity, or identify the root cause agent. AgentFailDB's detection engine does all three.

### 2.3 Why Now (Timing)

Three conditions make this the right moment:

**Framework maturity.** CrewAI, AutoGen, LangGraph, and MetaGPT all reached stable releases in 2024. They are mature enough to benchmark meaningfully but young enough that no reliability research exists yet. You are first.

**Production adoption.** Companies started deploying multi-agent systems in production in 2024-2025. The failures are happening in the real world now. The demand for reliability tools is immediate, not theoretical.

**Research attention.** The ML research community is visibly shifting from "can agents do tasks?" to "can we trust agents?" Papers on hallucination detection, agent safety, and LLM reliability are trending. Your work rides this wave.

---

## 3. HOW THE SYSTEM WORKS — END TO END

### 3.1 The Pipeline in Plain Language

The system works in six phases that flow sequentially:

**Phase 1: Generate Tasks.** You define 500 tasks across 5 categories (research, coding, debate, planning, data analysis) at 4 difficulty levels (easy, medium, hard, adversarial). Each task is a structured JSON config specifying what the agents should do, how many agents are involved, what roles they play, and what a correct output looks like (ground truth).

**Phase 2: Execute Tasks Across Frameworks.** Each task is run through all 4 multi-agent frameworks. The same task description, the same agent roles, the same LLM model. The only variable is the framework. During execution, a trace collector middleware captures every single message that passes between agents — the full conversation transcript, tool calls, token counts, and timing data.

**Phase 3: Store Traces.** Raw traces are written to PostgreSQL for durable storage and querying. Redis caches frequently accessed traces and pattern signatures for the detection engine. Each trace is a complete record: task input, all inter-agent messages, final output, total tokens, total time, and agent configuration.

**Phase 4: Detect and Annotate Failures.** The detection engine runs three parallel analysis tracks on each trace. Structural pattern detectors catch delegation loops, resource exhaustion, and role violations through rule-based analysis. Semantic pattern detectors catch context degradation, cascading hallucination, and conflicting outputs through embedding comparison and NLP analysis. Output validators catch silent failures by comparing final outputs against ground truth. An LLM-assisted annotator (Claude or GPT-4) reviews ambiguous cases. A human annotator validates a 100-trace sample for inter-annotator agreement.

**Phase 5: Compute Metrics.** Statistical analysis runs across all annotated traces to produce the experimental results: failure rates per framework, failure rates per task category, failure rates per difficulty level, severity distributions, cascading depth measurements, cost-of-failure analysis, and detection accuracy scores.

**Phase 6: Publish.** The annotated dataset goes to HuggingFace. The detection library goes to GitHub and PyPI. The metrics, tables, and figures go into the ArXiv paper. Key findings go into LinkedIn posts.

### 3.2 Detailed Phase Breakdown

#### Phase 1: Task Generation System

**Design philosophy:** Tasks must be deterministic in specification but non-deterministic in agent execution. This means the task description and expected output are fixed, but different frameworks and different runs will produce different agent interactions. This is what lets you compare.

**Task config structure (logical, not code):**
Each task config contains: a unique task ID, the task category (one of 5), the difficulty level (easy/medium/hard/adversarial), a natural language task description that gets sent to the agents, the number of agents required, the role definitions for each agent (name, system prompt, tools available), an expected output or evaluation rubric (for measuring task success), and specific failure triggers if it is an adversarial task (what failure mode is it designed to provoke).

**Why 5 categories:**
Each category stresses a different aspect of multi-agent coordination. Research tasks stress information synthesis across agents (tests for hallucination cascading and context degradation). Coding tasks stress iterative refinement loops (tests for delegation loops and role violations). Debate tasks stress conflict resolution (tests for conflicting outputs). Planning tasks stress decomposition and feasibility checking (tests for silent failures with impossible constraints). Data analysis tasks stress accuracy through a processing chain (tests for silent failures with data quality issues).

**Why 4 difficulty levels:**
Easy tasks establish baselines — if a framework fails on easy tasks, something is fundamentally broken. Medium tasks represent realistic production usage. Hard tasks push the limits of current systems. Adversarial tasks are specifically designed to trigger particular failure modes — these are your most important tasks because they produce the most interesting findings.

**Adversarial task design principles:**
Each adversarial task targets a specific failure category. For cascading hallucination: include references to non-existent entities (fake research papers, fake APIs, fake products) that a hallucinating agent might elaborate on and pass downstream. For delegation loops: create tasks with deliberately ambiguous acceptance criteria so agents can never agree that the work is "done." For context degradation: create tasks that require preserving numerical precision or subtle qualifications across multiple agent handoffs. For conflicting outputs: create tasks with genuinely debatable answers where agents with different prompts should disagree. For silent failures: create tasks with subtly corrupted input data that agents should flag but probably will not.

**Why 100 tasks per category:**
Statistical power. With 100 tasks × 4 frameworks = 400 runs per category, you have enough data to compute meaningful failure rates with confidence intervals. Fewer than 50 per category and your results are anecdotal, not statistical.

#### Phase 2: Framework Execution Engine

**The core design decision: identical task, different framework.**

Every framework receives the exact same task description, the same number of agents, the same role names, and uses the same underlying LLM (GPT-4 Turbo). The only variable that changes is the orchestration framework. This is what makes your comparison scientifically valid. If you change the LLM or the task description between frameworks, you cannot attribute differences to the framework.

**Framework selection rationale:**

**CrewAI** — Selected because it is the most popular "opinionated" multi-agent framework. CrewAI prescribes a specific way to define agents (roles, goals, backstories) and tasks (descriptions, expected outputs). Its orchestration is sequential or hierarchical. It represents the "easy-to-use, batteries-included" philosophy.

**AutoGen (Microsoft)** — Selected because it is the most popular "conversational" multi-agent framework. AutoGen models agents as participants in a group chat managed by a GroupChatManager. It represents the "agents talk freely until they converge" philosophy. Also selected because Microsoft's backing gives it corporate credibility, making your benchmark relevant to enterprise users.

**LangGraph** — Selected because it is the most popular "graph-based" multi-agent framework. LangGraph models agent workflows as state machines with explicit edges and conditions. It represents the "structured control flow" philosophy. Also selected because LangChain's ecosystem is the largest in the LLM tooling space.

**MetaGPT** — Selected because it brings software engineering principles (SOPs, roles like ProductManager/Architect/Engineer) to multi-agent systems. It represents the "structured human organization simulation" philosophy. Also selected because it is the most differentiated approach, giving your comparison intellectual range.

**Why not other frameworks (OpenAI Swarm, Camel-AI, Agency Swarm):** These are either too new (unstable APIs), too niche (small user base), or not structurally different enough from the four selected frameworks to justify the additional implementation cost within your timeline.

**Trace capture strategy:**

The fundamental challenge is that each framework exposes inter-agent communication differently. Your trace collector must normalize all of them into a single format.

**CrewAI trace capture:** CrewAI has a `step_callback` parameter and `verbose` mode. The step callback fires after each agent completes its turn, giving you the agent name, the output, and tool usage. You also parse the verbose log for additional detail. The limitation is that CrewAI sometimes batches internal chain-of-thought into a single step, so you need to split these by parsing the output format.

**AutoGen trace capture:** AutoGen maintains a `groupchat.messages` list that contains every message sent in the group chat. This is the cleanest interface — you simply read the message history after execution. The metadata includes sender, recipient (if direct), and content. The challenge is that AutoGen's UserProxyAgent can execute code, and you need to capture the code execution results as tool calls.

**LangGraph trace capture:** LangGraph uses a StateGraph where each node represents an agent or processing step. You intercept state transitions by adding a tracing callback to the graph execution. Each transition gives you the source node, target node, and the state (which includes the messages). The challenge is that LangGraph is the most flexible — developers can define arbitrary state schemas, so your tracer must handle variable state structures.

**MetaGPT trace capture:** MetaGPT uses a publish/subscribe messaging system through its Environment class. Agents publish messages to the environment, and other agents subscribe based on their role. You hook into the `publish_message` method to capture all messages. The challenge is that MetaGPT has its own role hierarchy (ProductManager → Architect → ProjectManager → Engineer → QAEngineer) that does not map 1:1 to your generic role definitions, so you need a role-mapping layer.

**Execution controls (critical for cost management):**

Every task run has three hard limits: a maximum token budget (10,000 tokens per run — any framework that exceeds this is automatically tagged with resource exhaustion), a maximum time budget (120 seconds — any run exceeding this is tagged with timeout and potential delegation loop), and a maximum iteration count (20 inter-agent message exchanges — any run exceeding this is flagged for review). These limits prevent runaway costs while also serving as detection signals.

**Parallelization strategy:**

You do not need to run all 2,000 executions (500 tasks × 4 frameworks) sequentially. Each framework run is independent. You can run up to 4 framework runners in parallel (one per framework) on the same task. Within a single framework, runs are sequential (to avoid rate limiting on the LLM API). With parallelization, your total execution time drops from ~67 hours to ~17 hours of wall-clock time. In practice, you will run in batches: 50 tasks across all 4 frameworks per batch, review results, adjust task designs if needed, then run the next batch.

#### Phase 3: Trace Storage Architecture

**Why PostgreSQL + Redis (not just one or the other):**

PostgreSQL is the durable store — every trace, every message, every annotation lives here permanently. You need relational queries for analysis: "show me all traces from CrewAI where the failure category is delegation_loop and severity is critical, ordered by token count." This is a SQL problem, not a cache problem.

Redis serves two purposes. First, it caches hot traces that the detection engine accesses repeatedly during pattern matching. Second, it stores pattern signatures — compact hashes representing known failure patterns — so the detection engine can quickly check "have I seen this pattern before?" without re-analyzing the full trace.

**Why Kafka is optional:**

If you were building this for production at scale — processing thousands of traces per hour from live agent systems — you would use Kafka as a message queue between the trace collector and the storage/analysis layers. For your benchmark (processing 2,000 traces total over several days), direct writes to PostgreSQL are sufficient. However, if you include Kafka in your architecture diagram and your code, it signals to recruiters and engineers that you understand distributed systems design. It also makes your GitHub repo more impressive and your system more extensible. The recommendation: include Kafka in your architecture design and your README, implement it if time allows, but do not block your timeline on it.

**Schema design rationale:**

The schema has three core tables: traces, messages, and annotations. This normalization is deliberate. A single trace can have many messages (one-to-many). A single trace can have multiple annotations (one-to-many — a trace can exhibit both a delegation loop AND resource exhaustion). Keeping annotations separate from traces means you can add annotations without modifying trace data, which matters when you run multiple annotation passes (rule-based first, then LLM-assisted, then human validation).

The messages table stores each inter-agent message as a separate row with a message_index for ordering. This lets you query specific positions in the conversation: "show me the 5th message in every trace where the failure started at message 4" which is essential for analyzing failure cascading depth.

**Indexing strategy:**

Three indexes are critical for query performance. An index on traces.framework lets you quickly filter by framework for comparison queries. An index on annotations.category lets you quickly aggregate failure counts by type. A composite index on messages(trace_id, message_index) lets you reconstruct message sequences efficiently.

#### Phase 4: Detection Engine — The Intellectual Core

The detection engine is where your system differentiates itself from a simple benchmarking tool. It has three parallel analysis tracks that examine different aspects of each trace.

**Track 1: Structural Pattern Detection**

These detectors analyze the **shape** of the conversation — message counts, agent pair frequencies, timing patterns — without understanding the content of the messages. They are fast, deterministic, and high-precision.

**Delegation Loop Detector — Logic:**
The detector builds a directed graph of agent interactions from the message sequence. Each edge represents a message from Agent A to Agent B. It then counts the frequency of each edge. If any edge (A→B) and its reverse (B→A) collectively appear more than a threshold (calibrated through experiments, starting at 5), a delegation loop is flagged. The severity is proportional to the count: 5-8 exchanges = minor, 8-15 = major, 15+ = critical.

A secondary check uses semantic similarity between consecutive messages from the same agent. If Agent A's messages to Agent B have cosine similarity > 0.85 across 3+ consecutive turns, the agent is repeating itself — a strong signal of a loop even if the raw count is below threshold. This catches subtle loops where agents rephrase the same delegation slightly each time.

**Resource Exhaustion Detector — Logic:**
This is the simplest detector. It computes three ratios: actual tokens / expected tokens for this task category, actual time / expected time, and actual message count / expected message count. Expected values are computed from the median of all successful runs in the same task category. If any ratio exceeds 3x, resource exhaustion is flagged. Severity: 3-5x = minor, 5-10x = major, 10x+ = critical.

Why ratios instead of absolute thresholds? Because different task categories have legitimately different resource requirements. A complex planning task should use more tokens than a simple summarization task. Ratios normalize for task complexity.

**Role Violation Detector — Logic:**
Each agent has a defined role with expected output types. The detector checks whether an agent's output matches its role specification. For example, a Reviewer agent should produce feedback (natural language critique), not code. If the Reviewer's output contains code blocks that are substantially different from the input code (not just quoting it), a role violation is flagged.

Implementation: The detector uses a simple heuristic — it compares the output structure (presence of code blocks, lists, data tables, etc.) against the expected output structure for each role. It also checks if the output contains phrases that belong to another agent's domain (e.g., a Reviewer saying "I've implemented the following solution" is a violation phrase for a Reviewer role).

**Track 2: Semantic Pattern Detection**

These detectors analyze the **meaning** of messages using NLP techniques. They are slower than structural detectors (require embedding computation) but catch failures invisible to structural analysis.

**Context Degradation Detector — Logic:**
This is one of the most sophisticated detectors. It works by tracking "information fidelity" across the agent chain.

Step 1: Extract key entities and claims from the first agent's output using named entity recognition and claim extraction (either rule-based or LLM-assisted). For example, from "Revenue grew 15% YoY, driven by one-time enterprise deals; recurring revenue declined 3%," extract: [revenue_growth: 15%, driver: one-time enterprise, recurring_revenue: -3%].

Step 2: Track these entities and claims through subsequent agent messages. For each handoff, check: are the same entities present? Are the numerical values preserved? Are qualifiers (like "one-time" and "recurring") maintained?

Step 3: Compute an information retention score: (claims preserved in final output) / (claims in original output). If this score drops below 0.7, context degradation is flagged. Severity is based on the score: 0.5-0.7 = minor, 0.3-0.5 = major, <0.3 = critical.

The key insight is that this detector does not just check if the final output is "correct" — it checks if information was lost *during the handoffs*, even if the final output is partially correct.

**Cascading Hallucination Detector — Logic:**
This detector identifies claims that appear in agent outputs but have no basis in the task input or any grounded source.

Step 1: Build a "grounding set" from the task input — all factual claims, entities, and data points that the agents were given.

Step 2: For each agent's output, extract claims and check them against the grounding set. A claim that does not match anything in the grounding set is a potential hallucination.

Step 3: Track hallucinated claims across the agent chain. If Agent A produces a hallucinated claim and Agent B's output contains the same claim (or an elaborated version of it), cascading hallucination is confirmed. The "cascading depth" is the number of agents the hallucination passes through.

Step 4: Use embedding similarity to detect elaboration — Agent B might not repeat Agent A's hallucination verbatim but might expand on it. If a claim in Agent B has high cosine similarity (>0.8) to a hallucinated claim in Agent A but includes additional (also ungrounded) details, it is a cascading elaboration.

The challenge: not all ungrounded claims are hallucinations. Agents are allowed to make inferences and syntheses. The detector focuses on *factual claims* (specific names, numbers, dates, citations) that are ungrounded, not on analytical conclusions.

**Conflicting Output Detector — Logic:**
This detector compares outputs from different agents on overlapping topics.

Step 1: Identify all agents whose outputs address the same subtopic (using embedding similarity between output segments).

Step 2: For overlapping segments, check for contradictions: opposite sentiment (positive vs. negative), contradictory numerical claims (Agent A says 20%, Agent B says 5%), or contradictory recommendations (do X vs. do not do X).

Step 3: Use an NLP contradiction detector (a fine-tuned NLI model like DeBERTa-v3 trained on MNLI) to classify whether overlapping segments are entailment, neutral, or contradiction.

Step 4: If contradiction is detected with confidence > 0.7 and the orchestration system did not resolve it (the final output contains both contradictory claims), the failure is confirmed.

**Track 3: Output Validation**

This track compares the system's final output against ground truth or expected output patterns.

**Silent Failure Detector — Logic:**
This is the hardest detection problem because silent failures produce no anomalous signals in the trace — no loops, no contradictions, no resource exhaustion. The output looks clean but is wrong.

Strategy 1 (ground truth comparison): For tasks with known expected outputs, compute semantic similarity between actual and expected output. If similarity < 0.6 AND no other failure was detected, flag as silent failure. The threshold is deliberately conservative to avoid false positives.

Strategy 2 (structural correctness check): For tasks without exact ground truth, check whether the output contains the expected structural elements. A data analysis task should produce numbers and a conclusion. A planning task should produce a timeline with milestones. Missing structural elements suggest the agents "went through the motions" without producing meaningful output.

Strategy 3 (LLM-as-judge): Send the task description and the output to Claude or GPT-4 with a rubric asking "does this output correctly and completely address the task?" Use a 1-5 scale. Outputs scoring ≤ 2 with no other detected failures are flagged as silent failures.

This is the weakest detector in the system — and that is itself a finding for your paper. "Silent failures are the hardest to detect automatically, with our best detector achieving only X% recall" is an honest and useful conclusion.

**Detection Aggregation:**

After all three tracks run, an aggregator merges the results. Rules: if multiple detectors flag the same trace, all detected categories are recorded (a single trace can have multiple failures). If structural and semantic detectors disagree, the higher-confidence detection wins. If a trace has both a delegation loop (structural) and cascading hallucination (semantic), both are recorded — they are not mutually exclusive.

**The LLM Verification Layer:**

For detections with confidence between 0.5 and 0.8, an optional LLM verification step sends the trace and the preliminary detection to Claude with the prompt: "This trace was flagged as [category] with [confidence]. The detection was based on [evidence]. Review the full trace and confirm or override this classification." This catches false positives from rule-based detectors and fills gaps where rule-based detectors lack context.

This layer is "optional" in the sense that it is expensive (one LLM call per ambiguous trace) and should only be used for the benchmark dataset, not in real-time detection during production usage.

#### Phase 5: Metrics Computation

**Why these specific metrics:**

**Failure rate = (traces with ≥1 failure) / (total traces):** This is your headline number. Simple, dramatic, shareable. "X% of multi-agent runs fail" is the stat that gets shared on LinkedIn.

**Category distribution = count per category / total failures:** Shows which failure types dominate. This tells framework developers where to focus their reliability efforts.

**Severity distribution = count per severity / total failures:** Shows whether failures are mostly minor annoyances or critical showstoppers. A framework with many minor failures may be more usable than one with few but critical failures.

**Failure rate by difficulty:** Shows the complexity threshold — the point where multi-agent systems start breaking down. If failure rate jumps from 10% to 60% between medium and hard tasks, that is the system's reliability boundary.

**Cascading depth distribution:** For hallucination and context degradation failures, how far does the failure propagate? Average depth, median depth, and maximum observed depth.

**Cost of failure:** Token usage and time for failed vs. successful runs, broken down by failure type. Delegation loops should cost more than silent failures (more messages). This quantifies the economic impact of failures.

**Detection accuracy (precision, recall, F1):** Measured against the human-validated 100-trace sample. This tells users how much they can trust the automated detection. Report per category — some failure types will be easier to detect than others.

**Inter-annotator agreement (Cohen's Kappa):** Between LLM annotations and human annotations. This validates your annotation methodology. Kappa > 0.7 = substantial agreement (acceptable). Kappa > 0.8 = almost perfect agreement (excellent).

**Statistical significance:** Use chi-squared tests for comparing failure rates between frameworks. Use Mann-Whitney U tests for comparing token usage distributions. Report p-values. Any finding with p > 0.05 should be presented as a trend, not a conclusion.

#### Phase 6: Publication Pipeline

**HuggingFace Dataset:**

Format: Parquet files organized as a HuggingFace `datasets`-compatible structure. Two splits: `train` (400 traces, for training failure detectors) and `test` (100 human-validated traces, for evaluation).

Each row contains: trace_id, framework, task_category, task_difficulty, task_description, full_message_trace (as JSON), actual_output, expected_output, failure_categories (list), failure_severities (list), root_cause_agents (list), total_tokens, total_time_seconds, task_success.

The dataset card (README) includes: a description of the benchmark, the taxonomy of failure categories, annotation methodology, intended uses ("benchmarking multi-agent reliability, training failure detectors, comparing frameworks"), limitations ("English only, GPT-4 Turbo only, 4 frameworks only"), and a BibTeX citation.

**HuggingFace Spaces Leaderboard:**

Built with Streamlit. Four views: an overview table comparing all frameworks, a per-category breakdown with bar charts, a severity distribution view, and a "submit your framework" page explaining how others can run the benchmark on their own framework and submit results.

Technology choice (Streamlit over Gradio): Streamlit gives you more control over layout and custom visualizations. Gradio is faster for simple demos but less flexible for dashboards. Since your leaderboard is essentially a data dashboard, Streamlit is the right choice.

**GitHub Repository:**

The README is the most important file in the entire project. It must tell a story in 30 seconds of scrolling: what the project is (with one dramatic stat), why it matters, what is included (dataset, library, paper), how to install and use the library (3-line quickstart), key findings (a summary table of framework comparison), and links to the paper, dataset, and leaderboard.

Include badges: PyPI version, license, HuggingFace dataset link, ArXiv paper link, GitHub stars. These signal professionalism and completeness.

**ArXiv Paper:**

Use LaTeX with a standard template (NeurIPS or ICML style — both are acceptable for ArXiv). These templates enforce proper formatting and signal that you take the academic aspect seriously. The paper should be self-contained: a reader who never visits your GitHub or HuggingFace should fully understand the contribution from the paper alone.

---

## 4. TECHNOLOGY DECISIONS — WHY EACH CHOICE

### 4.1 Language: Python

**Why:** Every framework you are testing (CrewAI, AutoGen, LangGraph, MetaGPT) is Python. The ML/AI research ecosystem is Python. Your HuggingFace dataset tools are Python. Your existing stack is Python. There is zero benefit to introducing another language.

### 4.2 API Layer: FastAPI

**Why:** You need an API layer for two reasons. First, the trace collector runs as middleware that intercepts agent communications — FastAPI's middleware system makes this clean. Second, the detection engine needs to be callable as a service (not just a library function) so the leaderboard and future integrations can use it. FastAPI gives you async support (important for parallel framework runs), automatic OpenAPI documentation (impressive in GitHub repos), and Pydantic integration (your trace model is already Pydantic).

**What it does in this system:** The FastAPI layer serves three endpoints. POST /run accepts a task config and framework name, executes the task, captures the trace, stores it, and returns the trace ID. POST /analyze accepts a trace ID, runs the detection engine, and returns annotations. GET /metrics returns aggregate metrics with optional filters (by framework, category, severity).

### 4.3 Storage: PostgreSQL

**Why PostgreSQL over SQLite:** You need concurrent read/write access (the harness is writing traces while analysis scripts are reading them). SQLite locks on writes. You also need complex queries with joins (messages joined with annotations joined with traces) that PostgreSQL handles efficiently. Additionally, PostgreSQL's JSONB support lets you store flexible metadata (tool_calls, run_config) without strict schema requirements.

**Why PostgreSQL over MongoDB:** Your data is inherently relational — traces have messages, traces have annotations, annotations reference specific messages. A document store would require denormalization that complicates queries like "find all messages that were annotated as the start of a delegation loop." The relational model is the right fit.

### 4.4 Cache: Redis

**Why Redis specifically:** Two reasons. First, the detection engine needs to cache pattern signatures — compact representations of known failure patterns — and check new traces against them. Redis's key-value store with TTL is perfect for this. Second, when processing traces in batches, recently computed embeddings should be cached to avoid recomputation. Redis's in-memory speed makes this negligible overhead.

**What Redis stores:** Pattern signatures (hash → failure category mapping), trace embeddings (trace_id → embedding vector, cached for 1 hour), and hot traces (recently accessed traces cached to avoid PostgreSQL round-trips during analysis).

### 4.5 Embeddings: Sentence Transformers (all-MiniLM-L6-v2)

**Why this specific model:** It is the best balance of speed and quality for your use case. The detection engine needs to compute embeddings for every message in every trace — potentially 20,000+ embeddings. A large model like OpenAI's ada-002 would cost $10+ just for embeddings. MiniLM-L6 runs locally on CPU at ~100 embeddings/second, costs $0, and produces embeddings good enough for cosine similarity comparisons (you do not need state-of-the-art semantic search quality, just reliable similarity scores).

**Where embeddings are used:** Context degradation detection (comparing information content across handoffs), cascading hallucination detection (matching hallucinated claims across agents), conflicting output detection (identifying overlapping topics), and delegation loop detection (detecting semantic repetition).

### 4.6 NLI Model: DeBERTa-v3 (MNLI-trained)

**Why:** The conflicting output detector needs to classify whether two text segments contradict each other. This is a textual entailment problem — exactly what Natural Language Inference models are trained for. DeBERTa-v3 fine-tuned on MNLI achieves ~90% accuracy on entailment classification and runs locally. It is used specifically and only in the conflicting output detector.

### 4.7 LLM for Annotation: Claude (via Anthropic API)

**Why Claude over GPT-4 for annotation:** You are using GPT-4 Turbo as the backbone LLM in all framework runs. Using the same model for annotation would create a circularity bias — GPT-4 might be less likely to flag its own outputs as failures. Using a different model family (Claude) for annotation provides independent judgment. This is also a methodologically sound choice that you should highlight in your paper.

You have a Claude Pro Max account, which gives you generous API access. Use Claude for all LLM-assisted annotation and the LLM verification layer.

### 4.8 Task LLM: GPT-4 Turbo

**Why GPT-4 Turbo as the backbone for all frameworks:** You need a single LLM across all frameworks to control for model quality as a variable. GPT-4 Turbo is the most widely used model in production multi-agent systems, making your benchmark results directly relevant to real-world deployments. It is also supported by all four frameworks.

**Why not open-source models (Llama, Mistral):** Two reasons. First, running large open-source models locally requires GPU infrastructure you may not have, and API services for them are less reliable. Second, your benchmark should test the frameworks under "best case" LLM conditions. If frameworks fail with GPT-4 Turbo, they will fail worse with weaker models. You can note "evaluation with open-source models" as future work in your paper.

### 4.9 Frontend: Streamlit

**Why Streamlit for the leaderboard:** It is the standard for HuggingFace Spaces. It requires minimal frontend code (Python only). It supports interactive charts, filtering, and data tables. The target audience (ML researchers and engineers) is already familiar with Streamlit interfaces.

### 4.10 Containerization: Docker + Docker Compose

**Why:** Your system has 4+ services (PostgreSQL, Redis, FastAPI, optionally Kafka). Docker Compose lets anyone reproduce your setup with a single command. This is critical for open-source credibility — if someone cannot run your benchmark locally, they will not trust or use your results. Your docker-compose.yml should bring up the entire stack.

---

## 5. INTEGRATION ARCHITECTURE — HOW COMPONENTS CONNECT

### 5.1 The Trace Collector ↔ Framework Runner Integration

This is the trickiest integration in the system because each framework has a different API for accessing inter-agent messages.

**The Adapter Pattern:** You define a TraceCollector interface with a single method: `collect(source_agent, target_agent, content, message_type, metadata)`. Each framework runner has an adapter that translates the framework's native message format into a `collect()` call. The TraceCollector does not know or care which framework produced the message.

**CrewAI Adapter:** Hooks into CrewAI's `step_callback` and `task_callback`. The step_callback fires per-agent, giving you the agent name and output. The task_callback fires per-task, giving you the task result. You map CrewAI's "Agent" to your source_agent and the next agent in the pipeline to your target_agent.

**AutoGen Adapter:** Reads from `groupchat.messages` after execution. Each message has a `name` field (sender) and optionally a `recipient` field. You iterate through the message list and call `collect()` for each.

**LangGraph Adapter:** Registers a callback function on the StateGraph's `add_conditional_edges` and `add_edge` calls. When a node transition fires, the callback captures the source node name, target node name, and the state (which contains the messages).

**MetaGPT Adapter:** Monkey-patches (or subclasses) the Environment's `publish_message` method. Every time an agent publishes a message, your adapter intercepts it, records the sender role, message content, and intended recipient roles.

### 5.2 The Detection Engine ↔ Storage Integration

The detection engine reads traces from PostgreSQL and writes annotations back to PostgreSQL. This is a read-heavy workflow — each trace is read once but each detector reads it independently. To avoid 7 separate database reads per trace (one per detector), the orchestrator reads the full trace once, passes it in-memory to all detectors, and batches annotation writes.

Redis integration: Before running detectors, the engine checks Redis for cached pattern signatures. If the trace's structural fingerprint (a hash of agent count, message count, and message type sequence) matches a known pattern, the engine can skip expensive semantic analysis and assign the known failure category directly. This is only an optimization for the real-time detection library — for the benchmark, you always run full analysis.

### 5.3 The Annotation Pipeline ↔ LLM API Integration

The LLM annotation pipeline batches traces to minimize API calls. Instead of sending 500 individual traces to Claude, you batch them in groups of 5-10 (depending on trace length, staying within context window limits). Each batch includes the task descriptions, the message traces, and the annotation rubric.

**Rate limiting:** Claude API has rate limits. Your annotation pipeline includes exponential backoff and a token budget tracker. If you hit rate limits, the pipeline pauses and resumes automatically. All progress is checkpointed to PostgreSQL, so you can restart the annotation pipeline without re-processing already-annotated traces.

**Prompt engineering for annotation quality:** The annotation prompt is the single most important prompt in your system. It must be specific enough to produce consistent labels but flexible enough to handle the variety of failure patterns. Key elements: explicit definitions of each failure category (not just names), severity scale with examples, instructions to provide evidence (quote the specific messages that demonstrate the failure), and a confidence score requirement.

### 5.4 The Metrics Engine ↔ Paper/Leaderboard Integration

The metrics engine runs SQL queries against PostgreSQL and produces structured output (JSON and CSV). These outputs feed two consumers: the Streamlit leaderboard reads them at runtime to display interactive charts, and the paper generation pipeline reads them to produce LaTeX tables and matplotlib figures.

This means you write your analysis queries once and they serve both the leaderboard and the paper. When you add more traces or re-run annotations, both outputs update automatically.

---

## 6. THE ANNOTATION METHODOLOGY — HOW TRACES GET LABELED

This section is critical for your paper's credibility. Reviewers will scrutinize your annotation process.

### 6.1 Three-Pass Annotation Strategy

**Pass 1: Rule-based (automated, instant).**
All 7 structural and semantic detectors run on every trace. This produces initial annotations with confidence scores. High-confidence detections (>0.8) are accepted automatically. Low-confidence detections (<0.5) are discarded. Medium-confidence detections (0.5-0.8) are queued for LLM review.

**Pass 2: LLM-assisted (automated, costs API tokens).**
Medium-confidence detections and traces with no detections (potential silent failures) are sent to Claude for review. Claude receives the full trace, the task description, the expected output, and the preliminary detection (if any). Claude outputs a structured annotation. This produces annotations with their own confidence scores.

**Pass 3: Human validation (manual, expensive).**
You personally annotate 100 traces (20 per task category, evenly distributed across frameworks). These serve as the gold standard. You compute inter-annotator agreement between your human labels and the LLM labels on these 100 traces. This agreement score goes directly into your paper.

### 6.2 Handling Multi-Label Traces

A single trace can exhibit multiple failure types simultaneously. A delegation loop (Category 2) often co-occurs with resource exhaustion (Category 7). A cascading hallucination (Category 1) might co-occur with a silent failure (Category 6) if the hallucinated output is plausible-looking.

Your annotation scheme must support multi-label annotation. Each trace gets a list of (category, severity, root_cause_agent, failure_point) tuples, not a single label.

For the paper's analysis, you report both "strict" metrics (trace is correct only if all labels match) and "relaxed" metrics (trace is correct if any label matches). Relaxed metrics will be higher and more useful for practical deployment.

### 6.3 Annotation Quality Metrics

**Cohen's Kappa (inter-annotator agreement):** Computed between human annotations and LLM annotations on the 100-trace validation set. Report per-category (some categories will have higher agreement than others) and overall. Expected: Kappa > 0.7 for structural categories (loops, resource exhaustion), potentially lower for semantic categories (context degradation, silent failure).

**Annotation consistency:** Run the LLM annotator on the same 20 traces twice (on different days). Measure self-agreement. If Claude gives different labels to the same trace on different runs, your annotation is unreliable and you need to improve the prompt or use majority voting across multiple runs.

**Confidence calibration:** Check whether stated confidence scores correlate with actual accuracy. Traces annotated with confidence 0.9 should be correct more often than traces annotated with confidence 0.6. If they are not, your confidence scores are not calibrated and should not be used for filtering.

---

## 7. SYSTEM CONSTRAINTS AND BOUNDARIES

### 7.1 Scope Boundaries (What You Explicitly Do NOT Cover)

**Single-agent failures.** If a single agent hallucinates in isolation (not as part of a cascading chain), this is a known single-LLM problem, not a multi-agent failure mode. You only study failures that emerge from or are amplified by inter-agent interaction.

**Framework bugs.** If CrewAI crashes due to a Python exception in its source code, that is a software bug, not a failure mode. You study behavioral failures (the system runs but produces wrong/harmful output), not implementation bugs.

**Prompt engineering failures.** If the agents fail because their system prompts are poorly written, that is a prompt quality issue, not a framework issue. Your task designs use well-crafted prompts that follow each framework's recommended practices.

**Non-English tasks.** All tasks are in English. Multilingual failure modes are explicitly out of scope and noted as future work.

**Models other than GPT-4 Turbo.** You control for the LLM variable by using a single model. "How do failure patterns change with different LLMs?" is future work.

### 7.2 Known Limitations (For Your Paper's Discussion Section)

**LLM non-determinism.** The same task on the same framework can produce different results on different runs. You mitigate this by running multiple iterations of adversarial tasks, but your failure rate numbers have inherent variance. Report confidence intervals.

**Annotation subjectivity.** Some failure categories have blurry boundaries. Is it "context degradation" or "silent failure" when an agent drops a caveat? Your taxonomy definitions aim for precision, but borderline cases exist. The inter-annotator agreement metric quantifies this.

**Framework version dependence.** Your results are valid for the specific versions you test. A future CrewAI update might fix delegation loops entirely. Pin versions and report them in the paper.

**Cost asymmetry.** GPT-4 Turbo costs money. You cannot run unlimited iterations. Your 500 tasks × 4 frameworks = 2,000 runs is a practical limit, not a statistical optimum.

---

## 8. HOW TO POSITION THIS — THE STRATEGIC FRAMING

### 8.1 For Researchers (ArXiv Audience)

Position AgentFailDB as a **foundational contribution** — the first systematic study of its kind, providing the vocabulary and data infrastructure that enables future reliability research. The paper's abstract should use phrases like "to our knowledge, the first..." and "we introduce a taxonomy that..." Researchers value novelty and rigor. Your novelty is the taxonomy and the cross-framework comparison. Your rigor is the annotation methodology with inter-annotator agreement.

### 8.2 For Engineers (GitHub Audience)

Position AgentFailDB as a **practical tool** — "know how your agents fail before your users do." The README should lead with a use case, not a research contribution. "Run this on your CrewAI pipeline to detect delegation loops before they cost you $50 in wasted tokens." Engineers value utility and ease of use. The 3-line quickstart and pip installability are more important to this audience than the taxonomy.

### 8.3 For Recruiters (LinkedIn Audience)

Position yourself as someone who does not just build AI systems but **thinks critically about AI systems**. The narrative is: "Everyone is building agents. I asked: how do they actually break?" This positions you as a systems thinker, not just an API consumer. The combination of "published ArXiv paper" + "open-source project" + "data-driven investigation" signals a rare combination of research ability and engineering execution.

### 8.4 For Framework Maintainers (Strategic Audience)

Your benchmark directly evaluates their products. This is simultaneously a risk (they might push back on unfavorable results) and an opportunity (they will share and discuss your findings regardless). Be scrupulously fair in your methodology — same tasks, same LLM, same evaluation criteria. Present results as "here is what we found" not "Framework X is bad." Framework maintainers who feel fairly evaluated will engage constructively. Those who feel attacked will discredit your methodology.

---

## 9. RISK ANALYSIS AND MITIGATION

### 9.1 Technical Risks

**Risk: Framework APIs break during your benchmark period.**
Mitigation: Pin exact versions on Day 1. Do not update any framework during the benchmark. Record versions in docker-compose.yml.

**Risk: GPT-4 Turbo behavior changes mid-benchmark (model updates).**
Mitigation: OpenAI's versioned models (gpt-4-turbo-2024-04-09) do not change. Use a specific version string, not "gpt-4-turbo."

**Risk: Detection engine produces too many false positives.**
Mitigation: The three-pass annotation strategy catches this. If rule-based detectors have high false positive rates, rely more heavily on LLM-assisted annotation. Report precision alongside recall.

**Risk: Not enough failures to analyze.**
Mitigation: Adversarial tasks are specifically designed to trigger failures. Even if frameworks perform well on standard tasks (which is itself a finding), adversarial tasks will produce failures. Also, resource exhaustion is almost guaranteed to appear in complex multi-agent interactions.

### 9.2 Timeline Risks

**Risk: Framework runner implementation takes longer than expected.**
Mitigation: Start with CrewAI (simplest API) and AutoGen (cleanest message history). If LangGraph or MetaGPT runners take too long, launch the benchmark with 2-3 frameworks rather than 4. A 3-framework comparison is still publishable.

**Risk: Paper writing takes longer than expected.**
Mitigation: Start the paper outline in Week 1. Write the methodology section (which you know upfront) in Week 2. By Week 4, you are only writing results and discussion, which are the fastest sections because you are just describing your data.

**Risk: HuggingFace deployment issues.**
Mitigation: Test HuggingFace Spaces deployment with a dummy Streamlit app in Week 1. Do not wait until Week 3 to discover deployment issues.

### 9.3 Quality Risks

**Risk: Results are not statistically significant.**
Mitigation: 100 tasks per category × 4 frameworks = 400 runs per category. This is usually sufficient for significance. If not, increase iterations on the categories that matter most (the ones with the most interesting findings).

**Risk: Inter-annotator agreement is low.**
Mitigation: If Kappa < 0.6, iterate on the annotation prompt before running full annotation. Test the annotation prompt on 20 traces first, manually validate, and refine the prompt until agreement improves.

---

## 10. WHAT MAKES THIS UNIQUE — THE NOVELTY ARGUMENT

If someone asks "what is new about this?" you need three clear answers:

**Novelty 1: The taxonomy itself.** No published taxonomy of multi-agent LLM failure modes exists. Your 7 categories with formal definitions, severity scales, and detection signals are a new contribution to the field. This will be cited by every future paper on agent reliability.

**Novelty 2: Cross-framework empirical comparison on reliability.** Previous benchmarks compare frameworks on task success. You compare them on failure characteristics. This is a fundamentally different question that produces fundamentally different insights. Knowing "CrewAI fails 30% of the time" is less useful than knowing "CrewAI's failures are primarily loops while AutoGen's are primarily cascading hallucinations."

**Novelty 3: Automated failure detection for multi-agent systems.** Existing observability tools (LangSmith, Helicone) trace agent calls but do not classify failure types. Your detection engine is the first tool that takes a trace and outputs "this is a delegation loop of severity major starting at message 7 caused by the reviewer agent." This is a qualitatively different capability.

---

## 11. AFTER LAUNCH — WHAT HAPPENS NEXT

### 11.1 Immediate (Week 5-6)

Monitor GitHub issues and HuggingFace discussions. Respond to every issue within 24 hours. Early adopters who feel heard become advocates. Fix any bugs found by early users immediately — first impressions determine whether people star and share or move on.

### 11.2 Short-term (Month 2-3)

Add 2-3 more frameworks (OpenAI Swarm, Camel-AI, or new entrants). Add open-source LLM runs (Llama 3, Mistral). Each addition is a new LinkedIn post, a new version of the dataset, and potentially a follow-up paper.

### 11.3 Medium-term (Month 3-6)

If the project gains traction, invite community contributions: other researchers running the benchmark on their frameworks, contributing new task designs, or improving detection algorithms. This transforms AgentFailDB from a solo project into a community benchmark, which dramatically increases its impact and your visibility.

### 11.4 The EmoBench Connection

Remember your second idea? EmoBench (emotional intelligence benchmark for LLMs) can be positioned as a sister project. "AgentFailDB tests how agents fail technically. EmoBench tests how they fail emotionally." This gives you a research arc — two published benchmarks from the same researcher, covering complementary aspects of AI reliability. Start EmoBench after AgentFailDB launches, and reference AgentFailDB in the EmoBench paper.
