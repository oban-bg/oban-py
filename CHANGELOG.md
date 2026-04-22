# Changelog

Oban is a robust job orchestration framework for Python, backed by PostgreSQL. This is the
second public release, bringing battle-tested patterns from Oban for Elixir to the Python
ecosystem with an async-native, Pythonic API.

## Metrics Broadcasting for Oban Web

This release adds metrics broadcasting, enabling real-time visibility in [Oban Web][web]. When
enabled, each node publishes queue state, job execution statistics, and cron schedules on the
gossip channel.

Metrics include execution timing distributions, job counts by state and queue, running job
details, and cron schedules. Large tables automatically switch to query planner estimates for
performance.

Enable metrics in your configuration:

```toml
[oban]
queues = {default = 10}
metrics = true
```

[web]: https://hexdocs.pm/oban_web/standalone.html

## v0.6.2 — 2026-04-22

### Enhancements

- [Looper] Introduce a protocol for lifecycle components

  Add a shared Looper protocol describing the start/stop/_loop lifecycle used by background
  components, and apply it to Stager, Pruner, Scheduler, Refresher, Lifeline, Leader, Metrics, and
  Producer. This documents the contract explicitly and enables uniform handling of lifecycle
  loopers.

- [Stager] Include queue in the staging index

  The staging index is now referenced by stage_jobs to filter jobs down to the set of running
  queues. Adding queue as an INCLUDE column lets that filter apply during the index scan while
  keeping the (scheduled_at, id) ordering that the LIMIT relies on.

- [Stager] Restrict stage_jobs to active queues

  Previously the `stage_jobs` query transitioned any scheduled or retryable job whose time had
  come, regardless of whether its queue was running on the node. That let idle queues churn jobs
  into the available state with nothing around to execute them. Stage only jobs belonging to the
  running queues passed in by the stager.

### Bug Fixes

- [Scheduler] Guard scheduler against backward wall-clock steps

  Track the last evaluated wall-clock minute on the scheduler and skip re-entry when the clock
  steps backward (NTP adjustment, VM resume) into a minute that was already handled. Prevents
  duplicate cron inserts without introducing catch-up semantics, so healthy clocks are unaffected.

## v0.6.1 — 2026-03-17

### Enhancements

- [Pruner] Add extension point for pruner

  Apply the `put_ext` extension pattern to the pruner module, allowing customized pruning
  behavior.

### Bug Fixes

- [Worker] Fix static analysis errors on decorated workers

  Static analysis tools (pyright, mypy, pylint) previously reported false-positive errors because
  the `enqueue` and `new` methods were dynamically added at runtime with no type hints.

  This introduces a `Worker` Protocol that decorated classes implement, enabling editors and type
  checkers to provide accurate autocomplete and eliminate "unknown attribute" warnings.


## v0.6.0 — 2026-02-23

### Breaking Changes

- [Oban] Change default instance name from "oban" to "Oban"

  The default instance name is changed for simpler compatibility with elixir workers and metric
  aggregation for the web dashboard.

- [Schema] Change tags field from `jsonb` to `text[]` for compatibility

  This enables inserting jobs from oban-py into tables managed by Oban for Elixir, which uses
  text[] for tags rather than jsonb.

  Existing `oban-py` created schemas require the following migration:

  ```sql
  CREATE OR REPLACE FUNCTION jsonb_to_text_array(jsonb) RETURNS text[] AS $$
      SELECT COALESCE(array_agg(x), '{}') FROM jsonb_array_elements_text($1) AS x;
  $$ LANGUAGE sql IMMUTABLE;

  UPDATE oban_jobs SET tags = '[]'::jsonb WHERE tags IS NULL;
  ALTER TABLE oban_jobs ALTER COLUMN tags DROP DEFAULT;

  ALTER TABLE oban_jobs
      ALTER COLUMN tags TYPE text[]
      USING jsonb_to_text_array(tags);

  ALTER TABLE oban_jobs ALTER COLUMN tags SET DEFAULT '{}';

  DROP FUNCTION jsonb_to_text_array(jsonb);
  ```

