# Writing Jobs

This guide covers how to define workers and write effective job logic. You'll learn how to choose
between function and class-based workers, configure job options, control execution flow with
snooze and cancel, access job context, and customize retry behavior.

## Function vs Class Workers

Choose the right worker type for your needs:

Function workers (`@job`) are simpler but have limitations:

```python
from oban import job

@job(queue="default")
async def send_email(to: str, subject: str, body: str):
    # Simple and clean, but no access to job context
    await smtp.send(to, subject, body)

await send_email.enqueue("user@example.com", "Hello", "World")
```

Class workers (`@worker`) provide full control:

```python
from oban import worker

@worker(queue="default")
class EmailWorker:
    async def process(self, job):
        # Full access to job context
        if job.attempt > 2:
            await backup_smtp.send(job.args["to"], job.args["subject"])
        else:
            await primary_smtp.send(job.args["to"], job.args["subject"])

    def backoff(self, job):
        return 60 * job.attempt

await EmailWorker.enqueue({"to": "user@example.com", "subject": "Hello"})
```

Use function workers for simple, stateless operations. Use class workers when you need retry
customization, job metadata access, or complex flow control.

## Worker Configuration

Configure default behavior with decorator parameters:

```python
@worker(
    queue="critical",        # Queue name
    priority=0,              # 0 = highest priority
    max_attempts=5,          # Retry up to 5 times
    tags=["payment", "v2"],  # Default tags
)
class PaymentWorker:
    async def process(self, job):
        await process_payment(job.args)
```

Override defaults when enqueueing:

```python
await PaymentWorker.enqueue(
    {"amount": 100},
    priority=0,              # Override to highest priority
    tags=["urgent"],         # Override default tags
    max_attempts=3,          # Fewer retries for this job
    meta={"source": "api"},  # Add custom metadata
)
```

## Controlling Job Execution

Workers can return special values to control what happens after a job runs. Beyond simply
completing or raising an exception, you can snooze, cancel, or record results.

### Snoozing Jobs

Use `Snooze` to delay a job when conditions aren't right for processing. Unlike raising an
exception (which counts as a failed attempt), snoozing reschedules the job without penalizing the
attempt count:

```python
from oban import worker, Snooze

@worker(queue="default")
class PollingWorker:
    async def process(self, job):
        status = await check_report_status(job.args["report_id"])

        if status == "pending":
            return Snooze(seconds=30)  # Check again in 30 seconds

        await download_report(job.args["report_id"])
```

Snoozing is ideal for:

- Polling external APIs for completion
- Waiting for external resources to become available
- Rate limiting and backpressure

Each snooze increments a counter in `job.meta["snoozed"]`, allowing you to track how many times a
job has been snoozed and take alternative action:

```python
@worker(queue="default")
class LimitedSnoozeWorker:
    async def process(self, job):
        if not resource_available():
            if job.meta.get("snoozed", 0) >= 3:
                raise RuntimeError("Resource unavailable after 3 attempts")

            return Snooze(seconds=30)

        await use_resource()
```

### Cancelling Jobs

Use `Cancel` to stop a job gracefully without marking it as an error. This is useful when you
determine a job is no longer needed:

```python
from oban import worker, Cancel

@worker(queue="notifications")
class NotificationWorker:
    async def process(self, job):
        user = await get_user(job.args["user_id"])

        if user is None or user.notifications_disabled:
            return Cancel("User not found or notifications disabled")

        await send_notification(user, job.args["message"])
```

Jobs can also be cancelled externally via `oban.cancel_job()`. For long-running jobs, periodically
check `job.cancelled()` to handle this gracefully:

```python
@worker(queue="exports")
class ExportWorker:
    async def process(self, job):
        for chunk in data_chunks:
            if job.cancelled():
                return Cancel("Export cancelled by user")

            await process_chunk(chunk)
```

### Recording Results

