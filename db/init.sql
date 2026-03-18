CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TYPE failure_category AS ENUM (
    'cascading_hallucination', 'delegation_loop', 'context_degradation',
    'conflicting_outputs', 'role_violation', 'silent_failure',
    'resource_exhaustion', 'none'
);
CREATE TYPE failure_severity AS ENUM ('none', 'minor', 'major', 'critical');
CREATE TYPE ground_truth_type AS ENUM ('deterministic', 'claim_list', 'rubric');
CREATE TYPE message_type AS ENUM (
    'task_delegation', 'response', 'feedback', 'tool_call', 'tool_result',
    'system_control', 'subscription_routing', 'internal_reasoning', 'checkpoint'
);
CREATE TYPE annotation_source AS ENUM ('rule_based', 'llm_ollama', 'llm_claude', 'human');

CREATE TABLE traces (
    trace_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    framework            VARCHAR(50)  NOT NULL,
    task_category        VARCHAR(50)  NOT NULL,
    task_difficulty      VARCHAR(20)  NOT NULL,
    task_id              VARCHAR(100) NOT NULL,
    task_description     TEXT         NOT NULL,
    ground_truth_type    ground_truth_type NOT NULL,
    ground_truth         JSONB,
    actual_output        TEXT         NOT NULL,
    total_api_tokens     INTEGER      NOT NULL,
    total_content_tokens INTEGER      NOT NULL,
    context_overhead_ratio FLOAT      NOT NULL,
    total_time_seconds   FLOAT        NOT NULL,
    num_agents           INTEGER      NOT NULL,
    agent_roles          JSONB        NOT NULL,
    task_success         BOOLEAN,
    task_score           FLOAT,
    task_success_method  VARCHAR(50),
    model_used           VARCHAR(100) NOT NULL,
    run_timestamp        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    run_config           JSONB        DEFAULT '{}'
);

CREATE TABLE messages (
    message_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trace_id             UUID NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
    message_index        INTEGER      NOT NULL,
    timestamp            TIMESTAMPTZ  NOT NULL,
    source_agent         VARCHAR(100) NOT NULL,
    target_agent         VARCHAR(100) NOT NULL,
    content              TEXT         NOT NULL,
    msg_type             message_type NOT NULL,
    api_token_count      INTEGER,
    content_token_count  INTEGER,
    model_used           VARCHAR(100),
    tool_calls           JSONB,
    metadata             JSONB        DEFAULT '{}'
);

CREATE TABLE annotations (
    annotation_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trace_id             UUID NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
    category             failure_category NOT NULL,
    severity             failure_severity NOT NULL,
    root_cause_agent     VARCHAR(100),
    failure_point_index  INTEGER,
    description          TEXT         NOT NULL,
    confidence           FLOAT        NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    source               annotation_source NOT NULL,
    annotator_id         VARCHAR(100),
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_traces_framework          ON traces(framework);
CREATE INDEX idx_traces_category           ON traces(task_category);
CREATE INDEX idx_traces_difficulty         ON traces(task_difficulty);
CREATE INDEX idx_traces_model              ON traces(model_used);
CREATE INDEX idx_messages_trace            ON messages(trace_id, message_index);
CREATE INDEX idx_messages_type             ON messages(msg_type);
CREATE INDEX idx_annotations_trace         ON annotations(trace_id);
CREATE INDEX idx_annotations_category      ON annotations(category);
CREATE INDEX idx_annotations_source        ON annotations(source);
CREATE INDEX idx_traces_framework_category ON traces(framework, task_category);
