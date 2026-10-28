<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/oban-bg/oban-py/refs/heads/main/docs/_static/oban-py-logotype-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/oban-bg/oban-py/refs/heads/main/docs/_static/oban-py-logotype-light.png">
    <img alt="Oban logo" src="https://raw.githubusercontent.com/oban-bg/oban-py/refs/heads/main/docs/_static/oban-py-logotype-light.png" width="320">
  </picture>
</p>

<p align="center">
  Oban is a sophisticated job orchestration framework for Python, backed by PostgreSQL.
  Reliable, <br /> observable, and loaded with <a href="#features">enterprise grade features</a>.
</p>

<p align="center">
  <a href="https://pypi.org/project/oban/">
    <img alt="PyPI Version" src="https://img.shields.io/pypi/v/oban.svg">
  </a>

  <a href="https://github.com/oban-bg/oban-py/actions">
    <img alt="CI Status" src="https://github.com/oban-bg/oban-py/workflows/ci/badge.svg">
  </a>

  <a href="https://opensource.org/licenses/Apache-2.0">
    <img alt="Apache 2 License" src="https://img.shields.io/pypi/l/oban">
  </a>
</p>

## Table of Contents

- [Features](#features)
- [Oban Pro](#-oban-pro)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Also Available](#also-available)
- [Community](#community)
- [Contributing](#contributing)

---

> [!NOTE]
>
> This README is for the unreleased main branch, please reference the [official docs][docs]
> for the latest stable release.


---

<!-- INDEX START -->

## Features

Oban's primary goals are **reliability**, **consistency** and **observability**.

Oban is a powerful and flexible library that can handle a wide range of background job use cases,
and it is well-suited for systems of any size. It provides a simple and consistent API for
scheduling and performing jobs, and it is built to be fault-tolerant and easy to monitor.

Oban is fundamentally different from other background job processing tools because _it retains job
data for historic metrics and inspection_. You can leave your application running indefinitely
without worrying about jobs being lost or orphaned due to crashes.

### Advantages Over Other Tools

- **Async Native** — Built entirely on asyncio with async/await throughout. Integrates naturally
  with async web frameworks.

- **Fewer Dependencies** — If you are running a web app there is a _very good_ chance that you're
  running on top of a SQL database. Running your job queue within a SQL database minimizes system
  dependencies and simplifies data backups.

- **Transactional Control** — Enqueue a job along with other database changes, ensuring that
  everything is committed or rolled back atomically.

- **Database Backups** — Jobs are stored inside of your primary database, which means they are
  backed up together with the data that they relate to.

### Advanced Features

- **Isolated Queues** — Jobs are stored in a single table but are executed in distinct queues.
  Each queue runs in isolation, with its own concurrency limits, ensuring that a job in a single
  slow queue can't back up other faster queues.

- **Queue Control** — Queues can be started, stopped, paused, resumed and scaled independently at
  runtime locally or across _all_ running nodes.

- **Resilient Queues** — Failing queries won't crash the entire process, instead a backoff
  mechanism will safely retry them again in the future.

- **Job Canceling** — Jobs can be canceled regardless of which node they are running on. For
  executing jobs, workers can check for cancellation at safe points and stop gracefully.

- **Triggered Execution** — Insert triggers ensure that jobs are dispatched on all connected nodes
  as soon as they are inserted into the database.

- **Scheduled Jobs** — Jobs can be scheduled at any time in the future, down to the second.

- **Periodic (CRON) Jobs** — Automatically enqueue jobs on a cron-like schedule. Duplicate jobs
  are never enqueued, no matter how many nodes you're running.

- **Job Priority** — Prioritize jobs within a queue to run ahead of others with ten levels of
  granularity.

- **Historic Metrics** — After a job is processed the row isn't deleted. Instead, the job is
  retained in the database to provide metrics. This allows users to inspect historic jobs and to
  see aggregate data at the job, queue or argument level.

- **Node Metrics** — Every queue records metrics to the database during runtime. These are used to
  monitor queue health across nodes and may be used for analytics.

- **Graceful Shutdown** — Queue shutdown is delayed so that slow jobs can finish executing before
  shutdown. When shutdown starts queues are paused and stop executing new jobs. Any jobs left
  running after the shutdown grace period may be rescued later.

- **Recorded Results** — Return a `Record` from a worker to store its result on the job for later
  retrieval.

- **Testing Helpers** — Run jobs inline or manually during tests, assert on enqueued jobs, and
  drain queues on demand.

- **Telemetry Integration** — Job and queue life-cycle events are emitted through `oban.telemetry`.
  This enables simple logging, error reporting and health checkups without plug-ins.

- **Web Dashboard** — Monitor queues, inspect jobs, and view execution metrics through
  [Oban Web](https://hexdocs.pm/oban_web). Run the standalone dashboard via Docker for a
  complete overview of your job processing system.

## 🌟 Oban Pro

Oban Pro is a licensed add-on that expands what Oban is capable of while making complex workflows
possible.

- **Drop-In Optimizations** — Switch to Pro for automatic bulk inserts, bulk acking, and
  accurate orphan rescue, with no code changes required.

- **Multi-Process Execution** — Bypass the GIL and utilize multiple cores for CPU-intensive
  workloads. Just switch from `oban start` to `obanpro start`.

- **Erlang Notifier** — Replace Postgres pub/sub with direct communication between processes.
  Works through transaction poolers, without a payload size limit, and keeps notifications out of
  the database.

- **Smart Concurrency** — Global limits across all nodes, rate limiting (e.g., 60 jobs/minute),
  and partitioned queues that apply limits per worker, tenant, or any argument.

- **Pro Pruner** — Fine-grained retention rules per queue, worker, or state. Prune on a cron
  schedule, manage rules at runtime, and archive jobs rather than deleting them.

- **Unique Jobs** — Prevent enqueueing duplicate jobs based on worker, args, queue, or specific
  keys, with configurable time windows.

- **Relay** — Dispatch jobs and await their results with synchronous-style semantics, even when
  jobs execute on different servers.

- **Awaiting Signals** — Pause a job mid-execution to wait for an external decision, then resume
  when a signal arrives. Ideal for approval flows and third-party callbacks.

- **Durable Workflows** — Compose jobs with dependencies for sequential, fan-out, and fan-in patterns.
  Sub-workflows, result sharing, cascading functions, and runtime grafting for dynamic pipelines.

- **Chunks** — Process jobs together in groups by size or wait time, e.g. 1,000 jobs or every 10
  seconds, and apply outcomes to individual jobs.

[Learn more about Oban Pro →](https://oban.pro)

## Requirements

Oban requires:

- Python 3.12+
- PostgreSQL 14.0+

## Installation

See the [installation guide][docs] for details on installing and configuring Oban for your
application.

## Quick Start

Get up and running in just a few steps: define a worker (or decorate a function), start processing
with the CLI (or embedded mode), and enqueue jobs.

1. Define a worker to process jobs:

   ```python
   from oban import worker


   @worker(queue="exports", max_attempts=5)
   class ExportWorker:
       async def process(self, job):
           report = await generate_report(job.args["report_id"])

           await send_to_user(job.args["email"], report)
   ```

2. Run with the CLI:

   ```bash
   # Install the database schema (once)
   oban install --dsn postgresql://localhost/mydb

   # Start processing jobs
   oban start --dsn postgresql://localhost/mydb --queues exports:10
   ```

   Or embed in your application (FastAPI, Django, etc.):

   ```python
   from oban import Oban

   oban = Oban(pool=pool, queues={"exports": 10})

   async with oban:
       ...  # Run your app
   ```

3. Enqueue jobs from anywhere in your app:

   ```python
   # Enqueue one job
   await ExportWorker.enqueue({"report_id": 123, "email": "user@example.com"})

   # Or many jobs at once
   await oban.enqueue_many(
       ExportWorker.new({"report_id": report_id, "email": "user@example.com"})
       for report_id in [1, 2, 3]
   )
   ```

For more details, see the [full documentation][docs].

[docs]: https://oban.pro/docs/py
[uv]: https://docs.astral.sh/uv/
<!-- INDEX END -->

## Also Available

[Oban for Elixir][oban-elixir] — The original Oban, with support for PostgreSQL, MySQL, and SQLite3

Oban for Python and Elixir are fully compatible — they share the same database schema and can
run side-by-side, making it easy to use both languages in the same system.

[oban-elixir]: https://github.com/oban-bg/oban

## Community

Submit bug reports and upcoming features in the [issue tracker][issues]

[issues]: https://github.com/oban-bg/oban-py/issues

## Contributing

To run the Oban test suite you must have Python 3.12+, PostgreSQL 14+, and [uv] installed. Follow
these steps to create the database and run all tests:

```bash
make test
```

To ensure a commit passes CI you should run `make ci`, or run these checks locally:

- Lint with Ruff (`uv run ruff check`)
- Check formatting (`uv run ruff format --check`)
- Check types (`uv run ty check`)
- Run tests (`uv run pytest`)

### Building Documentation

There are `make` commands available to help build and serve the documentation locally:

```bash
# Build HTML documentation
make docs

# Build and serve at http://localhost:8000
make docs-serve

# Clean built documentation
make docs-clean
```
