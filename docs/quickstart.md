# Quickstart Guide

This guide walks you through the essentials of defining workers, enqueueing jobs, and actually
running Oban.

## Defining Workers

Oban provides two ways to define workers: function-based for simple tasks, and class-based when
you need more control.

### Function Workers

For simple tasks, use the `@job` decorator to turn any function into a worker:

```python
from oban import job


@job(queue="default")
def send_email(email, subject, body):
    print("Sending an email...")
```

### Class Workers

Use the `@worker` decorator when you need access to job details (e.g. attempts, tags, meta) or
want to customize retry backoff logic:

```python
from oban import worker


@worker(queue="exports", priority=2)
class ExportWorker:
    async def process(self, job):
        file_path = job.args["path"]
        print("Generating an export...")
```

### Periodic Jobs (Cron)

Periodic jobs run on a predictable schedule. Unlike one-time scheduled jobs, periodic jobs repeat
automatically without requiring you to insert new jobs after each execution.

Define workers that run on a schedule using the `cron` parameter:

```python
from oban import worker


@worker(queue="maintenance", cron="@daily")
class DailyCleanup:
    async def process(self, job):
        print("Running daily cleanup...")


@worker(queue="reports", cron="0 9 * * MON-FRI")
class BusinessHoursReport:
    async def process(self, job):
        print("Generating report during business hours...")
```

## Enqueueing Jobs

Once you've defined workers, you can enqueue jobs to run them. Jobs are stored in PostgreSQL and
executed later when available.

For function workers, pass the same parameters as you would for the original function:

```python
await send_email.enqueue("user@example.com", "Welcome", "Thanks for signing up!")
```

For class-based workers, enqueue using a single `args` dictionary:

```python
await ExportWorker.enqueue({"path": "/data/export.csv"})
```

### Batch Enqueueing

When you need to enqueue many jobs at once, use `enqueue_many()` for better performance. It
inserts all jobs in a single database query:

```python
oban.enqueue_many(
    send_email.new("user1@example.com", "Hello", "Message 1"),
    send_email.new("user2@example.com", "Hello", "Message 2"),
)
```

### Scheduled Jobs

Schedule jobs to run at a specific time in the future. This is useful for reminders, follow-ups,
or any task that shouldn't run immediately:

```python
from datetime import timedelta

await send_email.enqueue(
    "user@example.com", "Reminder", "Don't forget!", schedule_in=timedelta(hours=6)
)
```

## Setting up and Running Oban

Before enqueueing or processing jobs, you need to get an Oban instance running.

That's either done "standalone" (separate worker process) or "embedded" (workers run alongside
your web app). For either option, you need some configuration.

### Configuration

Start by creating an `oban.toml` file in your project root:

```toml
dsn = "postgresql://user:password@localhost/mydb"

[queues]
default = 10
mailers = 5
exports = 2

[pruner]
max_age = 3_600  # Prune completed jobs older than 1 hour
```

The queue values specify maximum concurrent jobs per queue. Configuration is loaded automatically
from `oban.toml`, environment variables, and can be overridden programmatically as needed.

### Running in Standalone Mode

This approach separates your web app (which only enqueues jobs) from the worker process (which
executes them). It's ideal for scaling workers independently and keeping job execution out of the
web process:

**In your web app:**

```python
from oban import Oban

pool = await Oban.create_pool()
oban = Oban(pool=pool)  # No queues, client-only


@app.route("/send-email")
async def send_email_route():
    await send_email.enqueue("user@example.com", "Hi", "Hello!")
    return "Email queued"
```

**Start the worker process with the CLI:**

```bash
oban start
```

### Running in Embedded Mode

This approach runs workers directly in your application process. It's simpler for development
and works well for lightweight apps where job processing doesn't need independent scaling.

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from oban import Oban


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await Oban.create_pool()
    oban = Oban(pool=pool, queues={"default": 10})

    async with oban:
        yield


app = FastAPI(lifespan=lifespan)
```

Keep exploring to learn about advanced features like retry backoff and advanced
scheduling.
