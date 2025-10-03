import os
import psycopg

ADMIN_URL = os.getenv("PG_ADMIN_URL", "postgresql://postgres@localhost/postgres")
TEMPLATE_DB = os.getenv("OBAN_TEMPLATE_DB", "oban_test_template")

with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
    conn.execute(f'ALTER DATABASE "{TEMPLATE_DB}" IS_TEMPLATE false')
    conn.execute(f'DROP DATABASE IF EXISTS "{TEMPLATE_DB}" WITH (FORCE)')
    conn.execute(f'CREATE DATABASE "{TEMPLATE_DB}"')

# Run migrations directly for now. We'll move them into a dedicated module later, but this allows
# us to run without sqlalchemy/alembic for now.
template_url = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEMPLATE_DB}"

with psycopg.connect(template_url) as conn:
    conn.execute("""
        CREATE TYPE oban_job_state AS ENUM (
            'available',
            'scheduled',
            'suspended',
            'executing',
            'retryable',
            'completed',
            'discarded',
            'cancelled'
        )
    """)

    conn.execute("""
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
        )
    """)

    conn.execute("CREATE INDEX oban_jobs_meta_index ON oban_jobs USING gin (meta)")

    conn.execute("""
        CREATE INDEX oban_jobs_state_queue_priority_scheduled_at_id_index
        ON oban_jobs (state, queue, priority, scheduled_at, id)
    """)

    conn.execute("""
        CREATE TABLE oban_peers (
            name TEXT PRIMARY KEY,
            node TEXT NOT NULL,
            started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
    """)

    conn.commit()

with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
    conn.execute(f'ALTER DATABASE "{TEMPLATE_DB}" IS_TEMPLATE true')
    conn.execute(f'ALTER DATABASE "{TEMPLATE_DB}" ALLOW_CONNECTIONS false')
