-- Types

CREATE TYPE oban_job_state AS ENUM (
    'available',
    'scheduled',
    'suspended',
    'executing',
    'retryable',
    'completed',
    'discarded',
    'cancelled'
);

-- Tables

CREATE TABLE oban_jobs (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    state oban_job_state NOT NULL DEFAULT 'available',
    queue TEXT NOT NULL DEFAULT 'default',
    worker TEXT NOT NULL,
    attempt SMALLINT NOT NULL DEFAULT 0,
    max_attempts SMALLINT NOT NULL DEFAULT 20,
    priority SMALLINT NOT NULL DEFAULT 0,
    args JSONB NOT NULL DEFAULT '{}',
    meta JSONB NOT NULL DEFAULT '{}',
    tags JSONB NOT NULL DEFAULT '[]',
    errors JSONB NOT NULL DEFAULT '[]',
    attempted_by TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    inserted_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT timezone('UTC', now()),
    scheduled_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT timezone('UTC', now()),
    attempted_at TIMESTAMP WITHOUT TIME ZONE,
    cancelled_at TIMESTAMP WITHOUT TIME ZONE,
    completed_at TIMESTAMP WITHOUT TIME ZONE,
    discarded_at TIMESTAMP WITHOUT TIME ZONE,
    CONSTRAINT attempt_range CHECK (attempt >= 0 AND attempt <= max_attempts),
    CONSTRAINT queue_length CHECK (char_length(queue) > 0),
    CONSTRAINT worker_length CHECK (char_length(worker) > 0),
    CONSTRAINT positive_max_attempts CHECK (max_attempts > 0),
    CONSTRAINT non_negative_priority CHECK (priority >= 0)
);

CREATE TABLE oban_leaders (
    name TEXT PRIMARY KEY,
    node TEXT NOT NULL,
    started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
);

-- Indexes

CREATE INDEX oban_jobs_meta_index ON oban_jobs USING gin (meta);

CREATE INDEX oban_jobs_state_queue_priority_scheduled_at_id_index
ON oban_jobs (state, queue, priority, scheduled_at, id);

CREATE INDEX oban_jobs_staging_index
ON oban_jobs (scheduled_at, id)
WHERE state IN ('scheduled', 'retryable');

-- Autovacuum

ALTER TABLE oban_jobs SET (
  -- Vacuum early, at 5% dead or 1000 dead rows
  autovacuum_vacuum_scale_factor = 0.05,
  autovacuum_vacuum_threshold = 1000,

  -- Analyze even earlier to keep the planner up to date
  autovacuum_analyze_scale_factor = 0.025,
  autovacuum_analyze_threshold = 500,

  -- Run vacuum more aggressively with minimal sleep and 10x the default IO
  autovacuum_vacuum_cost_limit = 2000,
  autovacuum_vacuum_cost_delay = 10,

  -- Reserve 10% free space per page for HOT updates
  fillfactor = 90
);
