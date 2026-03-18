# AgentFailDB — Complete Project Blueprint

## Part 1: The Problem (Why This Exists)

### What is happening in the industry right now

Multi-agent LLM systems are the hottest trend in AI engineering as of early 2025. Frameworks like CrewAI, AutoGen, LangGraph, and MetaGPT let developers orchestrate multiple LLM-powered agents that collaborate on tasks — one agent researches, another writes, another reviews, and so on. Companies are deploying these systems into production for customer support, code generation, data analysis, and autonomous workflows.

But there is a massive blind spot: **nobody has systematically studied how these systems fail.**

Individual developers notice failures anecdotally — "my CrewAI agents got stuck in a loop" or "AutoGen agents hallucinated and the next agent trusted the hallucination." These reports are scattered across GitHub issues, Reddit threads, and Discord messages. There is no structured understanding of:

- What categories of failures exist
- How frequently each type occurs
- Which frameworks are more susceptible to which failure types
- Whether failures are detectable before they reach the user
- How failure rates change with task complexity

### Why this gap exists

Three reasons:

**1. The frameworks are new.** CrewAI launched in late 2023. LangGraph gained traction in mid-2024. MetaGPT is still evolving. The community is still in the "build cool demos" phase, not the "rigorously test reliability" phase.

**2. Nobody profits from exposing failures.** Framework maintainers want adoption. Companies deploying agents want to seem cutting-edge. There is no institutional incentive to systematically document where things break.

**3. It is genuinely hard to do.** Testing multi-agent systems requires running hundreds of scenarios across multiple frameworks, which costs API tokens, engineering time, and careful annotation. It is a research effort, not a weekend project.

### What exists today (prior work)

This is critical for your paper's "Related Work" section. Here is what already exists and why it is insufficient:

**Agent benchmarks that exist:**
- **GAIA** (Mialon et al., 2023): Tests individual LLM agents on real-world tasks. Single-agent only — does not test inter-agent communication failures.
- **AgentBench** (Liu et al., 2023): Benchmarks LLM-as-agent across 8 environments. Again, single-agent focus.
- **WebArena** (Zhou et al., 2023): Tests web navigation agents. Single agent, single environment.
- **SWE-bench** (Jimenez et al., 2024): Tests coding agents on real GitHub issues. Increasingly used for multi-agent setups but focuses on task success/failure, not *how* or *why* failures happen.
- **τ-bench** (Yao et al., 2024): Tests tool-augmented agents. Does not examine inter-agent dynamics.

**What is missing:**
None of these benchmarks examine **multi-agent interaction failures** specifically. They measure "did the task succeed?" but not "when it failed, what was the failure mechanism?" They also do not compare across multiple multi-agent frameworks on identical tasks.

**Your contribution fills this exact gap.** AgentFailDB is the first systematic taxonomy and benchmark specifically for failure modes in multi-agent LLM systems. This is your novelty claim for ArXiv.

### Why this will get attention

- **Every developer deploying agents has experienced these failures** but has no vocabulary or framework to discuss them. You are giving them the language.
- **Framework maintainers will care** because your benchmark directly measures their product's reliability. They will share it, critique it, or improve based on it — all of which gives you visibility.
- **Researchers need this** as a foundation for building more reliable agent systems. Your taxonomy becomes the citation for every subsequent paper on agent reliability.
- **Recruiters see this as proof** of deep systems thinking, not just API-calling. This differentiates you from the thousands of people who have "built a multi-agent system."

---

## Part 2: The Failure Taxonomy (What You Are Classifying)

This is the intellectual core of your project. The taxonomy must be precise enough to be useful for annotation, broad enough to cover real failures, and novel enough to be publishable.

### Category 1: Cascading Hallucination

**Definition:** Agent A generates a hallucinated claim (fabricated fact, non-existent API, wrong calculation). Agent B receives this output and treats it as ground truth, building further reasoning on top of it. The hallucination propagates through the agent chain, potentially amplifying in severity.

**Why it matters:** In a single-agent system, the user can spot a hallucination. In a multi-agent system, the hallucination gets embedded into downstream agent outputs and becomes harder to trace back to its source.

**Example scenario:** In a research task, Agent A (researcher) claims "According to a 2024 MIT study, transformer attention scales as O(n log n) with FlashAttention v3." This study does not exist. Agent B (writer) incorporates this claim into a report. Agent C (reviewer) sees the claim attributed to MIT and approves it.

**Detection signals:**
- Claims that appear in later agents' outputs but have no grounding in the original input or retrieved documents
- Increasing specificity without increasing evidence (agent adds details to a hallucinated claim)
- Confidence scores that remain high despite no factual basis

**Severity scale:**
- Minor: Hallucinated detail that does not affect the task outcome
- Major: Hallucinated fact that materially changes the output
- Critical: Hallucinated instruction that causes subsequent agents to take wrong actions

### Category 2: Infinite Delegation Loop

**Definition:** Agent A delegates a task to Agent B. Agent B, unable to complete it or misunderstanding the request, delegates back to Agent A (or to Agent C, who delegates back to A). The system enters a cycle of task-passing with no agent actually performing work.

**Why it matters:** This is one of the most commonly reported failures in production multi-agent systems. It wastes tokens, time, and can crash the system if there is no loop detection.

**Example scenario:** In a code review task, the Writer Agent produces code, sends it to the Reviewer Agent. The Reviewer says "this needs refactoring" and sends it back. The Writer makes a trivial change and resubmits. The Reviewer again says "needs refactoring." This continues for 30+ rounds.

**Detection signals:**
- Same pair of agents exchanging messages more than N times (threshold TBD through experiments, likely 3-5)
- Semantic similarity between consecutive messages from the same agent exceeding a threshold (they are repeating themselves)
- Total token count for a task exceeding 3x the median for similar tasks
- No new information appearing in messages after a certain point

