# Managing Jobs

Oban provides methods to manage jobs after they've been inserted. You can cancel jobs to prevent
execution, retry failed jobs, delete jobs from the database, or update job fields.

## Cancelling Jobs

Cancel a job to prevent it from running and potentially stop it while it is executing:

```python
# Cancel with the job's id
await oban.cancel_job(123)

# Or with a job instance
await oban.cancel_job(job)
```

Only incomplete jobs, ones that aren't `completed`, `discarded`, or already `cancelled`, can be
cancelled. Cancellation changes the job status to `cancelled` and records a timestamp in the
database.

For jobs that are already executing, the database state is updated immediately, but _the running
task isn't forcefully terminated_. Workers should check for cancellation at safe points and stop
gracefully:

```python
class MyWorker:
    async def process(self, job):
        for item in dataset:
            if job.cancelled():
                return Cancel("Job was cancelled")

            await process_item(item)
```

To cancel multiple jobs at once:

```python
count = await oban.cancel_many_jobs([123, 456, 789])
print(f"Cancelled {count} jobs")
```

## Retrying Jobs

Retrying changes the job's status to `available`, making it ready for immediate execution:

```python
# Cancel with the job's id
await oban.retry_job(123)

# Or with a job instance
await oban.retry_job(job)
```

Jobs that are already `available` or `executing` are ignored. If the job has already exhausted
its `max_attempts`, the limit is automatically increased to allow the retry.

To retry multiple jobs at once:

```python
count = await oban.retry_many_jobs([123, 456, 789])
print(f"Retried {count} jobs")
```

```{tip}
The term "retry" is a slight misnomer because any non-executing job can be retried-not just
failed ones. For example, you can retry a `scheduled` job to make it `available` immediately,
whether it has ever ran before.
```

## Deleting Jobs

Deletion permanently removes a job from the database. This is useful for cleaning up jobs that
were inserted by mistake, contain sensitive data that shouldn't be retained, or are no longer
relevant and you don't want them to count toward historical metrics.

To delete a job:

```python
await oban.delete_job(123)

# Or with a job instance
await oban.delete_job(job)
```

Jobs in the `executing` state cannot be deleted. If you need to stop an executing job, cancel it
first and then delete it afterwards. Cancelling is preferred over deletion because it retains a
record that the job was purposefully stopped.

To delete multiple jobs at once:

```python
count = await oban.delete_many_jobs([123, 456, 789])
print(f"Deleted {count} jobs")
```

## Updating Jobs

Updating lets you modify a job's fields after insertion. Common use cases include:

- **Reprioritizing**: Bump a job's priority in response to user action or business rules
- **Rescheduling**: Delay a job that's waiting on an external dependency
- **Reassigning**: Move a job to a different queue based on its content or current load
- **Annotating**: Add metadata for tracking, debugging, or coordination between jobs

To update a job:

```python
await oban.update_job(job, {"priority": 0, "tags": ["urgent"]})

# Or by job ID
await oban.update_job(123, {"queue": "critical"})
```

The following fields can be updated:

| Field          | Description                                  |
| -------------- | -------------------------------------------- |
| `args`         | Job arguments                                |
| `max_attempts` | Maximum retry attempts                       |
| `meta`         | Arbitrary metadata                           |
| `priority`     | Job priority (0-9, lower is higher priority) |
| `queue`        | Target queue name                            |
| `scheduled_at` | When the job should run                      |
| `tags`         | List of string tags                          |
| `worker`       | Worker class name                            |

For scheduling convenience, you can use `schedule_in` instead of `scheduled_at`. For example, to
make a scheduled job run right away:

```python
await oban.update_job(job, {"schedule_in": 0})
```

### Dynamic Updates with Callables

For updates that depend on the current job state, pass a callable that receives the job and
returns a dict of changes:

```python
# Append to existing tags
await oban.update_job(job, lambda job: {"tags": job.tags + ["processed"]})

# Merge into existing meta
await oban.update_job(job, lambda job: {
    "meta": {**job.meta, "reviewed_at": datetime.now().isoformat()}
})
```

To update multiple jobs at once:

```python
jobs = await oban.update_many_jobs([123, 456, 789], {"priority": 0})
```

With a callable, each job is updated individually based on its current state:

```python
jobs = await oban.update_many_jobs(
    job_ids,
    lambda job: {"meta": {**job.meta, "batch_id": batch_id}}
)
```

```{warning}
Use caution when updating jobs that are currently executing. Modifying fields like `args`,
`queue`, or `worker` while a job is running may lead to unexpected behavior. Consider cancelling
the job first, or deferring the update until after execution completes.
```
