# Changelog

This release ports multi-process and unique jobs from Oban Pro, removes the compiler requirement
for installation, and includes extensive fixes for integration with other languages.

## Multi-Process Execution

The most-requested, "why is that only in Pro" feature, was certainly multi-process. While asyncio
is fine for I/O-bound jobs, CPU-intensive jobs were still limited to a single core. Jobs can now
be executed across multiple worker processes, each with its own event loop and connection pool, to
run work in parallel:

```bash
oban start --processes 4 --queues "default:20,heavy:8"
```

Concurrent processes can be controlled with an environment variable, or with a static `processes`
value in `oban.toml`. Queue limits still control total concurrency, so processes add parallelism
without multiplying the number of jobs that run at once.

There are optional setup and teardown hooks for loading per-process resources like large ML
models:

```toml
processes = 4
setup = "myapp.worker_setup"
teardown = "myapp.worker_cleanup"
```

See the [Multi-Process][multi] guide for sizing processes and database connections.

[multi]: https://oban.pro/docs/py/multi_process.html

## Unique Jobs

Enqueueing the same work twice, from a double click, a retried request, or an overlapping cron,
previously required checking for an existing job yourself. Jobs can now be marked unique to
help prevent inserting duplicates. Set `unique` on a worker to apply it to every job:

```python
@worker(queue="mailers", unique=True)
class WelcomeEmail:
    async def process(self, job):
        await send_welcome(job.args["user_id"])
```

Or set, override, and disable it for an individual job. Uniqueness can be limited to a period, to
particular keys within the args, or to a group of job states:

```python
@worker(unique={"period": "5 minutes", "keys": ["account_id"]})
```

Uniqueness is enforced on insert during `enqueue` and `enqueue_many`, with an advisory lock and a
query for matches just like Oban for Elixir.

Finally, uniqueness is best-effort and race conditions may still allow duplicates through. For
strong guarantees, [Oban Pro][pro-unique] automatically enforces uniqueness with database
constraints (and bulk inserting unique jobs is ~2-3x faster).

See the [Unique Jobs][unique] guide for all of the options.

[unique]: https://oban.pro/docs/py/unique_jobs.html
[pro-unique]: https://oban.pro/docs/py_pro/adoption.html#automatic-improvements

## Compiler Free Encoding

Oban previously used the `erlpack` package for ETF (Erlang Term Format) encoding, the portable
binary format used to share data with other Oban instances. The `erlpack` dependency didn't ship
any wheels, which made installing Oban dependent on build tools.

Oban now includes a pure Python encoder instead of `erlpack` and its `six` dependency. There are
fewer dependencies, simpler builds, and full compatibility with Oban for other ecosystems.

## v0.8.0 — 2026-11-16

### Enhancements

- [CLI] Port multi-process execution from Pro

  Jobs can be executed across multiple worker processes to bypass the GIL and use multiple cores
  for CPU-intensive work. Set the number of processes with `oban start --processes`, the
  `OBAN_PROCESSES` environment variable, or `processes` in `oban.toml`. Each process runs its own
  event loop and connection pool, and queue limits still control overall concurrency.

  Optional setup and teardown hooks run as each worker process starts and stops, for loading
  per-process resources such as ML models. Without processes, jobs execute in a single process as
  before.

- [Config] Accept period strings for unique and pruning

  Unique periods and the pruner's `max_age` now accept strings such as "30 minutes", "1 hour", or
  "7 days", in addition to integers and timedeltas. This works in `oban.toml` as well:

  ```toml
  [pruner]
  max_age = "1 day"
  ```

- [Cron] Include `cron_tz` in cron job meta

  Cron jobs now record the timezone they were scheduled in, matching the meta written by Elixir.