### Enhancements

- [Config] Configure connection pool lifecycle for resilience

  Adds `pool_max_lifetime`, `pool_max_idle` options with sensible defaults to automatically
  recycle stale database connections. This helps workers recover after database restarts.

- [Metrics] Include the queue producer's `uuid` in checks and metrics

  The uuid is used to determine which jobs are orphans in the web dashboard. Without the correct
  uuid information, all python jobs look like orphans.

- [Metrics] Add `counts_enabled` flag to disable metric counts

  Counting should only be performed by one node for the same database/schema. With the new
  `counts_enabled` option, it's possible to disable counting on the python side while still
  broadcasting other metrics.

- [Metrics] Add telemetry-based job metrics for Oban Web

  Extend metrics broadcasting to include job execution data collected from telemetry events. Each
  node now reports `exec_time`, `wait_time`, and `exec_count` metrics on the metrics channel,
  enabling Oban Web to display execution statistics and timing distributions.

  Metrics use DDSketch for timing data (exec_time, wait_time) to support quantile queries, and
  simple gauges for counts (exec_count).

- [Metrics] Add metrics broadcasting for Oban Web integration

  Enable real-time queue visibility in Oban Web by broadcasting producer state on the gossip
  channel. When enabled, each node periodically publishes its local queue information including
  limits, paused status, and running jobs. Disabled by default to avoid pub/sub overhead.

- [Metrics] Broadcast cron entries for schedule display

  Scheduled job entries are broadcast using the "cronitor" format for inclusin in Oban Web's cron
  page.

- [Metrics] Add full_count metrics with estimate optimization

  Broadcast job counts by state and queue on the metrics channel for Oban Web integration. When
  counts for any state exceed 50,000, automatically switch to query planner estimates for better
  performance.

- [Job] Include full traceback in recorded job errors

  Errors persisted to the database now include the complete Python traceback, matching Elixir
  Oban's behavior. This makes it easier to diagnose failures when you only have database access
  (e.g. via Oban Web or direct SQL queries).

- [Cron] Log exceptions during failed cron scheduling

  An exception is logged if a scheduld job fails to enqueue for any reason.

- [Schema] Inject `suspended` value into existing `oban_job_state` enum

  Maximize compatibility with elixir based oban schemas by inserting the new `suspended` state
  after the `scheduled` state.

### Bug Fixes

- [CLI] Optimize cron cron discovery by splitting into two passes

  Split the single grep into two passes:

  1. find files with worker or job decorators
  2. filter to those also containing cron=

  This handles multi-line decorators correctly with no meaningful performance cost. False
  positives (files with @worker and an unrelated cron=) are harmless because the module gets
  imported but no cron schedule is registered.

- [Oban] Fix connection unwrapping for transactional inserts

  Passing SQLAlchemy `AsyncSession` or `AsyncConnection` to the conn parameter of `enqueue()`
  failed with greenlet errors. The `unwrap_connection` function now correctly extracts the
  underlying psycopg connection from SQLAlchemy's async adapters.

- [Oban] Safely handle signals without an identifier

  Changed handling so signals (pause, resume, scale, etc.) without an explicit ident are treated
  as broadcasts to all nodes.

- [Cron] Use deterministic hash for cron entry names

  Replace non-deterministic `hash()` with sha-256 to generate stable, shorter cron names that
  include expression, worker, and options. Include the name when broadcasting cron entries via
  metrics.

- [Notifier] Fix notifier reconnection after connection loss

  This improves resilience when databases restart for maintenance or scaling, ensuring the
  notifier recovers and workers continue receiving insert notifications.

- [Oban] Use `logger.exception` consistently throughout all core classes

  Update looping classes and the cli to use `logger.exception` instead of manual `logger.error`
  reporting.
