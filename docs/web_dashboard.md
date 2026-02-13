# Web Dashboard

[Oban Web](https://hexdocs.pm/oban_web) provides a real-time dashboard for monitoring queues,
inspecting jobs, and viewing execution metrics. A standalone Docker image makes it easy to run
the dashboard alongside your Python application.

## Enable Metrics

Before setting up the dashboard, enable metrics broadcasting in your `oban.toml`:

```toml
metrics = true
```

This broadcasts queue state, job execution metrics, and cron schedules via PostgreSQL pub/sub,
which the dashboard uses for real-time updates.

## Running the Dashboard

Pull and run the standalone Docker image:

```bash
docker run -d \
  -e DATABASE_URL="postgres://user:pass@host.docker.internal:5432/myapp" \
  -p 4000:4000 \
  ghcr.io/oban-bg/oban-dash
```

Then visit `http://localhost:4000/oban`.

```{note}
Use `host.docker.internal` instead of `localhost` when connecting to a database on your host
machine.
```

## Configuration

Configure the dashboard using environment variables:

| Variable          | Required | Default  | Description                     |
| ----------------- | -------- | -------- | ------------------------------- |
| `DATABASE_URL`    | Yes      | —        | PostgreSQL connection URL       |
| `POOL_SIZE`       | No       | `5`      | Database connection pool size   |
| `PORT`            | No       | `4000`   | HTTP port                       |
| `OBAN_PREFIX`     | No       | `public` | Oban table schema prefix        |
| `OBAN_READ_ONLY`  | No       | `false`  | Disable job actions when `true` |
| `BASIC_AUTH_USER` | No       | —        | Basic auth username             |
| `BASIC_AUTH_PASS` | No       | —        | Basic auth password             |

## Authentication

Enable HTTP Basic Authentication for production deployments:

```bash
docker run -d \
  -e DATABASE_URL="postgres://user:pass@host.docker.internal:5432/myapp" \
  -e BASIC_AUTH_USER="admin" \
  -e BASIC_AUTH_PASS="secret" \
  -p 4000:4000 \
  ghcr.io/oban-bg/oban-dash
```

## Read-Only Mode

Disable job actions (cancel, retry, delete) by enabling read-only mode:

```bash
docker run -d \
  -e DATABASE_URL="postgres://user:pass@host.docker.internal:5432/myapp" \
  -e OBAN_READ_ONLY="true" \
  -p 4000:4000 \
  ghcr.io/oban-bg/oban-dash
```

## Health Checks

The dashboard exposes a health check endpoint at `/health` that returns `{"status":"ok"}`.
Docker's built-in `HEALTHCHECK` monitors this endpoint automatically.

## Tuning Metrics

The default metrics configuration works well for most deployments. For high-volume systems, you
can tune these options:

```toml
[metrics]
interval = 1.0           # Broadcast frequency in seconds
estimate_limit = 50000   # Use estimates above this job count
cronitor_interval = 30   # Cron schedule broadcast frequency
counts_enabled = true    # Enable job state counts
```

| Option              | Default  | Description                                      |
| ------------------- | -------- | ------------------------------------------------ |
| `interval`          | `1.0`    | How often metrics are broadcast (in seconds)     |
| `estimate_limit`    | `50000`  | Job count threshold for switching to estimates   |
| `cronitor_interval` | `30`     | How often cron schedules are broadcast (seconds) |
| `counts_enabled`    | `true`   | Enable broadcasting job state counts             |

### Estimated Counts

Counting jobs in each state can become expensive at scale. When the count for a state exceeds
`estimate_limit`, Oban switches to PostgreSQL's statistics-based estimates. These are fast and
accurate enough for dashboard displays, while exact counts are used for states with fewer jobs.

Lower the limit if you have many jobs and notice database load from count queries:

```toml
[metrics]
estimate_limit = 10000
```

### Hybrid Elixir/Python Environments

When running both Elixir and Python Oban workers against the same database, disable counts on the
Python side to avoid duplicate job state counts in the dashboard:

```toml
[metrics]
counts_enabled = false
```

The Elixir instance will handle broadcasting job counts, while Python workers still report their
own execution metrics (timing, throughput, etc.).