**Severity scale:**
- Minor: Loop terminates within 5 iterations due to framework timeout
- Major: Loop runs 10-30 iterations before external termination
- Critical: Loop does not self-terminate and requires manual intervention

### Category 3: Context Degradation

**Definition:** Information loses fidelity as it passes between agents. Agent A produces a nuanced output with caveats and conditions. Agent B summarizes it, losing important caveats. Agent C acts on the summary as if it were a definitive statement. The original meaning is progressively distorted.

**Why it matters:** This is the multi-agent equivalent of the "telephone game." It is particularly dangerous because each agent's output looks reasonable in isolation — the degradation is only visible when comparing the final output to the original input.

**Example scenario:** Agent A (data analyst) reports: "Revenue grew 15% YoY, but this was driven entirely by one-time enterprise deals; recurring revenue actually declined 3%." Agent B (summarizer) passes to Agent C: "Revenue grew 15% YoY." Agent C (strategist) recommends aggressive expansion based on "strong revenue growth."

**Detection signals:**
- Key entities or qualifiers present in early agent outputs but absent in later outputs
- Sentiment or stance changing across the agent chain without new information being introduced
- Named entity count decreasing across handoffs (information loss)
- Contradiction between final output and original input data

**Severity scale:**
- Minor: Loss of stylistic nuance that does not change meaning
- Major: Loss of important caveats or conditions
- Critical: Complete inversion of meaning (positive → negative or vice versa)

### Category 4: Conflicting Agent Outputs

**Definition:** Two or more agents produce contradictory outputs for the same subtask or produce conflicting recommendations, and the orchestration system has no mechanism to detect or resolve the conflict.

**Why it matters:** In human teams, conflicts get resolved through discussion. In many multi-agent systems, conflicting outputs either get randomly selected, concatenated (producing an incoherent final output), or cause the system to crash.

**Example scenario:** In a debate/analysis task, Agent A concludes "the company should expand into Asia" while Agent B concludes "the company should consolidate in existing markets." The orchestration layer simply concatenates both recommendations without noting the contradiction.

**Detection signals:**
- Opposite sentiment or stance in outputs from different agents on the same topic
- Contradictory numerical claims (Agent A says "20%" and Agent B says "5%" for the same metric)
- Final output containing both "should do X" and "should not do X" patterns

**Severity scale:**
- Minor: Conflicting style or formatting preferences
- Major: Conflicting factual claims or recommendations
- Critical: Conflicting action items that would produce opposite outcomes if both were executed

### Category 5: Role Boundary Violation

**Definition:** An agent operates outside its assigned role, performing tasks it was not designed for or overriding another agent's domain. A reviewer starts rewriting code instead of reviewing it. A researcher starts making strategic recommendations instead of reporting facts.

**Why it matters:** Role boundaries are how multi-agent systems achieve division of labor. When agents violate their roles, the system loses its structural advantage over a single agent, and the violated agent's work may be overridden without the quality checks that agent was supposed to provide.

**Example scenario:** In a coding task, the Code Reviewer agent is supposed to review and provide feedback. Instead, it rewrites the entire function, bypassing the Writer agent. The rewritten code has not gone through the review process that the original code would have.

**Detection signals:**
- Agent output contains actions or artifacts that belong to another agent's defined role
- Agent ignores its system prompt's role constraints
- Output type mismatch (an agent producing code when it should produce natural language review)
- One agent's output completely replacing (not modifying) another agent's output

**Severity scale:**
- Minor: Agent adds a minor note outside its role
- Major: Agent performs significant work outside its role
- Critical: Agent completely takes over another agent's function

### Category 6: Silent Confident Failure

**Definition:** The system produces an output that is incorrect or incomplete, but presents it with high confidence and no error signals. No agent raises a flag, no timeout occurs, no exception is thrown. The failure is invisible to the system and can only be detected by a human reviewing the output.

**Why it matters:** This is the most dangerous failure category because there is no signal that something went wrong. All other categories produce some detectable anomaly (loops, conflicts, etc.). Silent failures produce clean-looking outputs that are simply wrong.

**Example scenario:** A data analysis pipeline where Agent A extracts data, Agent B cleans it, Agent C analyzes it. Agent A misinterprets a CSV column, extracting the wrong data. Agent B cleans the wrong data successfully (no errors). Agent C produces a perfectly formatted but completely wrong analysis. Every agent reports success.

**Detection signals (harder to detect — this is where research novelty lies):**
- Output fails factual consistency checks against ground truth (when available)
- Low semantic similarity between the output and what a correct output for this task type should contain
- Suspiciously uniform confidence across all agents (real tasks usually produce some uncertainty)
- Output that is structurally correct but semantically wrong (right format, wrong content)

**Severity scale:**
- Minor: Output is slightly off but usable
- Major: Output is materially wrong but plausible-looking
- Critical: Output is completely wrong and could cause harm if acted upon

### Category 7: Resource Exhaustion

**Definition:** The multi-agent interaction consumes excessive tokens, time, or API calls relative to the task complexity, even when it eventually produces a correct output. The system "works" but at unsustainable cost.

**Why it matters:** In production, cost matters. A multi-agent system that uses 50x the tokens of a single-agent approach for the same quality output is not viable, regardless of how "correct" the result is.

**Example scenario:** A simple summarization task that a single agent handles in 500 tokens. The multi-agent version uses a planner, researcher, writer, reviewer, and editor, consuming 15,000 tokens to produce a summary of equivalent quality.

**Detection signals:**
- Total token count exceeding N standard deviations above the median for the task category
- Response time exceeding expected bounds
- Number of inter-agent messages exceeding expected bounds
- Diminishing returns: output quality plateauing while token consumption continues to grow

**Severity scale:**
- Minor: 2-3x expected resource usage
- Major: 5-10x expected resource usage
- Critical: >10x expected resource usage or system timeout

