-- Create custom types
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

-- Create main jobs table
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

-- Create indexes
CREATE INDEX oban_jobs_meta_index ON oban_jobs USING gin (meta);

CREATE INDEX oban_jobs_state_queue_priority_scheduled_at_id_index
ON oban_jobs (state, queue, priority, scheduled_at, id);

CREATE INDEX oban_jobs_staging_index
ON oban_jobs (scheduled_at, id)
WHERE state IN ('scheduled', 'retryable');

-- Create peers table for distributed coordination
CREATE TABLE oban_peers (
    name TEXT PRIMARY KEY,
    node TEXT NOT NULL,
    started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
