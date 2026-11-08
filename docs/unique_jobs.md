# Unique Jobs

Unique jobs lets you specify constraints that prevent *enqueuing* duplicate jobs. These
constraints only apply when jobs are inserted. Uniqueness has no bearing on whether jobs are
*executed* concurrently.

Uniqueness is based on a combination of job attributes using the following options:

* **period** — How long from a job's scheduled time until it is no longer considered a duplicate.
  Can be an integer number of seconds, a `timedelta`, or a period string such as `"5 minutes"`
  or `"7 days"`. Supported units are `second`, `minute`, `hour`, `day`, `week`, and `month`
  (always 30 days). Defaults to `None`, where jobs are considered
  duplicates for as long as they're retained in the database (see
  [Job Maintenance](job_maintenance.md)).

* **fields** — The fields to compare when evaluating uniqueness. The available fields are `args`,
  `queue`, `worker`, and `meta`. Defaults to `["queue", "worker", "args"]`. It's recommended that
  you leave the default fields, otherwise you risk unexpected conflicts between unrelated jobs.

* **keys** — A specific subset of the `args` or `meta` to consider when comparing against
  historic jobs. This allows a job with multiple key/value pairs in its arguments to be compared
  using only a subset of them. Keys require `args` or `meta` in the fields.

* **group** — A named group of job states that are checked for duplicates:

  * `all` - All states, including `cancelled` and `discarded`
  * `incomplete` - Jobs that haven't finished processing: `scheduled`, `available`,
    `executing`, `retryable`, and `suspended`
  * `scheduled` - Only `scheduled` jobs (useful for "debouncing")
  * `successful` - All states except `cancelled` and `discarded` (the default)

## Configuring Uniqueness

The simplest form of uniqueness considers a job a duplicate for as long as a matching job exists in
the database, unless it was cancelled or discarded:

```python
@worker(queue="default", unique=True)
class MyWorker:
    async def process(self, job):
        pass
```

Uniqueness can also be set, overridden, or disabled for an individual job:

```python
# Jobs are unique for 5 minutes
await MyWorker.enqueue({"id": 1}, unique={"period": "5 minutes"})

# A timedelta works as well
await MyWorker.enqueue({"id": 1}, unique={"period": timedelta(minutes=5)})

# Skip uniqueness for this job entirely
await MyWorker.enqueue({"id": 1}, unique=False)
```

Here's a more complex example which uses multiple options:

```python
@worker(
    unique={
        # Jobs should be unique for 2 minutes after they're scheduled
        "period": "2 minutes",
        # Don't consider the whole args field, just the url key within args
        "keys": ["url"],
        # Consider a job unique across all states, including cancelled/discarded
        "group": "all",
        # Consider a job unique across queues; only compare the worker and url key
        "fields": ["worker", "args"],
    }
)
```

Options are validated when the job is built, so an unknown option, field, or group raises an
error immediately rather than at insert time.

```{tip}
Oban checks uniqueness with a transactional advisory lock and a query for matching jobs. It
*does not* rely on unique constraints in the database, which leaves it prone to race conditions in
some circumstances.

[Oban Pro][pro-unique] automatically enforces uniqueness with unique constraints, which provides
strong uniqueness guarantees.
```

[pro-unique]: https://oban.pro/docs/py_pro/adoption.html#automatic-improvements

## Uniqueness vs Concurrency

Understanding the distinction between uniqueness and concurrency is crucial for designing
efficient processing pipelines. While these concepts may seem related, they operate at different
stages of a job's lifecycle.

Uniqueness operates at **job insertion time**. When a job is marked as unique, Oban checks whether
an identical job already exists before inserting a new one.

* **When it applies** - During job insertion
* **What it prevents** - Duplicate jobs from being inserted
* **What it doesn't affect** - Which jobs run concurrently

A common misunderstanding is that unique jobs run one at a time or in sequence. This isn't
true—uniqueness only prevents duplicate insertions. Once unique jobs are in the queue, they'll run
according to the queue's concurrency settings.

For example, given the following configuration that allows 10 concurrent email jobs:

```toml
[queues]
emails = 10
```

These 10 unique jobs will all run concurrently:

```python
await oban.enqueue_many(
    EmailWorker.new({"user_id": id}, unique=True) for id in range(10)
)
```

To restrict the number of jobs that run at once you must set concurrency accordingly.

## Detecting Conflicts

When unique settings match an existing job, `enqueue` still returns a `Job`. However, the job
isn't inserted, and you can detect the conflict by checking `conflicted` in the job's `extra`. If
there was a matching job the value is `True`; otherwise it is `False`.

You can use the `conflicted` value to customize responses after insert:

```python
job = await MyWorker.enqueue({"id": 1}, unique=True)

if job.extra["conflicted"]:
    print("Job already exists")
else:
    print(f"Job inserted with ID: {job.id}")
```

Conflict detection works the same way for bulk inserts with `enqueue_many`. Each job is checked
individually, including against other jobs earlier in the same batch:

```python
jobs = await oban.enqueue_many(
    MyWorker.new({"id": 1}, unique=True),
    MyWorker.new({"id": 1}, unique=True),
)

[job.extra["conflicted"] for job in jobs]  # [False, True]
```

```{note}
A job is also marked as conflicted when another process is inserting an identical unique job at
the same moment. In that case one insert wins and the others are reported as conflicts.
```

## Specifying Fields and Keys

The `fields` option determines which high-level job attributes Oban will consider when checking
for uniqueness, including `args`, `queue`, `worker`, and `meta`.

When `args` or `meta` are included in the `fields` list, the `keys` option provides additional
specificity by allowing you to designate particular keys within the dictionary for comparison,
rather than comparing the entire dictionary.

Let's see this with an example:

```python
# This compares the entire args dictionary
await MyWorker.enqueue(
    {"url": "...", "user_id": 1},
    unique={"fields": ["worker", "queue", "args"]},
)

# This compares only the url key within the args dictionary
await MyWorker.enqueue(
    {"url": "...", "user_id": 1},
    unique={"keys": ["url"], "fields": ["worker", "queue", "args"]},
)
```

In the second example, the uniqueness check only looks at the `url` key within the `args`
dictionary because `keys` is specified. Jobs with the same `url` but different `user_id` values are
considered duplicates.

Without `keys`, the entire dictionary must match exactly. A job with args `{"id": 1}` is distinct
from one with `{"id": 1, "tenant": None}`, and empty args are distinct from non-empty args.