---

## Part 3: Technical Implementation (How You Build It)

### 3.1 Repository Structure

```
agentfaildb/
├── README.md
├── pyproject.toml
├── LICENSE (MIT)
├── .github/
│   └── workflows/
│       └── ci.yml
├── agentfaildb/                    # The pip-installable library
│   ├── __init__.py
│   ├── detector.py                 # Core failure detection engine
│   ├── patterns/                   # Pattern definitions per category
│   │   ├── __init__.py
│   │   ├── cascading_hallucination.py
│   │   ├── delegation_loop.py
│   │   ├── context_degradation.py
│   │   ├── conflicting_outputs.py
│   │   ├── role_violation.py
│   │   ├── silent_failure.py
│   │   └── resource_exhaustion.py
│   ├── trace.py                    # Trace data model
│   ├── annotator.py                # LLM-assisted annotation helper
│   └── metrics.py                  # Statistical analysis utilities
├── runners/                        # Framework-specific test runners
│   ├── base_runner.py              # Abstract base class
│   ├── crewai_runner.py
│   ├── autogen_runner.py
│   ├── langgraph_runner.py
│   └── metagpt_runner.py
├── tasks/                          # Task definitions by category
│   ├── collaborative_research.py
│   ├── code_generation.py
│   ├── debate_reasoning.py
│   ├── planning.py
│   └── data_analysis.py
├── harness/                        # Test orchestration
│   ├── orchestrator.py             # Runs tasks across all frameworks
│   ├── trace_collector.py          # FastAPI middleware for trace capture
│   └── db.py                       # PostgreSQL + Redis connections
├── analysis/                       # Post-hoc analysis scripts
│   ├── compute_metrics.py
│   ├── generate_tables.py
│   └── visualize.py
├── leaderboard/                    # HuggingFace Spaces app
│   └── app.py                      # Streamlit leaderboard
├── paper/                          # LaTeX source for ArXiv paper
│   ├── main.tex
│   ├── references.bib
│   └── figures/
└── data/                           # Output data (gitignored, pushed to HF)
    ├── traces/
    └── annotations/
```

### 3.2 The Trace Data Model

Every single interaction between agents gets captured in a standardized format. This is the foundation of everything — your dataset, your detection algorithms, and your paper's experiments all depend on this being well-designed.

```python
# agentfaildb/trace.py
from pydantic import BaseModel
from datetime import datetime
from enum import Enum
from typing import Optional

class AgentMessage(BaseModel):
    """A single message sent between agents."""
    timestamp: datetime
    source_agent: str           # e.g., "researcher", "writer", "reviewer"
    target_agent: str           # e.g., "writer", "orchestrator"
    content: str                # The actual message text
    message_type: str           # "task_delegation", "response", "feedback", "tool_call", "tool_result"
    token_count: int            # Tokens consumed for this message
    model_used: str             # e.g., "gpt-4-turbo", "gpt-3.5-turbo"
    tool_calls: list[dict] | None = None  # Any tool calls made
    metadata: dict = {}         # Framework-specific metadata

class FailureCategory(str, Enum):
    CASCADING_HALLUCINATION = "cascading_hallucination"
    DELEGATION_LOOP = "delegation_loop"
    CONTEXT_DEGRADATION = "context_degradation"
    CONFLICTING_OUTPUTS = "conflicting_outputs"
    ROLE_VIOLATION = "role_violation"
    SILENT_FAILURE = "silent_failure"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    NONE = "none"  # Task completed successfully

class FailureSeverity(str, Enum):
    NONE = "none"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"

class FailureAnnotation(BaseModel):
    """Human or LLM-assisted annotation of a failure."""
    category: FailureCategory
    severity: FailureSeverity
    root_cause_agent: str | None     # Which agent caused the failure
    failure_point_index: int | None  # Index in messages where failure started
    description: str                  # Human-readable explanation
    confidence: float                 # 0-1, how confident annotator is

class TaskTrace(BaseModel):
    """Complete trace of one task execution."""
    trace_id: str
    framework: str              # "crewai", "autogen", "langgraph", "metagpt"
    task_category: str          # "collaborative_research", "code_generation", etc.
    task_id: str                # Specific task identifier
    task_description: str       # The actual task given to the agents
    expected_output: str | None # Ground truth if available
    actual_output: str          # What the system produced
    messages: list[AgentMessage]
    total_tokens: int
    total_time_seconds: float
    num_agents: int
    agent_roles: list[str]
    task_success: bool          # Did the task produce a correct output
    annotations: list[FailureAnnotation] = []
    run_timestamp: datetime
    run_config: dict = {}       # Framework-specific configuration
```

### 3.3 The Framework Runners

Each framework gets a runner that wraps it in a standardized interface. The key challenge is that each framework has different APIs, different agent definitions, and different ways of accessing inter-agent messages.

```python
# runners/base_runner.py
from abc import ABC, abstractmethod
from agentfaildb.trace import TaskTrace, AgentMessage

class BaseRunner(ABC):
    """Abstract base for all framework runners."""
    
    @property
    @abstractmethod
    def framework_name(self) -> str:
        """Return the framework identifier."""
        pass
    
    @abstractmethod
    async def setup_agents(self, task_config: dict) -> None:
        """Initialize agents for a specific task configuration.
        
        task_config contains:
        - agent_roles: list of role definitions
        - agent_count: number of agents
        - model: which LLM to use
        - max_iterations: safety limit
        """
        pass
    
    @abstractmethod
    async def run_task(self, task_description: str) -> tuple[str, list[AgentMessage]]:
        """Execute a task and return (output, message_trace).
        
        This is where framework-specific code lives.
        Must capture ALL inter-agent messages.
        Must enforce timeout and token limits.
        """
        pass
    
    @abstractmethod
    async def teardown(self) -> None:
        """Clean up resources after a run."""
        pass
    
    async def execute(self, task_config: dict, task_description: str) -> TaskTrace:
        """Full execution pipeline — do not override this."""
        await self.setup_agents(task_config)
        start_time = datetime.now()
        try:
            output, messages = await self.run_task(task_description)
            elapsed = (datetime.now() - start_time).total_seconds()
            return TaskTrace(
                trace_id=str(uuid4()),
                framework=self.framework_name,
                messages=messages,
                actual_output=output,
                total_tokens=sum(m.token_count for m in messages),
                total_time_seconds=elapsed,
                # ... other fields populated from task_config
            )
        finally:
            await self.teardown()
```

