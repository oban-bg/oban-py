# Quickstart Guide

This guide will help you get started with Oban quickly.

## Defining Workers

Oban provides two ways to define workers: function-based and class-based.

### Function-based Workers

Use the `@job` decorator to define a function-based worker:

```python
from oban import job

@job(queue="default")
def send_email(email_address, subject, body):
    # Your email sending logic here
    print(f"Sending email to {email_address}")
```

### Class-based Workers

Use the `@worker` decorator for class-based workers:

```python
from oban import worker

@worker(queue="exports", priority=2)
class ExportWorker:
    async def process(self, job):
        # Access job arguments
        file_path = job.args["file_path"]
        # Your export logic here
        print(f"Exporting {file_path}")
```

## Setting up Oban

Create an Oban instance with your database connection and queue configuration:

```python
from oban import Oban

oban = Oban(
    dsn="postgresql://user:password@localhost/mydb",
    queues={"default": 10, "mailers": 5, "exports": 2},
    pruner={"max_age": 60}  # Prune jobs older than 60 seconds
)
```

The queue configuration specifies the maximum number of concurrent jobs per queue.

## Enqueueing Jobs

### Basic Enqueueing

Enqueue jobs from your application code:

```python
# Function-based worker
await send_email.enqueue("user@example.com", "Welcome", "Thanks for signing up!")

# Class-based worker
await ExportWorker.enqueue({"file_path": "/data/export.csv"})
```

### Batch Enqueueing

Enqueue multiple jobs efficiently:

```python
oban.enqueue_many(
    send_email.new("user1@example.com", "Hello", "Message 1"),
    send_email.new("user2@example.com", "Hello", "Message 2"),
)
```

### Scheduled Jobs

Schedule jobs for future execution:

```python
from datetime import datetime, timedelta

# Schedule for a specific time
scheduled_time = datetime.now() + timedelta(hours=24)
await send_email.enqueue(
    "user@example.com",
    "Reminder",
    "Don't forget!",
    scheduled_at=scheduled_time
)
```

## Running Workers

### Using the CLI

Start the Oban worker process:

```bash
oban run --dsn "postgresql://user:password@localhost/mydb"
```

### Programmatically

Run Oban as a context manager in your application:

```python
import signal
from myapp.oban import oban

if __name__ == "__main__":
    with oban:
        signal.pause()
```

Or manage the lifecycle manually:

```python
async def main():
    await oban.start()
    # Your application logic
    await oban.stop()
```

## Client-only Mode

If you only need to enqueue jobs (e.g., in a web application):

```python
# web.py
from oban import Oban

# Don't specify queues for client-only mode
oban = Oban(dsn="postgresql://user:password@localhost/mydb")

@app.route("/send-email")
async def send_email_route():
    await send_email.enqueue("user@example.com", "Hi", "Hello!")
    return "Email queued"
```

Run a separate worker process to actually process the jobs.

## Next Steps

- Check out the [API Reference](api) for detailed information about all available options
- Learn about [CLI commands](cli) for managing Oban
- Explore advanced features like retries, unique jobs, and telemetry in the full documentation