Use `Record` to store computation results directly on the job. This is useful for expensive
computations where you want to persist the output:

```python
from oban import worker, Record

@worker(queue="analysis")
class AnalysisWorker:
    async def process(self, job):
        result = await run_expensive_analysis(job.args["dataset_id"])

        return Record({
            "score": result.score,
            "metrics": result.metrics,
            "computed_at": datetime.now().isoformat()
        })
```

Recorded values are stored in the job's `meta` field and can be retrieved later. The default size
limit is a _very large_ 64MB, but you can customize it for safety:

```python
return Record(large_result, limit=1_000_000)  # 1MB limit
```

```{tip}
Recorded values are encoded using Erlang Term Format for compatibility with [Oban Web][oban-web]
and [Oban for Elixir][oban-elixir]. Most Python primitives—strings, integers, floats, booleans,
lists, and dicts—are supported and will round-trip correctly between Python and Elixir.
```

[oban-web]: https://hexdocs.pm/oban_web
[oban-elixir]: https://hexdocs.pm/oban

## Accessing Job Attributes

Class-based workers receive a `Job` object with full context about the current execution. Here are
the most commonly used attributes:

### Core Attributes

```python
@worker(queue="default")
class ContextAwareWorker:
    async def process(self, job):
        # Job identification
        print(f"Job ID: {job.id}")
        print(f"Queue: {job.queue}")

        # Job arguments (your custom data)
        user_id = job.args["user_id"]
        action = job.args["action"]

        # Attempt tracking
        print(f"Attempt: {job.attempt} of {job.max_attempts}")

        # Priority (0 = highest, 9 = lowest)
        print(f"Priority: {job.priority}")

        # Tags for filtering and grouping
        if "urgent" in job.tags:
            await process_urgently()
```

### Metadata and Errors

The `meta` field stores arbitrary metadata, and `errors` contains records of previous failures:

```python
@worker(queue="default")
class RetryAwareWorker:
    async def process(self, job):
        # Custom metadata
        if job.meta.get("source") == "api":
            await log_api_job()

        # Check previous errors on retries
        if job.attempt > 0 and job.errors:
            last_error = job.errors[-1]
            print(f"Last failure: {last_error['error']} at {last_error['at']}")

            # Maybe try a different approach on retry
            if "timeout" in last_error["error"].lower():
                await process_with_longer_timeout()
                return

        await process_normally()
```

Each error record contains:

- `attempt`: The attempt number when the error occurred
- `at`: ISO 8601 timestamp of the failure
- `error`: String representation of the exception

## Retries and Backoff

When a worker raises an exception, Oban automatically retries the job (up to `max_attempts`). The
delay between retries is controlled by a backoff function.

### Default Backoff

Oban uses a jittered exponential backoff by default. The attempt number is clamped at 20 for jobs
with higher `max_attempts`. This table shows the base delay between attempts and cumulative time:

| Attempt | Base Delay | Cumulative |
|--------:|-----------:|-----------:|
| 1       | 17s        | 17s        |
| 2       | 19s        | 36s        |
| 3       | 23s        | 59s        |
| 4       | 31s        | 1m 30s     |
| 5       | 47s        | 2m 17s     |
| 6       | 1m 19s     | 3m 36s     |
| 7       | 2m 23s     | 5m 59s     |
| 8       | 4m 31s     | 10m 30s    |
| 10      | 17m 7s     | 44m 44s    |
| 15      | 9h 6m      | 18h 18m    |
| 20      | 12d 3h     | 24d 8h     |

Jitter is applied to all backoff, which spreads out retries to avoid thundering herd problems when
many jobs fail simultaneously

### Custom Backoff

Override the `backoff` method to customize retry timing:

```python
@worker(queue="default")
class LinearBackoffWorker:
    async def process(self, job):
        await do_work()

    def backoff(self, job):
        # Linear: 30s, 60s, 90s, 120s...
        return 30 * job.attempt
```