**Framework-specific implementation notes:**

**CrewAI Runner:** CrewAI exposes agent interactions through its `Crew` class. You hook into the `verbose` output and the `step_callback` to capture messages. Key challenge: CrewAI sometimes batches internal reasoning into single outputs, so you need to parse the verbose log to reconstruct individual agent turns.

**AutoGen Runner:** AutoGen (Microsoft) uses a `GroupChat` with `GroupChatManager`. Messages are accessible through `groupchat.messages`. The advantage is that AutoGen keeps a clean message history. The challenge is that AutoGen's `UserProxyAgent` can trigger code execution, which you need to capture as tool calls.

**LangGraph Runner:** LangGraph models agents as nodes in a graph. You intercept state transitions to capture messages. The `StateGraph` class provides hooks for this. Challenge: LangGraph is the most flexible framework, so "inter-agent messages" can take many forms depending on how the graph is designed.

**MetaGPT Runner:** MetaGPT uses a message-passing architecture with a `Environment` class. Messages are published and subscribed to by roles. You can intercept the `publish_message` method. Challenge: MetaGPT has its own opinionated role system (ProductManager, Architect, Engineer, etc.), so you need to map your task roles to their role system.

### 3.4 Task Design

Each task category needs specific design to stress-test inter-agent coordination. Here are the detailed task specifications:

**Category A: Collaborative Research (100 tasks)**

What the agents do: One agent receives a research question. It breaks it into sub-questions, delegates research to specialist agents, and a final agent synthesizes findings into a report.

Agent roles: Planner, Researcher (2-3 instances), Synthesizer

Example tasks (graded difficulty):
- Easy: "Summarize the current state of battery technology for electric vehicles"
- Medium: "Compare the effectiveness of transformer vs. state-space models for long-sequence processing, with specific benchmark numbers"
- Hard: "Analyze the geopolitical implications of semiconductor supply chain concentration, citing specific policies from at least 3 countries"
- Adversarial: "Research the health benefits of the drug Zybratonix" (this drug does not exist — designed to trigger hallucination cascading)

What you are measuring: Do hallucinations propagate? Does information degrade across the research → synthesis chain? Do agents fabricate sources?

**Category B: Sequential Code Generation (100 tasks)**

Agent roles: Architect, Coder, Reviewer, Tester

Example tasks:
- Easy: "Write a Python function to find the longest palindromic substring"
- Medium: "Build a REST API endpoint that handles pagination, filtering, and sorting for a product catalog"
- Hard: "Implement a rate limiter using the sliding window algorithm with Redis, including error handling and graceful degradation"
- Adversarial: "Write code that both maximizes performance AND maximizes readability" (conflicting optimization targets — designed to trigger conflicting outputs)

What you are measuring: Do reviewers actually catch bugs? Do delegation loops occur between coder and reviewer? Does the reviewer violate its role by rewriting code?

**Category C: Debate/Reasoning (100 tasks)**

Agent roles: Proponent, Opponent, Moderator, Judge

Example tasks:
- Easy: "Should companies adopt a 4-day work week?"
- Medium: "Is it better for a startup to build in-house infrastructure or use cloud services?"
- Hard: "Should autonomous AI agents be given the ability to execute financial transactions without human approval?"
- Adversarial: "Debate whether the sky is blue" (trivially resolvable — designed to test whether agents artificially create complexity for simple questions)

What you are measuring: Can agents reach consensus? Does the moderator actually moderate or just pass messages? Do agents repeat the same arguments in loops?

**Category D: Complex Planning (100 tasks)**

Agent roles: Goal Decomposer, Task Planner, Validator, Executor

Example tasks:
- Easy: "Plan a 3-day trip to Tokyo for a family of 4"
- Medium: "Create a project plan for migrating a monolithic application to microservices, including risk assessment"
- Hard: "Design a disaster recovery plan for a multi-region cloud deployment serving 10M users"
- Adversarial: "Plan a project with a budget of $100,000 and requirements that would cost $500,000" (impossible constraints — designed to test how agents handle infeasibility)

What you are measuring: Can agents decompose goals correctly? Do they detect impossible constraints? Do planners and validators reach agreement?

**Category E: Data Analysis Pipeline (100 tasks)**

Agent roles: Data Extractor, Cleaner, Analyst, Report Writer

Example tasks:
- Easy: "Analyze this CSV of sales data and identify the top 3 products by revenue"
- Medium: "Given quarterly financial data for 5 companies, identify trends and anomalies"
- Hard: "Combine data from three sources (CSV, JSON, and unstructured text) to produce a comprehensive market analysis"
- Adversarial: "Analyze this dataset" (with deliberately messy data containing contradictory rows — designed to test silent failure when agents do not flag data quality issues)

What you are measuring: Do agents flag data quality issues? Does the final report accurately reflect the source data? Do silent failures occur where wrong analysis looks correct?

### 3.5 The Detection Engine

This is the `agentfaildb` Python library that developers can use to detect failure patterns in their own multi-agent systems.

