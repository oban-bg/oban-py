# Periodic Jobs

Periodic jobs allow you to schedule recurring tasks that execute on a predictable schedule. Unlike
one-time scheduled jobs, periodic jobs repeat automatically without requiring you to insert new
jobs after each execution.

Oban automatically manages these recurring jobs when you define workers with the `cron` parameter,
allowing you to use familiar cron syntax.

## Defining Periodic Jobs

Periodic jobs are defined using the `cron` parameter in the `@worker` or `@job` decorator:

```python
from oban import worker


@worker(queue="maintenance", cron="* * * * *")
class MinuteWorker:
    async def process(self, job):
        print("Running every minute")


@worker(queue="reports", cron="0 * * * *")
class HourlyWorker:
    async def process(self, job):
        print("Running at the first minute of every hour")


@worker(queue="cleanup", cron="0 0 * * *")
class DailyWorker:
    async def process(self, job):
        print("Running at midnight every day")


@worker(queue="scheduled", cron="0 12 * * MON")
class MondayWorker:
    async def process(self, job):
        print("Running at noon every Monday")


@worker(queue="simple", cron="@daily")
class AnotherDailyWorker:
    async def process(self, job):
        print("Running at midnight every day using a cron alias")
```

## How Periodic Jobs Work

When you start Oban with the CLI or embed it in your application, the scheduler automatically
inserts jobs according to the schedule you define. When the time comes for a job to run, Oban:

1. Creates a new job for the specified worker
2. Enqueues the job in the appropriate queue
3. Executes it when a worker becomes available

Jobs are always inserted by the leader node in a cluster, which prevents duplicate jobs regardless
of cluster size, restarts, or crashes.

## Cron Expressions

Standard cron expressions consist of five fields that specify when the job should run:

```
┌───────────── minute (0 - 59)
│ ┌───────────── hour (0 - 23)
│ │ ┌───────────── day of the month (1 - 31)
│ │ │ ┌───────────── month (1 - 12 or JAN-DEC)
│ │ │ │ ┌───────────── day of the week (0 - 6 or SUN-SAT)
│ │ │ │ │
│ │ │ │ │
* * * * *
```

Each field supports several notation types:

- `*` — Wildcard, matches any value (0, 1, 2, …)
- `0` — Literal, matches only the specific value (only 0)
- `*/15` — Step, matches any value that is a multiple (0, 15, 30, 45)
- `0-5` — Range, matches any value within the range (0, 1, 2, 3, 4, 5)
- `0-9/2` — Step values can be used with ranges (0, 2, 4, 6, 8)
- `1,3,5` — Comma-separated values, matches any listed value (1, 3, 5)

Each part may have multiple rules, where rules are separated by a comma. The allowed values for
each field are as follows:

| Field      | Allowed Values                               |
| ---------- | -------------------------------------------- |
| `minute`   | 0-59                                         |
| `hour`     | 0-23                                         |
| `days`     | 1-31                                         |
| `month`    | 1-12 (or aliases, `JAN`, `FEB`, `MAR`, etc.) |
| `weekdays` | 0-6 (or aliases, `SUN`, `MON`, `TUE`, etc.)  |

```{tip}
Cron syntax can be difficult to write and read. We recommend using a tool like
[Crontab Guru][guru] to make sense of cron expressions and write new ones.
```

### Cron Aliases

Oban supports these common cron aliases for better readability:

| Expression                 | Translates To |
| -------------------------- | ------------- |
| `@hourly`                  | `0 * * * *`   |
| `@daily` (or `@midnight`)  | `0 0 * * *`   |
| `@weekly`                  | `0 0 * * 0`   |
| `@monthly`                 | `0 0 1 * *`   |
| `@yearly` (or `@annually`) | `0 0 1 1 *`   |

### Practical Examples

Here are some specific examples to help you understand the full range of expressions:

- `0 * * * *` — The first minute of every hour
- `*/15 9-17 * * *` — Every fifteen minutes during standard business hours (9 AM to 5 PM)
- `0 0 * DEC *` — Once a day at midnight during December
- `0 7-9,16-18 * * MON-FRI` — Once an hour during morning and evening rush hours on weekdays
- `0 0 1,15 * *` — Twice monthly on the 1st and 15th at midnight
- `0 7-9,16-18 13 * FRI` — Once an hour during rush hours on Friday the 13th

## Timezone Configuration

All schedules are evaluated as UTC by default. To run jobs according to a specific timezone, you
can configure the scheduler or specify a timezone per job:

### Global Timezone

Configure the scheduler timezone in `oban.toml`:

```toml
[scheduler]
timezone = "America/Chicago"
```

Or programmatically:

```python
oban = Oban(pool=pool, scheduler={"timezone": "America/Chicago"})
```

### Per-Job Timezone

You can also specify a timezone for individual jobs:

```python
@worker(cron={"expr": "0 9 * * MON-FRI", "timezone": "America/Chicago"})
class BusinessHoursReport:
    async def process(self, job):
        print("Running during Chicago business hours")
```

This ensures jobs run at the expected local time, even if your server is in a different timezone.

## Running Periodic Jobs

### Using the CLI

The CLI automatically discovers and loads workers with cron schedules:

```bash
# Auto-discover in current directory
oban start

# Specify paths to search
oban start --cron-paths "app/workers,app/jobs"

# Specify modules directly
oban start --cron-modules "app.workers,app.jobs"
```

### Embedded Mode

When running Oban embedded in your application, make sure to import your worker modules before
starting Oban:

```python
from app.workers import DailyCleanup, HourlyReport  # Import workers
from oban import Oban

pool = await Oban.create_pool()
oban = Oban(pool=pool, queues={"default": 10})

async with oban:
    # Workers are now registered and periodic jobs will run
    await asyncio.Event().wait()
```

## Periodic Guidelines

- **Timezone Considerations**: All schedules are evaluated as UTC unless a different timezone is
  configured. Use the scheduler timezone configuration or per-job timezone settings to ensure jobs
  run at the expected local time.

- **Overlapping Executions**: Long-running jobs may execute simultaneously if the scheduling
  interval is shorter than the time it takes to execute the job. For example, if a job scheduled to
  run every minute takes two minutes to complete, you'll have two instances running concurrently.
  Design your workers with this possibility in mind.

- **Resolution Limit**: Cron scheduling has a one-minute resolution at minimum. For more frequent
  executions, consider an alternative approach (e.g., using an async loop, or scheduling sub-jobs
  from a per-minute worker).

[guru]: https://crontab.guru
