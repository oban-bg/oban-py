# Changelog

Oban is a robust job orchestration framework for Python, backed by PostgreSQL. This is the
initial public release, bringing battle-tested patterns from Oban for Elixir to the Python
ecosystem with an async-native, Pythonic API.

## v0.5.0 — 2025-01-19

### Features

- [Oban] Core job processing with asyncio, supporting enqueueing, execution, retries, and
  graceful shutdown.

- [Oban] Multiple isolated queues with independent concurrency limits. Each queue processes
  jobs separately, preventing slow jobs in one queue from blocking others.

- [Oban] Runtime queue control with `pause_queue`, `resume_queue`, `start_queue`, `stop_queue`,
  and `scale_queue`. Control can target specific nodes or all nodes in the cluster.

- [Oban] Job scheduling with `scheduled_at` for absolute times or `schedule_in` for relative
  delays. Jobs are staged automatically when their scheduled time arrives.

- [Oban] Job priority from 0-9, where lower values execute first within the same queue.

- [Oban] Job management with `cancel_job`, `retry_job`, `delete_job`, and `update_job` for
  modifying jobs after insertion. All operations support single jobs or batches.

- [Oban] Graceful cancellation support. Workers can check `job.cancelled()` at safe points
  and return `Cancel("reason")` to stop cleanly.

- [Worker] The `@worker` decorator for class-based workers with a `process` method. Supports
  default options like `queue`, `priority`, `max_attempts`, and custom `backoff` methods.

- [Worker] The `@job` decorator for function-based workers. The decorated function's signature
  is preserved, allowing natural `enqueue(arg1, arg2)` calls.

- [Worker] Job creation via `Worker.new()` and enqueueing via `Worker.enqueue()`. Both accept
  args and option overrides.

- [Scheduler] Periodic job scheduling with cron expressions via the `cron` parameter on
  `@worker` or `@job` decorators. Supports standard 5-field syntax and nicknames like
  `@daily`, `@hourly`, `@weekly`.

- [Scheduler] Timezone support for cron expressions with `cron={"expr": "...", "timezone": "..."}`.

- [Leader] Automatic leader election for coordinating cluster-wide operations like scheduling,
  pruning, and job rescue. Only the leader performs these operations to prevent duplicates.

- [Notifier] PostgreSQL LISTEN/NOTIFY for real-time job dispatch. Jobs are executed immediately
  when inserted rather than waiting for the next polling interval.

- [Stager] Background process that moves scheduled and retryable jobs to available state when
  their time arrives.

- [Pruner] Automatic cleanup of old completed, cancelled, and discarded jobs. Configurable
  with `max_age`, `interval`, and `limit` options.

- [Lifeline] Rescue mechanism for orphaned jobs left in executing state after crashes or
  ungraceful shutdowns.

- [Refresher] Periodic recording of queue metrics to the database for monitoring and analytics.

- [Telemetry] Event emission for job lifecycle events (`oban.job.execute.start`, `.stop`,
  `.exception`) with timing and metadata.

- [Telemetry] Handler attachment API with `attach()`, `detach()`, and `span()` for custom
  instrumentation.

- [Telemetry] Built-in logging handler that can be attached with `telemetry.logger.attach()`.

- [Testing] The `process_job()` helper for unit testing workers without database interaction.

- [Testing] The `drain_queue()` helper for synchronously executing all jobs in a queue during
  integration tests.

- [Testing] Assertion helpers `assert_enqueued()` and `refute_enqueued()` with partial args
  matching and optional timeout for async assertions.

- [Testing] The `all_enqueued()` helper for retrieving jobs matching filters.

- [Testing] The `reset_oban()` helper for cleaning up between tests.

- [Testing] The `mode("inline")` context manager for executing jobs immediately during tests.

- [CLI] The `oban install` command for installing the database schema.

- [CLI] The `oban uninstall` command for removing the database schema.

- [CLI] The `oban start` command for running workers with queue processing. Supports auto-discovery
  of cron workers, signal handling, and configuration via CLI flags, environment variables, or
  `oban.toml`.

- [Config] TOML configuration file support via `oban.toml` with database, pool, and queue settings.

- [Config] Environment variable configuration with `OBAN_DSN`, `OBAN_QUEUES`, `OBAN_PREFIX`, etc.

- [Job] The `Snooze(seconds)` return value for rescheduling a job to run again after a delay.

- [Job] The `Cancel(reason)` return value for marking a job as cancelled from within the worker.

- [Job] The `Record(value)` return value for storing results in the job's meta field.

- [Job] Context manager lifecycle with `async with Oban(...) as oban:` for automatic start/stop.

- [Job] Instance registry with `get_instance(name)` for accessing Oban instances by name.