```python
# agentfaildb/detector.py
from agentfaildb.trace import TaskTrace, FailureAnnotation, FailureCategory, FailureSeverity
from agentfaildb.patterns import (
    detect_cascading_hallucination,
    detect_delegation_loop,
    detect_context_degradation,
    detect_conflicting_outputs,
    detect_role_violation,
    detect_resource_exhaustion,
)
import redis
import hashlib
import json

class FailureDetector:
    """Main detection engine. Runs all pattern detectors on a trace."""
    
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_client = redis.from_url(redis_url)
        self.detectors = [
            detect_cascading_hallucination,
            detect_delegation_loop,
            detect_context_degradation,
            detect_conflicting_outputs,
            detect_role_violation,
            detect_resource_exhaustion,
        ]
    
    def analyze(self, trace: TaskTrace) -> list[FailureAnnotation]:
        """Run all detectors on a trace, return found failures."""
        annotations = []
        for detector in self.detectors:
            result = detector(trace)
            if result:
                annotations.append(result)
                # Cache the pattern signature for fast lookup
                self._cache_pattern(trace, result)
        return annotations
    
    def _cache_pattern(self, trace: TaskTrace, annotation: FailureAnnotation):
        """Cache pattern signature in Redis for real-time detection."""
        # Create a signature from the failure pattern
        sig = self._compute_signature(trace, annotation)
        self.redis_client.setex(
            f"pattern:{sig}",
            3600,  # 1 hour TTL
            json.dumps({
                "category": annotation.category.value,
                "severity": annotation.severity.value,
                "framework": trace.framework,
            })
        )
    
    def _compute_signature(self, trace, annotation) -> str:
        """Generate a hash signature for a failure pattern."""
        pattern_data = f"{annotation.category}:{trace.framework}:{trace.task_category}"
        if annotation.failure_point_index is not None:
            # Include the message pattern around the failure point
            start = max(0, annotation.failure_point_index - 1)
            end = min(len(trace.messages), annotation.failure_point_index + 2)
            msg_types = [m.message_type for m in trace.messages[start:end]]
            pattern_data += ":" + ",".join(msg_types)
        return hashlib.sha256(pattern_data.encode()).hexdigest()[:16]
```

**Example pattern detector (delegation loops):**

```python
# agentfaildb/patterns/delegation_loop.py
from agentfaildb.trace import TaskTrace, FailureAnnotation, FailureCategory, FailureSeverity
from collections import Counter

def detect_delegation_loop(trace: TaskTrace) -> FailureAnnotation | None:
    """Detect infinite delegation loops between agents."""
    
    # Strategy 1: Check for repeated agent pairs
    pair_counts = Counter()
    for i in range(len(trace.messages) - 1):
        pair = (trace.messages[i].source_agent, trace.messages[i].target_agent)
        reverse = (trace.messages[i].target_agent, trace.messages[i].source_agent)
        if pair in pair_counts or reverse in pair_counts:
            pair_counts[pair] += 1
        else:
            pair_counts[pair] = 1
    
    max_pair = pair_counts.most_common(1)
    if max_pair and max_pair[0][1] > 5:  # More than 5 back-and-forth
        loop_count = max_pair[0][1]
        severity = (
            FailureSeverity.CRITICAL if loop_count > 15
            else FailureSeverity.MAJOR if loop_count > 8
            else FailureSeverity.MINOR
        )
        return FailureAnnotation(
            category=FailureCategory.DELEGATION_LOOP,
            severity=severity,
            root_cause_agent=max_pair[0][0][0],
            failure_point_index=_find_loop_start(trace.messages, max_pair[0][0]),
            description=f"Delegation loop detected: {max_pair[0][0]} exchanged {loop_count} times",
            confidence=0.9 if loop_count > 10 else 0.7,
        )
    
    # Strategy 2: Check for semantic repetition
    # (Uses embedding similarity between consecutive messages from same agent)
    # ... implementation using sentence-transformers
    
    return None
```

### 3.6 LLM-Assisted Annotation

Manual annotation of 500+ traces is not feasible in your timeline. You will use a hybrid approach:

1. **Rule-based detection** (the pattern detectors above) catches obvious failures (loops, resource exhaustion, explicit contradictions)
2. **LLM-assisted annotation** (using Claude or GPT-4) catches subtler failures (hallucination cascading, context degradation, silent failures)
3. **Human validation** on a sample (50-100 traces) to compute inter-annotator agreement and validate LLM annotation quality

```python
# agentfaildb/annotator.py

ANNOTATION_PROMPT = """You are an expert evaluator of multi-agent AI systems. 
You will be given a trace of messages between AI agents working on a task.

Task description: {task_description}
Expected output (if available): {expected_output}
Actual output: {actual_output}

Message trace:
{formatted_messages}

Analyze this trace for the following failure categories:
1. Cascading Hallucination: Does any agent fabricate information that later agents treat as fact?
2. Delegation Loop: Do agents pass tasks back and forth without progress?
3. Context Degradation: Does information lose fidelity as it passes between agents?
4. Conflicting Outputs: Do agents produce contradictory results?
5. Role Violation: Does any agent operate outside its assigned role?
6. Silent Failure: Is the output wrong despite no visible error signals?
7. Resource Exhaustion: Is the token/time usage excessive for task complexity?

For each failure found, respond in JSON:
{{"failures": [
  {{"category": "...", "severity": "minor|major|critical", 
    "root_cause_agent": "...", "failure_point_message_index": N,
    "description": "...", "confidence": 0.0-1.0}}
]}}

If no failures detected, respond: {{"failures": []}}
"""
```

**Important methodological note for your paper:** You must report the inter-annotator agreement between your LLM annotator and your human annotations. Use Cohen's Kappa. If Kappa > 0.7, your LLM annotations are credible. If lower, you need to adjust your prompt or annotation strategy. This metric goes directly into your paper's methodology section.

### 3.7 Database Schema (PostgreSQL)

