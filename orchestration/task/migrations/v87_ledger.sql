-- V8.7 persistent task ledger. State/history only. Not an execution queue.
-- Applied via CREATE IF NOT EXISTS by PostgresManager / ledger_store.ensure_schema.

CREATE TABLE IF NOT EXISTS doom_v8_tasks (
    task_id VARCHAR(128) PRIMARY KEY,
    goal_id VARCHAR(128) NOT NULL,
    owner_id VARCHAR(128) NOT NULL,
    session_id VARCHAR(128) NOT NULL DEFAULT '',
    computer_session_id VARCHAR(128) NOT NULL DEFAULT '',
    plan_hash VARCHAR(256) NOT NULL,
    recovery_plan_hash VARCHAR(256) NOT NULL DEFAULT '',
    state VARCHAR(24) NOT NULL,
    created_unix_ms BIGINT NOT NULL,
    started_unix_ms BIGINT NOT NULL DEFAULT 0,
    completed_unix_ms BIGINT NOT NULL DEFAULT 0,
    attempts_used INTEGER NOT NULL DEFAULT 0,
    transition_count INTEGER NOT NULL DEFAULT 0,
    failure_reason VARCHAR(128) NOT NULL DEFAULT '',
    execution_started BOOLEAN NOT NULL DEFAULT FALSE,
    pending_recovery BOOLEAN NOT NULL DEFAULT FALSE,
    version INTEGER NOT NULL DEFAULT 1,
    updated_unix_ms BIGINT NOT NULL,
    CHECK (transition_count >= 0 AND transition_count <= 64),
    CHECK (version >= 1)
);

CREATE INDEX IF NOT EXISTS idx_v8_tasks_owner ON doom_v8_tasks (owner_id);
CREATE INDEX IF NOT EXISTS idx_v8_tasks_goal ON doom_v8_tasks (goal_id);
CREATE INDEX IF NOT EXISTS idx_v8_tasks_state ON doom_v8_tasks (state);
CREATE INDEX IF NOT EXISTS idx_v8_tasks_plan_hash ON doom_v8_tasks (plan_hash);
CREATE INDEX IF NOT EXISTS idx_v8_tasks_created ON doom_v8_tasks (created_unix_ms);

CREATE TABLE IF NOT EXISTS doom_v8_task_transitions (
    task_id VARCHAR(128) NOT NULL REFERENCES doom_v8_tasks(task_id) ON DELETE CASCADE,
    sequence_number INTEGER NOT NULL,
    from_state VARCHAR(24) NOT NULL,
    to_state VARCHAR(24) NOT NULL,
    timestamp_unix_ms BIGINT NOT NULL,
    reason_code VARCHAR(128) NOT NULL,
    plan_hash VARCHAR(256) NOT NULL DEFAULT '',
    version INTEGER NOT NULL,
    PRIMARY KEY (task_id, sequence_number),
    CHECK (sequence_number >= 1 AND sequence_number <= 64)
);