- [Job] Add simplified unique jobs

  Jobs can be marked unique to prevent inserting duplicates, as in Oban Pro. Set `unique` on a
  worker or per job, or pass `unique=False` to disable it for a single job. Options are validated
  when the job is built.

  Uniqueness is checked at insert time with a transactional advisory lock and a query for matching
  jobs, mirroring Oban for Elixir. It works for both `enqueue` and `enqueue_many`, with each job
  checked individually, including against earlier jobs in the same batch.

  Unique jobs check against all states except `cancelled` and `discarded` by default, matching
  Oban for Elixir's semantics. Lock keys are hashed in the database rather than in Python, so
  every Oban implementation can derive the key the same way without a particular hash library.

- [Leader] Scope leadership by language and name

  Leader names are prefixed with `py:` to isolate Python from other languages sharing the
  `oban_leaders` table.

- [Metrics] Emit telemetry span for metrics broadcasts

  Metrics broadcasts now run within an `oban.metrics.broadcast` span, matching other internal
  processes. Broadcast failures emit `oban.metrics.broadcast.exception` events, so they reach the
  telemetry logger and any handlers attached to `EVENTS`.

- [Notifier] Start the notifier before queue producers

  Oban now starts the notifier together with its other services, and starts queue producers only
  once the notifier and dispatcher are running. Every startup channel is live when `start()`
  returns, and startup is as fast as before.

  `Notifier.listen` no longer takes a `wait` option. It always returns once the subscription is
  live, with an optional timeout to cap the wait.

- [Oban] Add `Oban.all_jobs` for listing jobs

  Add `Oban.all_jobs`, which returns jobs in any state, most recently inserted first.

- [Oban] Lock and refresh jobs when updating

  Updating jobs now fetches and locks each one within the update transaction, so changes and
  callables always build on current values rather than a possibly stale job instance. Updated
  jobs are returned as new instances, and the job passed in is no longer modified.

  Executing jobs keep their original values for the running attempt, and retrying or snoozing
  replaces an updated `scheduled_at`.

- [Oban] Pass additional queue options through `start_queue`

  Options beyond `limit` and `paused` are included in the signal to other nodes, so Pro options
  such as `global_limit` can be set when starting a queue at runtime.

- [Oban] Replace erlpack with a pure Python term codec

  Recorded values are now encoded with a built-in Erlang term codec rather than erlpack, which
  ships no wheels and had to be compiled at install. This removes the need for a compiler on slim
  or Alpine images and drops the erlpack and six dependencies.

  References also round-trip through the term codec, matching oban-js.

- [Oban] Add `oban.init` extension for construction

  The extension can be used to hook into instance construction, including the ability to stop
  construction entirely.

- [Producer] Add a `producer.check` extension and gossip meta

  Add a `check` extension for augmenting the queue info returned for producers.

- [Producer] Get jobs for paused producers without fetching

  Paused producers skipped getting jobs entirely, so extensions had no chance to keep current
  while running jobs finished. The default now fetches nothing while paused.

- [Schema] Prefix every `oban_` name when loading queries

  Loading SQL now prefixes every `oban_` name rather than a fixed list, skipping only index and
  trigger names that Postgres doesn't allow to be qualified. Tables from extensions are prefixed
  without further changes.

- [Stager] Skip staging when a node runs no queues

  Avoid the staging query entirely when a node isn't actively running any queues. Expiring unique
  jobs is skipped as well, so nodes running in client mode no longer run a pointless query every
  second.

- [Telemetry] Emit telemetry for job operations

  Enqueueing, cancelling, deleting, retrying, and updating jobs now emit `oban.jobs.*` spans, so
  there's a way to observe or hook into job changes made outside of execution.

  Start events include the requested `job_ids` (or `jobs` for enqueue), and stop events add the
  `jobs` that actually changed along with their `count`. Cancel, delete, and retry now return the
  affected rows so the stop event reflects what changed rather than what was requested.

  The telemetry logger records these at debug level with job ids rather than full jobs.

