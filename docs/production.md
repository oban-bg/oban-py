# Ready for Production

This guide covers the essentials for running Oban in production: logging, job maintenance, and
error monitoring.

## Using the CLI (Recommended)

If you're using `oban start` to run your workers, **you're already production-ready.**

The CLI automatically enables:

- ✅ **Logging**: Structured logs for all job and system events
- ✅ **Job Pruning**: Removes completed jobs after 1 day (configurable via `oban.toml`)
- ✅ **Orphan Rescue**: Recovers jobs interrupted by crashes or deployments

Simply start Oban with your configuration:

```bash
oban start
```

### Configuring Retention

You can adjust how long completed jobs are kept by configuring the pruner in `oban.toml`:

```toml
[pruner]
max_age = 3600  # Keep jobs for 1 hour instead of 1 day
```

That's it! The CLI handles everything else automatically.

## Running Embedded

If you're running Oban embedded in your application instead of using the CLI, you'll need to
configure a few things manually.

### Enable Logging

Attach the telemetry logger during application startup:

```python
from oban.telemetry import logger as telemetry_logger

telemetry_logger.attach()
```

The logger emits JSON-encoded structured logs at the `INFO` level by default. You can customize
the log level:

```python
telemetry_logger.attach(level=logging.DEBUG)
```

### Configure Job Maintenance

Pruning and orphan rescue are enabled by default, but you can customize them:

```python
from oban import Oban

pool = await Oban.create_pool()

oban = Oban(
    pool=pool,
    queues={"default": 10},
    pruner={"max_age": 3_600},  # Keep jobs for 1 hour
    lifeline={"interval": 30},  # Check for orphans every 30 seconds
)
```

See the [Job Maintenance](job_maintenance.md) guide for more details on job retention.

### Enable Metrics for Web Dashboard

To use the Oban Web dashboard, enable metrics broadcasting:

```python
oban = Oban(pool=pool, queues={"default": 10}, metrics=True)
```

See the [Web Dashboard](web_dashboard.md) guide for setup instructions.

## Sizing the Connection Pool

Oban runs its queries through a psycopg connection pool bounded by `pool_min_size` and
`pool_max_size`, which default to `1` and `10`.

A common mistake is to assume you need one connection per concurrently running job. You don't.
Workers only borrow a connection *briefly*—to fetch a batch of jobs or acknowledge results—then
return it. A queue with a limit of `100` does not hold `100` connections. What draws from the pool
are short, concurrent operations: producers fetching jobs, maintenance tasks (stager, scheduler,
pruner, lifeline), and your application calling `enqueue`. The PostgreSQL notifier keeps its own
dedicated connection outside the pool, so pubsub needs no pool slot.

The default of `10` is comfortable for most deployments. Raise `pool_max_size` if you enqueue at
high volume from many concurrent requests, or if you see timeouts acquiring a connection. Don't
oversize: every connection is a real backend, and the total across all your nodes counts against
Postgres's `max_connections` (and any PgBouncer limits).

Set the bounds in `oban.toml`:

```toml
pool_min_size = 2
pool_max_size = 20
```

Or directly in embedded mode via `Oban.create_pool(min_size=2, max_size=20)`.

## Graceful Shutdown

When Oban stops, queues stop fetching new jobs and wait for running jobs to finish. Jobs still
running after the shutdown grace period, 15 seconds by default, are cancelled and left for the
lifeline to rescue.

Keep the grace period below your platform's own shutdown timeout, e.g. Kubernetes'
`terminationGracePeriodSeconds` (30 seconds by default), so shutdown finishes before the process
is killed:

```toml
shutdown_grace_period = 25
```

Or directly in embedded mode via `Oban(shutdown_grace_period=25)`. Set it to `None` in embedded
mode to wait for running jobs indefinitely.

Cancellation is cooperative, so a job stops at its next `await`. Jobs that never await can't be
interrupted.

Each queue emits an `oban.queue.shutdown` telemetry event as it stops, with the `queue`, the
`elapsed` time in nanoseconds, and the `orphaned` job ids left executing. The telemetry logger
reports shutdowns that orphan jobs as a warning.

## Leadership

Nodes that run queues take part in leader election, and only the leader runs cron scheduling and
maintenance. The leader holds a lease in the database that it renews on the leader interval, and
it announces when it exits so another node can take over immediately.

That announcement travels over `LISTEN`/`NOTIFY`, which connection poolers in transaction mode,
such as PgBouncer, don't support. Leadership still transfers in that setup, but only after the
previous lease expires, which can take up to the leader interval of 30 seconds by default.

Every instance needs a distinct node name for leadership to work, see the
[node identity note](managing_queues.md) for the default and how to override it.

## Telemetry Events

The telemetry logger is built on Oban's telemetry events, which you can also attach to directly
for metrics, tracing, or error reporting:

```python
from oban import telemetry


def handle_event(name, meta): ...


telemetry.attach("my-app", ["oban.job.stop", "oban.job.exception"], handle_event)
```

Job execution emits `oban.job.start`, followed by either `oban.job.stop` or
`oban.job.exception`. Each includes the `job`, and the stop and exception events add the final
`state`, `duration`, and `queue_time` in nanoseconds. Exception events also carry the
`error_type`, `error_message`, and `traceback`.

Internal processes report their work as spans, emitting `.start`, `.stop`, and `.exception`
events under these prefixes:

| Prefix                    | Metadata                           |
| ------------------------- | ---------------------------------- |
| `oban.leader.election`    | `leader`                           |
| `oban.lifeline.rescue`    | `rescued_count`                    |
| `oban.producer.ack`       | `queue`, `count`                   |
| `oban.producer.get`       | `queue`, `count`                   |
| `oban.pruner.prune`       | `pruned_count`                     |
| `oban.refresher.cleanup`  | `cleanup_count`                    |
| `oban.refresher.refresh`  | `refreshed_count`                  |
| `oban.scheduler.evaluate` | `cron_at`, `enqueued_count`        |
| `oban.stager.stage`       | `staged_count`, `available_queues` |

Stop and exception events include the span's `duration`, and exception events add the
`error_type`, `error_message`, and `traceback`. Queues also emit `oban.queue.shutdown`, covered
under Graceful Shutdown above.

Attaching to a name that's never emitted fails silently, so check your subscriptions against
`oban.telemetry.EVENTS`, the complete list of emitted events.

## Ship It!

Whether you're using the CLI or embedded mode, you now have:

- ✅ **Structured logging** for debugging and monitoring
- ✅ **Automatic job pruning** to prevent unbounded table growth
- ✅ **Orphan recovery** for interrupted jobs

The CLI handles all of this automatically, making it the recommended approach for most production
deployments.
