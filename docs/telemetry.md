# Telemetry

Oban emits telemetry events as jobs run and as it does its background work, such as staging,
pruning, and leadership. This guide covers logging those events, attaching your own handlers for
metrics, tracing, or error reporting, the metadata each event carries, and wrapping your own work
in the same kind of events.

## Logging

The built-in logger handles Oban's events and writes each one as JSON through Python's `logging`
module. The CLI attaches it automatically. When running embedded, attach it when your application
starts:

```python
from oban.telemetry import logger as telemetry_logger

telemetry_logger.attach()
```

By default, it logs every job's start, stop, and exception at the `INFO` level, background work at
`DEBUG` when it succeeds and `ERROR` when it fails, and queue shutdowns that orphan jobs at
`WARNING`. Job events include the job's id, worker, queue, attempt, args, meta, and tags, along
with its final state and timings once it finishes.

Logs are written to the `oban` logger unless given another one. Choose the level for job events
with `level`:

```python
import logging

telemetry_logger.attach(level=logging.DEBUG, logger=logging.getLogger("myapp.oban"))
```

Pass `events` to log only some events, such as just job exceptions:

```python
telemetry_logger.attach(events=["oban.job.exception"])
```

Use `telemetry_logger.detach()` to stop logging.

## Custom Handlers

Attach a handler to report events wherever you need them. Each handler gets an id, a list of
events, and a function called with the event's name and metadata:

```python
from oban import telemetry


def record_duration(name, meta):
    job = meta["job"]

    metrics.timing(
        "oban.job.duration", meta["duration"], worker=job.worker, state=meta["state"]
    )


telemetry.attach(
    "job-metrics", ["oban.job.stop", "oban.job.exception"], record_duration
)
```

Handlers are called synchronously, in the order they were attached, so keep them quick and hand
slow work off elsewhere. A handler that raises is logged, and doesn't affect other handlers or the
job that emitted the event.

Remove a handler by its id with `telemetry.detach("job-metrics")`. Attaching with an id that's
already in use adds another handler rather than replacing the first, so detach before attaching
again.

### Checking Event Names

Attaching to a name that's never emitted fails silently, so a typo means a handler that never
runs. Check your subscriptions against `telemetry.EVENTS`, the complete list of emitted events:

```python
events = ["oban.job.stop", "oban.job.exception", "oban.pruner.prune.stop"]
unknown = [name for name in events if name not in telemetry.EVENTS]

if unknown:
    raise ValueError(f"Unknown Oban events: {', '.join(unknown)}")
```

## Job Events

Each job emits `oban.job.start` when it starts running, followed by either `oban.job.stop` or
`oban.job.exception` when it finishes:

| Event                | Metadata                                                                 |
| -------------------- | ------------------------------------------------------------------------ |
| `oban.job.start`     | `job`, `monotonic_time`, `system_time`                                   |
| `oban.job.stop`      | `job`, `state`, `duration`, `queue_time`, `monotonic_time`               |
| `oban.job.exception` | `job`, `state`, `duration`, `queue_time`, `monotonic_time`, error fields |

The `duration` is how long the job ran, and `queue_time` how long it waited between when it was
scheduled and when it started, both in nanoseconds. The `system_time` is a timestamp in
nanoseconds since the epoch.

Only failed attempts emit an exception event, with `state` as `retryable` or `discarded`, and the
`error_type`, `error_message`, and `traceback` of the raised exception. Everything else emits a
stop event, with `state` as `completed`, or as `cancelled` or `scheduled` for jobs that returned
`Cancel` or `Snooze`. That makes exception events the natural place to report errors:

```python
def report_error(_name, meta):
    error_tracker.capture(
        meta["error_message"],
        extra={
            "id": meta["job"].id,
            "worker": meta["job"].worker,
            "traceback": meta["traceback"],
        },
    )


telemetry.attach("job-errors", ["oban.job.exception"], report_error)
```

## Background Events

Oban's background work reports itself as spans, emitting `.start` before each run, then `.stop`
when it succeeds or `.exception` when it raises:

| Prefix                    | Metadata                            |
| ------------------------- | ----------------------------------- |
| `oban.leader.election`    | `leader`                            |
| `oban.lifeline.rescue`    | `rescued_count`                     |
| `oban.metrics.broadcast`  |                                     |
| `oban.producer.ack`       | `queue`, `count`                    |
| `oban.producer.get`       | `queue`, `count`                    |
| `oban.pruner.prune`       | `pruned_count`                      |
| `oban.refresher.cleanup`  | `cleanup_count`                     |
| `oban.refresher.refresh`  | `refreshed_count`, `restored_count` |
| `oban.scheduler.evaluate` | `cron_at`, `enqueued_count`         |
| `oban.stager.stage`       | `staged_count`, `available_queues`  |

Every event includes a `system_time`. Stop and exception events add the span's `duration` in
nanoseconds, and exception events add the `error_type`, `error_message`, and `traceback`. Counts
are only known once the work is done, so they're on the stop event rather than the start.

For example, to track how many jobs the pruner deletes:

```python
def record_pruned(_name, meta):
    metrics.increment("oban.pruned", meta["pruned_count"])


telemetry.attach("pruner", ["oban.pruner.prune.stop"], record_pruned)
```

## Queue Shutdown

Each queue emits `oban.queue.shutdown` as it stops, with the `queue`, the `elapsed` time in
nanoseconds, and the `orphaned` ids of jobs still running when the grace period ended. Orphaned
jobs are left for the lifeline to rescue, and the logger reports any shutdown that orphans jobs as
a warning.

## Custom Spans

Wrap your own work in a span to emit events the same way Oban does, such as within a worker.
Metadata added to the collector is merged into the stop or exception event:

```python
with telemetry.span("myapp.report", {"report_id": report_id}) as collector:
    rows = await generate_report(report_id)

    collector.add({"row_count": len(rows)})
```

A span that raises emits its exception event, then re-raises the exception.