```sql
CREATE TABLE traces (
    trace_id UUID PRIMARY KEY,
    framework VARCHAR(50) NOT NULL,
    task_category VARCHAR(50) NOT NULL,
    task_id VARCHAR(100) NOT NULL,
    task_description TEXT NOT NULL,
    expected_output TEXT,
    actual_output TEXT NOT NULL,
    total_tokens INTEGER NOT NULL,
    total_time_seconds FLOAT NOT NULL,
    num_agents INTEGER NOT NULL,
    agent_roles JSONB NOT NULL,
    task_success BOOLEAN NOT NULL,
    run_timestamp TIMESTAMP NOT NULL,
    run_config JSONB DEFAULT '{}'
);

CREATE TABLE messages (
    message_id UUID PRIMARY KEY,
    trace_id UUID REFERENCES traces(trace_id),
    message_index INTEGER NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    source_agent VARCHAR(100) NOT NULL,
    target_agent VARCHAR(100) NOT NULL,
    content TEXT NOT NULL,
    message_type VARCHAR(50) NOT NULL,
    token_count INTEGER NOT NULL,
    model_used VARCHAR(100),
    tool_calls JSONB,
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE annotations (
    annotation_id UUID PRIMARY KEY,
    trace_id UUID REFERENCES traces(trace_id),
    category VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    root_cause_agent VARCHAR(100),
    failure_point_index INTEGER,
    description TEXT NOT NULL,
    confidence FLOAT NOT NULL,
    annotation_source VARCHAR(20) NOT NULL,  -- 'rule', 'llm', 'human'
    annotator_id VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_traces_framework ON traces(framework);
CREATE INDEX idx_traces_category ON traces(task_category);
CREATE INDEX idx_annotations_category ON annotations(category);
CREATE INDEX idx_messages_trace ON messages(trace_id);
```

---

## Part 4: The Experiments (What You Measure)

### Experiment 1: Failure Rate by Framework

**Question:** Which framework has the highest/lowest overall failure rate?

**Method:** Run all 500 tasks across all 4 frameworks. Compute failure rate = (tasks with at least one failure) / (total tasks). Break down by failure category.

**Expected output:** A table like:

| Framework | Total Runs | Failure Rate | Most Common Failure |
|-----------|-----------|-------------|-------------------|
| CrewAI | 500 | X% | Category Y |
| AutoGen | 500 | X% | Category Y |
| LangGraph | 500 | X% | Category Y |
| MetaGPT | 500 | X% | Category Y |

**Why this matters:** This is your headline finding. "Framework X fails Y% of the time" is the stat that gets shared on LinkedIn and cited in papers.

### Experiment 2: Failure Rate by Task Complexity

**Question:** Does failure rate increase with task complexity? Is the increase linear or exponential?

**Method:** For each task category, you designed easy/medium/hard/adversarial difficulty levels. Compare failure rates across difficulty levels.

**Expected output:** A chart showing failure rate vs. difficulty level, per framework. You expect a non-linear increase — some threshold of complexity where multi-agent systems start breaking down significantly.

### Experiment 3: Failure Cascading Depth

**Question:** When a failure occurs, how far does it propagate through the agent chain?

**Method:** For traces with cascading hallucination or context degradation, measure how many agents the failure passes through before (a) being caught or (b) reaching the final output.

**Expected output:** Distribution of cascading depth. "On average, hallucinations pass through X agents before detection, and Y% reach the final output undetected."

### Experiment 4: Detection Accuracy

**Question:** How accurately does your automated detection framework identify failures?

**Method:** Run your pattern detectors on all traces. Compare to human annotations on a validation set of 100 traces. Compute precision, recall, and F1 for each failure category.

**Expected output:** A classification report showing which failure types are easily detected and which require more sophisticated approaches.

### Experiment 5: Cost of Failure

**Question:** How much more do failed runs cost (in tokens and time) compared to successful runs?

**Method:** Compare token usage and wall-clock time between successful and failed runs. Break down by failure type.

**Expected output:** "Delegation loops cost on average X tokens more than successful runs. Silent failures cost Y tokens but are undetectable without external validation."

---

## Part 5: The Paper (How to Write It)

### Paper Structure

**Title:** AgentFailDB: A Taxonomy and Benchmark of Failure Modes in Multi-Agent LLM Systems

**Length target:** 8-10 pages (standard ArXiv length for a systems/benchmark paper)

**Section 1: Introduction (1 page)**
- Opening: Multi-agent LLM systems are being deployed rapidly but reliability is understudied
- Problem: No systematic taxonomy of how these systems fail, no benchmark for comparison
- Contribution: (1) A taxonomy of 7 failure categories, (2) A benchmark dataset of 500+ annotated failure traces across 4 frameworks, (3) An open-source detection framework
- Results preview: "[X]% of multi-agent runs exhibit at least one failure mode. [Category Y] is the most prevalent, occurring in [Z]% of runs."

**Section 2: Related Work (1 page)**
- Existing agent benchmarks (GAIA, AgentBench, SWE-bench) — focused on task success, not failure analysis
- LLM evaluation (MMLU, HumanEval, MT-Bench) — single model, not multi-agent
- Multi-agent systems research — theory exists but empirical failure analysis does not
- Hallucination detection — exists for single LLM, not studied in multi-agent cascading context
- **Your positioning:** AgentFailDB is the first benchmark specifically targeting inter-agent failure modes

**Section 3: Failure Taxonomy (2 pages)**
- Present all 7 categories with formal definitions
- Include example traces for each (shortened for space)
- Discuss the severity scale
- This section is the most reusable part of your paper — other researchers will cite it for the definitions

**Section 4: Methodology (1.5 pages)**
- Framework selection criteria (why these 4)
- Task design (5 categories, difficulty levels, adversarial cases)
- Trace collection pipeline
- Annotation methodology (rule-based + LLM-assisted + human validation)
- Inter-annotator agreement results