- [Telemetry] Include the instance name in all telemetry events

  Every event Oban emits now includes the `name` of the instance that emitted it, so handlers can
  tell instances apart when running more than one. That covers job execution, background work,
  job operations, and custom spans emitted while a job runs.

- [Telemetry] Tighten telemetry metadata

  Every event now includes a `system_time`, and job events no longer include `monotonic_time`.
  Background spans report a uniform `count` rather than per-event names like `pruned_count`, and
  queue shutdown events report `duration` rather than `elapsed`. Handlers that read the old keys
  need to be updated.

  The telemetry logger derives its default events from `telemetry.EVENTS` and handles span start
  events, which have no duration.

- [Worker] Apply worker registration hooks to older workers

  Hooks that run when a worker is registered now also apply to workers registered before the hook
  was set, so Pro features like relay and chunk work regardless of import order.

### Bug Fixes

- [Cron] Preserve worker default meta on periodic jobs

  Periodic jobs replaced the worker's default meta with scheduling details. Cron fields are now
  merged into the default meta instead.

- [Leader] Fix leader lease expiring after seconds

  The TTL placeholder sat inside an interval string literal, so every lease lasted a few seconds
  and other nodes could claim leadership between refreshes.

- [Notifier] Establish notifier subscriptions before start

  Signals and insert notifications sent immediately after starting Oban could be lost, because
  subscriptions weren't established until the notifier's next poll. This dropped job
  cancellations, remote queue starts and stops, and delayed processing for jobs inserted right
  after startup.

- [Notifier] Decode compressed notifier payloads from Elixir

  Elixir gzips notification payloads over 512 bytes and prefixes them with "Z:". Those payloads
  couldn't be decoded, so large gossip messages from Elixir nodes were dropped. Oban itself
  didn't subscribe to the gossip channel, so this is an edge case.

- [Oban] Handle queue signals sent from Elixir nodes

  Elixir includes `node` and `local_only` in queue signals, which crashed starting a queue with a
  duplicate node argument. Those keys are now ignored.

- [Oban] Record atoms as strings for safe decoding in Elixir

  Recorded values are decoded in Elixir with `binary_to_term(bin, [:safe])`, which rejects atoms
  that don't already exist on the node. Atoms in recorded values are now encoded as strings so
  they always decode.

- [Producer] Record errors for jobs cancelled while executing

  A job cancelled from elsewhere while it was running, such as from an Elixir node, would lose
  the error from its own cancel when it finished. Cancel acks now also apply to jobs that are
  already cancelled, which matches Elixir.

- [Schema] Fix repeated installs with a non-public prefix

  Installing into a custom prefix a second time failed with "type already exists" because the
  existence checks looked in the current schema rather than the configured prefix.

- [Schema] Scope `oban_count_estimate` to the configured prefix

  The count estimate function was always created in the current schema, regardless of prefix, and
  uninstall never removed it. It's now created within the prefix and dropped on uninstall.
  Installations using a custom prefix should re-run install after upgrading.

- [Schema] Check for suspended without casting to `regtype`

  Use a safer `to_regtype` cast that can't fail for missing types.

- [Stager] Notify queues with newly staged jobs immediately

  Staged jobs weren't visible to the final select of the staging query, so their queues waited
  until the next stage cycle.

- [Stager] Report number of staged jobs rather than queues

  The stager's staged count telemetry was the number of distinct queues with available jobs, not
  the number of jobs moved to available. The staging query now returns the actual count of jobs
  it staged.

- [Telemetry] Use wall clock time for telemetry span `system_time`

  The `system_time` in span start, stop, and exception events was taken from the monotonic clock,
  so it couldn't be read as a timestamp. It is now the wall clock time in nanoseconds since the
  epoch, while duration is still measured with the monotonic clock.

- [Testing] Drain all available jobs in a single pass

  With `with_recursion=False`, `drain_queue` only executed one job rather than every available
  job as documented. It now fetches and runs all available jobs in one pass, leaving any jobs
  inserted during the pass enqueued.
