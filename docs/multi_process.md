# Multi-Process

Oban can execute jobs across multiple worker processes, allowing CPU-intensive jobs to run in
parallel. This bypasses Python's Global Interpreter Lock (GIL) and enables true parallelism for
compute-heavy workloads.

## Why Multi-Process?

Python's GIL prevents multiple threads from executing Python bytecode simultaneously. While asyncio
handles I/O-bound concurrency efficiently, CPU-intensive tasks block the event loop and become a
bottleneck.

Multi-process execution solves this by running jobs in separate Python processes, each with its own
GIL and asyncio event loop. This provides:

- **Parallelism** for CPU-bound work across multiple cores
- **Concurrency** within each process for I/O-bound work

Jobs within each process are still async, so a single process can efficiently handle many
concurrent I/O operations (API calls, database queries) while also benefiting from parallel CPU
execution across workers.

This makes multi-process execution ideal for:

- **Data processing** — parsing, transforming, or aggregating large datasets
- **Machine learning** — model inference or batch predictions
- **Cryptographic operations** — hashing, encryption, or signature verification
- **Scientific computing** — numerical simulations or statistical analysis

## Enabling Multi-Process

By default, `oban start` executes jobs within its own event loop. Set the number of worker
processes with the `--processes` flag to execute jobs across multiple processes instead:

```bash
oban start --processes 4
```

Or use the `OBAN_PROCESSES` environment variable:

```bash
export OBAN_PROCESSES=4
oban start
```

Or set `processes` in `oban.toml`:

```toml
processes = 4
```

Each worker process runs its own asyncio event loop. Jobs are distributed across processes, with
each process handling its share of concurrent jobs _across all queues_.

```{tip}
Size the process count from the CPUs actually available to Oban. In a container, that's the CPU
limit (Docker's `--cpus` or a Kubernetes CPU limit), not the number of cores on the host. Each
process also opens its own [connection pool](#database-connections), so an inflated process count
can exhaust PostgreSQL connections.
```

## Concurrency vs Parallelism

Queue limits control concurrency, or the total number of jobs that can execute at once. This works
identically with or without multiple processes, where a queue with a limit of 20 can run up to 20
jobs concurrently. With multi-processing, jobs are distributed between native processes allowing
CPU-bound work to run simultaneously on multiple cores.

The number of processes does *not* multiply concurrency. For example, with 4 processes and a queue
limit of 20, you still get 20 concurrent jobs while utilizing 4 CPU cores for parallel work.

```bash
# 20 concurrent jobs distributed across 4 parallel processes
oban start --processes 4 --queues "default:20"
```

### Recommended Configuration

For CPU-intensive workloads, tailor the number of processes to available cores:

```bash
oban start --processes 8 --queues "heavy:16"
```

For mixed workloads, use moderate queue limits with enough processes:

```bash
oban start --processes 4 --queues "default:20,heavy:8"
```

```{tip}
Unix systems use copy-on-write memory and processes share the parent's memory pages until
modified, so loading large read-only data (like ML models) before starting reduces total memory
usage. However, Python's reference counting can trigger copies when objects are accessed, so
memory savings vary. For memory-constrained environments, favor fewer processes with higher queue
limits.
```

(database-connections)=

## Database Connections

Database connections can't be shared across processes, so each worker process opens its own
connection pool in addition to the main process. The total number of connections is therefore
`(processes × pool_max_size) + main_pool_max_size`, and that total must stay within your
PostgreSQL `max_connections`.

To keep the count manageable, worker pools default to a maximum size of 5 rather than inheriting
the main pool's size. Setting `--pool-max-size` (or `OBAN_POOL_MAX_SIZE`) applies to worker pools
as well, so size it with the multiplier in mind:

```bash
# 4 workers × 4 + 4 for the main process = 20 connections
oban start --processes 4 --pool-max-size 4
```

Worker queries through `Oban.get_instance()` use the worker's own pool automatically, so no setup
hook is required for database access.

## Setup and Teardown Hooks

Worker processes may need to initialize per-process resources like ML models or external clients.
Use the `--setup` and `--teardown` options to specify initialization functions:

```bash
oban start --processes 4 --setup myapp.worker_setup --teardown myapp.worker_cleanup
```

The hooks can also be set with the `OBAN_SETUP` and `OBAN_TEARDOWN` environment variables, or with
`setup` and `teardown` in `oban.toml`.

The setup function runs once when each worker process starts, and the teardown function runs once
as it stops:

```python
# myapp.py
model = None


async def worker_setup():
    global model
    model = load_model("model.bin")


async def worker_cleanup():
    global model
    model = None
```

```{note}
Database access doesn't belong in a setup hook. Each worker process opens its own Oban connection
pool automatically (see [Database Connections](#database-connections)), so `Oban.get_instance()`
works inside workers without any extra setup.
```

```{note}
Setup and teardown functions must be async and importable by path (e.g., `myapp.worker_setup`).
```

## Performance Comparison

Multi-process execution provides significant speedups for CPU-bound work. In a benchmark of 100
jobs with "heavy" CPU work (100k SHA-256 hash iterations for each job), the speedup is apparent:

| Configuration                                    | Time     | Speedup  |
| ------------------------------------------------ | -------- | -------- |
| Single process (`oban start`)                    | ~2,100ms | 1x       |
| Multi-process with 4 workers (`oban start -p 4`) | ~770ms   | **2.7x** |

The speedup scales with the number of processes, up to the number of available CPU cores. Note
that the benchmark isn't representative of maximum throughput as it includes database pool
creation and process instantiation.