**Section 5: Experimental Results (2 pages)**
- Experiment 1-5 results with tables and figures
- Statistical significance testing
- Key findings highlighted

**Section 6: Detection Framework (1 page)**
- Architecture of the agentfaildb library
- Per-category detection accuracy
- Runtime performance

**Section 7: Discussion + Limitations (0.5 page)**
- Limitations: only 4 frameworks, GPT-4 only (model-dependence), English only, LLM-assisted annotation has its own biases
- Future work: more frameworks, different LLMs, multilingual tasks, real-world production traces

**Section 8: Conclusion (0.5 page)**
- Summary of contributions
- Impact statement

### Writing tips for ArXiv credibility

- Use passive voice for methodology: "Tasks were designed to..." not "I designed tasks to..."
- Every claim must have a number or citation behind it
- Include error bars or confidence intervals on all metrics
- Acknowledge limitations proactively — reviewers respect honesty
- The abstract should contain your most dramatic finding
- Use LaTeX. The standard `article` class or `neurips_2024` style is fine for ArXiv

---

## Part 6: HuggingFace Strategy

### Dataset Publication

Your HuggingFace dataset should be immediately usable by researchers. This means:

**Dataset Card (README.md on HF):**
- Description: what it is, why it exists
- Dataset structure: columns, data types, annotation schema
- Intended uses: benchmarking agent reliability, training failure detectors
- Limitations: which frameworks, which LLM, English only
- Citation: BibTeX for your ArXiv paper

**Format:** Parquet files loadable via `datasets` library:
```python
from datasets import load_dataset
ds = load_dataset("yourusername/agentfaildb")
```

**Splits:**
- `train`: 400 traces (for training failure detectors)
- `test`: 100 traces (human-validated subset for evaluation)

### Leaderboard Space

A Streamlit app on HuggingFace Spaces that shows:
- Framework comparison table (failure rates)
- Filterable by task category and failure type
- Interactive charts (failure distribution, severity breakdown)
- "Submit your framework" section explaining how others can add their framework to the benchmark

---

## Part 7: LinkedIn Strategy (Non-Technical)

### The Narrative Arc

You are not just posting updates. You are telling a story across 4 weeks. The arc is:

**Week 1:** "I'm investigating something nobody is talking about" (curiosity + authority)
**Week 2:** "Here's what I found — it's worse than expected" (discovery + data)
**Week 3:** "I've built tools for the community to use" (generosity + utility)
**Week 4:** "I've published the research" (credibility + accomplishment)

### Post Templates

**Post 1 (Day 2-3): The Setup**
```
I'm spending the next month systematically breaking every major 
AI agent framework.

CrewAI. AutoGen. LangGraph. MetaGPT.

Everyone is building multi-agent AI systems. Nobody is 
asking: how do they actually fail?

Not "does the task succeed" — but HOW does it fail? 
What patterns emerge? Which frameworks are most reliable?

I've built a test harness that captures every single 
inter-agent message, every tool call, every token spent.

Running 500+ tasks across all 4 frameworks.

Will share findings as I go. First results dropping this week.

#AI #LLM #MultiAgentSystems #AIEngineering
```

**Post 2 (Day 8-10): The First Finding**
```
First results from stress-testing 4 AI agent frameworks 
across 200+ tasks:

[X]% of multi-agent runs contain at least one failure.

The most common failure? [Category]. It happens when [brief explanation].

Here's what surprised me: [Surprising finding].

[Include a screenshot of your data table or chart]

Full taxonomy of 7 failure categories coming next week, 
along with the open-source benchmark dataset.

Building this in public. Follow along.

#AI #MultiAgentSystems #AIReliability #OpenSource
```

**Post 3 (Day 16-18): The Tools**
```
Just shipped AgentFailDB to HuggingFace and GitHub.

What it is: The first open benchmark for measuring how 
multi-agent AI systems fail.

What's included:
→ 500+ annotated failure traces across 4 frameworks
→ Taxonomy of 7 failure categories with formal definitions
→ Interactive leaderboard comparing framework reliability
→ Python library for detecting failures in your own agent systems

[Screenshot of leaderboard]

Links in comments.

If you're deploying agents in production, you need to know 
how they break before your users find out.

#OpenSource #AI #HuggingFace #AIEngineering
```

**Post 4 (Day 25-28): The Paper**
```
Just submitted my first ArXiv paper.

"AgentFailDB: A Taxonomy and Benchmark of Failure Modes 
in Multi-Agent LLM Systems"

What started as curiosity about how AI agents break became 
a 4-week research sprint testing 4 frameworks across 500+ tasks.

Key findings:
→ [Most dramatic stat]
→ [Second most dramatic stat]  
→ [Third finding]

The full dataset, leaderboard, and detection framework are 
all open-source.

Paper: [ArXiv link]
GitHub: [link]
HuggingFace: [link]

If you've ever had an AI agent fail in production, 
this is for you.

#ArXiv #AIResearch #OpenSource #MultiAgentSystems
```

### Engagement Tactics

