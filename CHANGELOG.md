# Changelog

This release focuses on controlling jobs with bounded shutdowns, per-job timeouts, and safer acks
when jobs are rescued and run again on another node.

## Job Timeouts

Jobs could previously run indefinitely, so a hung network call or runaway loop could hold a queue
slot forever. Workers can now limit how long a job runs by defining a `timeout` method that
returns the limit in seconds. It receives the job, so the limit can vary by args or attempt:

```python
@worker(queue="default")
class ExportWorker:
    async def process(self, job):
        await export_report(job.args["report_id"])

    def timeout(self, job):
        # Allow more time on each attempt: 60s, 120s, 180s...
        return 60 * job.attempt
```

Jobs that exceed their timeout fail with a `JobTimeoutError` and retry with the usual backoff.
Cancellation is cooperative, so a timed out job stops at its next `await`.

## Shutdown Grace Period

Stopping Oban or a queue previously waited indefinitely for running jobs, so a slow or hung job
could stall a deploy until the process was killed. Running jobs now have a shutdown grace period,
15 seconds by default. Jobs still running afterwards are cancelled and left for the lifeline to
rescue.

```toml
[oban]
queues = {default = 10}
shutdown_grace_period = 30
```

Each queue emits an `oban.queue.shutdown` telemetry event as it stops, including the ids of any
orphaned jobs, which the telemetry logger reports as a warning. Set `shutdown_grace_period` to
`None` to restore the previous behavior of waiting indefinitely.

## Current Job Context

Function workers defined with `@job` are concise, but they only receive the job's args, not the
job itself. The new `oban.current_job()` function returns the job executing in the current
context, so function workers can read details like the attempt, id, or meta without converting to
a class worker:

```python
from oban import current_job, job


@job(queue="billing", max_attempts=5)
async def charge_invoice(invoice_id: int):
    job = current_job()

    # Use the job id as an idempotency key so retries never double charge
    await payments.charge(invoice_id, idempotency_key=f"oban-{job.id}")

    if job.attempt > 1:
        logger.warning("Retried invoice %s, attempt %s", invoice_id, job.attempt)
```

Because it's backed by a context variable, `current_job()` also works in any helper called from
the job, without threading the job through as an argument. The job is set for the entire
execution, so it's available within `oban.job.start`, `oban.job.stop`, and `oban.job.exception`
telemetry handlers and result wrapping extensions as well. Outside of job execution it returns
`None`.

## v0.7.0 — 2026-10-22

### Enhancements

- [Config] Extend to provide the notifier from config

  A new `notifier` setting, from a `[notifier]` table in `oban.toml` or `OBAN_NOTIFIER`, is passed
  to the `notifier.build` extension hook along with the prefix and connection pool. Extensions can
  use it to replace the default PostgreSQL notifier without application code changes. Without an
  extension it's ignored, and the default notifier is used.

  Fixes dict settings from the environment or CLI being dropped when `oban.toml` didn't also set
  them.

- [Job] Rename `schedule_in` to `scheduled_in` for relative scheduling

  Using `scheduled_in` is now the correct way to schedule a job relatively, matching the
  `scheduled_at` field it sets. The `schedule_in` option is still accepted as an alias, and will be
  removed in 1.0.

  Testing helpers also accept `scheduled_in`, matching jobs scheduled that far from now within a
  second, so assertions don't need to compute an exact `scheduled_at`.

- [Notifier] Build notifier from a config dict

  Oban now accepts `notifier` as a dict, like the other component options, and builds the notifier
  itself with the instance's connection pool and prefix.

- [Oban] Build background components through extensions

  All looping services are constructed through `<name>.build` extensions, matching the existing
  `notifier.build`.

- [Oban] Expose `current_job` and retain through telemetry

  Add the `oban.current_job()` function to fetch the job executing in the current context. This is
  primarily for function workers that don't receive the job as an argument.

  The current job is also set for the entire execution, rather than only while the worker runs, so
  it's available within `oban.job.start`, `oban.job.stop`, and `oban.job.exception` telemetry
  handlers, as well as result wrapping extensions.

- [Producer] Cancel jobs running after shutdown grace period

  Stopping Oban or a queue previously waited indefinitely for running jobs, so a slow or hung job
  could stall shutdown until the process was killed.

  Running jobs now have a `shutdown_grace_period` to finish, 15 seconds by default. Jobs still
  running afterwards are cancelled at their next await and left for the lifeline to rescue. Set
  `shutdown_grace_period` to `None` to restore the previous behavior of waiting indefinitely.

  Each queue emits an `oban.queue.shutdown` telemetry event as it stops, including the ids of any
  orphaned jobs, which the telemetry logger reports as a warning.

- [Telemetry] Publish canonical telemetry event names

  Add `oban.telemetry.EVENTS`, listing every emitted event, so integrations can validate the names
  they attach to. Attaching to an event that's never emitted otherwise fails silently.

  The telemetry logger now correctly logs the actual `oban.producer.get.*` and
  `oban.producer.ack.*` events as well.

- [Testing] Include suspended jobs in testing assertions

  The `all_enqueued`, `assert_enqueued`, and `refute_enqueued` helpers now include the `suspended`
  state. This makes it possible to assert on jobs that are enqueued but waiting on something else,
  e.g. workflow deps.

- [Worker] Add job timeouts through a worker method

  Workers may define `timeout(self, job)` to limit how long a job runs, in seconds. Jobs exceeding
  their timeout are cancelled at the next await and fail with a `JobTimeoutError`, then retry with
  the usual backoff. Timeouts raised by the job itself are left untouched.

### Bug Fixes

- [Cron] Validate cron range bounds before expanding them

  Cron expressions with ranges far outside a field's bounds, such as `0-99999999 * * * *`, are now
  rejected immediately. Previously the full range was built in memory before validation, which
  could stall startup or exhaust memory.

- [Leader] Release leadership before notifying on shutdown

  Peers could react to the resign notification before the lease was released, lose the election,
  and wait a full interval before taking over.

- [Oban] Broadcast `start_queue`/`stop_queue` across all nodes

  Calling `start_queue` or `stop_queue` without a node only changed the local node, despite being
  documented to act on all nodes. Both now act locally and broadcast, matching `scale_queue`.

- [Producer] Prevent stale acks from overwriting newer executions

  Acking a job now also requires its `attempted_at` to match the execution being acked.
  Previously, a job that was rescued and fetched again by another node could have its newer
  execution completed, retried, or snoozed by a late ack from the original run.

- [Producer] Fix shutdown crash when jobs finish while draining

  Stopping a queue could raise "Set of Tasks/Futures is empty" if the last running jobs completed
  while the producer was recording its shutdown state.

- [Refresher] Restore producers missing after a database reset

  Producer records live in an unlogged table, which Postgres empties after a crash restart or
  failover. Previously the refresher only updated existing rows, so producers stayed missing until
  their queue restarted, and the Pro lifeline repeatedly rescued jobs that were still running. The
  refresher now re-inserts any missing producer records and reports a `restored_count` in its
  telemetry.

- [Stager] Keep notifying queues when staging jobs fails

  The stager notifies every local queue when there is a staging error such as a unique conflict.
  Previously, queues weren't notified after a failed stage, and a queue with no running jobs could
  leave available jobs unprocessed.