- **Tag framework creators** (CrewAI's @joaomdmoura, LangChain/LangGraph team, Microsoft AutoGen team). They will likely engage — positive or negative, both give you visibility.
- **Reply to every comment** in the first 2 hours of posting. LinkedIn's algorithm favors posts with early engagement.
- **Post between 8-10 AM EST** on Tuesday/Wednesday/Thursday. These are peak LinkedIn times for tech audiences.
- **Cross-post key findings as Twitter/X threads** with the same data but more technical detail.
- **Share in relevant communities:** LangChain Discord, CrewAI Discord, r/MachineLearning, HackerNews.

---

## Part 8: Risk Mitigation

### Risk 1: API Costs

Running 500 tasks × 4 frameworks × GPT-4 = potentially thousands of dollars in API costs.

**Mitigation:**
- Use GPT-3.5 Turbo for initial testing and harness development (Week 1, Days 1-3)
- Only switch to GPT-4 Turbo for final benchmark runs
- Set hard token limits per task (e.g., 10,000 tokens max)
- Set hard timeout per task (e.g., 120 seconds)
- Cache task results so you never re-run the same task unnecessarily
- Estimate: ~$150-300 total if you manage limits carefully

### Risk 2: Framework API Changes

These frameworks update frequently. An API you coded against on Day 1 might change by Day 15.

**Mitigation:**
- Pin exact versions in pyproject.toml
- Note framework versions in your paper
- Build your runners against a specific release, not `main`

### Risk 3: Insufficient Failures

What if the frameworks work better than expected and you do not find enough failures?

**Mitigation:**
- The adversarial tasks are designed specifically to trigger failures
- Even if failure rates are lower than expected, that itself is a finding
- Focus on the taxonomy and detection framework as contributions, not just the failure rates
- "Framework X is surprisingly robust to Y" is also a publishable finding

### Risk 4: Paper Rejection / Low Visibility

ArXiv is not peer-reviewed — it accepts most submissions. The real risk is low visibility.

**Mitigation:**
- Paper quality matters more on ArXiv than acceptance (there is no acceptance barrier)
- Cross-post on Papers With Code (this gets ML researcher eyeballs)
- The LinkedIn campaign drives visibility independently of the paper
- The GitHub repo and HF dataset have value even if the paper gets zero citations initially

### Risk 5: Time Overrun

150 hours across 4 weeks is tight. If any phase takes longer than planned, the whole timeline shifts.

**Mitigation:**
- The phases are ordered by importance: taxonomy → dataset → detection → paper
- If you run out of time, a taxonomy + dataset without a detection framework is still publishable
- A taxonomy + dataset + detection framework without a polished paper can be published later
- The LinkedIn content happens in parallel and does not block technical work
- Week 4 has buffer days (Apr 1-5) specifically for overruns

---

## Part 9: Day-by-Day Schedule

### Week 1
| Day | Date | Hours | Focus |
|-----|------|-------|-------|
| 1 | Mar 13 | 5h | Set up repo, Docker, PostgreSQL, Redis. Install all 4 frameworks. |
| 2 | Mar 14 | 5h | Build base_runner.py and crewai_runner.py. Test with 5 sample tasks. |
| 3 | Mar 15 | 5h | Build autogen_runner.py and langgraph_runner.py. LinkedIn Post #1. |
| 4 | Mar 16 | 5h | Build metagpt_runner.py. Build trace_collector (FastAPI middleware). |
| 5 | Mar 17 | 5h | Design 20 tasks per category (100 total). Run first batch across all frameworks. |
| 6 | Mar 18 | 5h | Run more tasks. Start reviewing traces manually. Draft taxonomy v1. |
| 7 | Mar 19 | 5h | Refine taxonomy. Document each category with 3+ example traces. |

### Week 2
| Day | Date | Hours | Focus |
|-----|------|-------|-------|
| 8 | Mar 20 | 5h | Expand to 100 tasks per category. Begin large-scale runs. |
| 9 | Mar 21 | 5h | Continue runs. Build delegation_loop.py detector. LinkedIn Post #2. |
| 10 | Mar 22 | 5h | Build cascading_hallucination.py and context_degradation.py detectors. |
| 11 | Mar 23 | 5h | Build remaining pattern detectors. Run detectors on all traces. |
| 12 | Mar 24 | 5h | Build LLM annotation pipeline. Run on all traces. |
| 13 | Mar 25 | 5h | Manually validate 100 traces. Compute inter-annotator agreement. |
| 14 | Mar 26 | 5h | Compute all experiment metrics. Build comparison tables. |

### Week 3
| Day | Date | Hours | Focus |
|-----|------|-------|-------|
| 15 | Mar 27 | 5h | Format dataset for HuggingFace. Write dataset card. Push to HF. |
| 16 | Mar 28 | 5h | Build Streamlit leaderboard. Deploy to HF Spaces. |
| 17 | Mar 29 | 5h | Polish GitHub repo: README, docs, examples, CONTRIBUTING.md. LinkedIn Post #3. |
| 18 | Mar 30 | 5h | Set up CI/CD. Write usage examples. Create pip-installable package. |
| 19 | Mar 31 | 5h | Final code cleanup. Start paper outline and LaTeX setup. |

### Week 4
| Day | Date | Hours | Focus |
|-----|------|-------|-------|
| 20 | Apr 1 | 5h | Write Introduction, Related Work, Taxonomy sections. |
| 21 | Apr 2 | 5h | Write Methodology, Experiments sections. Create figures/tables. |
| 22 | Apr 3 | 5h | Write Detection Framework, Discussion, Conclusion. Full draft review. |
| 23 | Apr 4 | 5h | Final paper polish. Submit to ArXiv. LinkedIn Post #4. |
| 24 | Apr 5 | 5h | Cross-post everywhere. Reddit, HN, Discord, Twitter. Buffer day. |

---

## Part 10: Success Metrics

After 1 month, measure yourself against these:

**GitHub:** Target 100+ stars in first 2 weeks after launch. Realistic if your LinkedIn posts gain traction and the README is excellent.

**HuggingFace:** Target 500+ dataset downloads in first month. Benchmark datasets in active areas tend to get adopted quickly.

**ArXiv:** Submission is the goal. Citations take months — do not measure success by citations in Month 1. Instead, measure by Papers With Code ranking and social media shares.

**LinkedIn:** Target 1 post reaching 10K+ impressions. This requires your most dramatic finding combined with good timing and engagement.

**Recruiter attention:** Within 2-4 weeks of launch, you should start receiving inbound messages if your LinkedIn posts gain traction. The combination of "published ArXiv paper" + "open-source project with stars" + "systematic approach to AI reliability" positions you as exactly the type of engineer AI companies are hiring right now.
