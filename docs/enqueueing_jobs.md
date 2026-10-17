# Enqueueing Jobs

Oban has flexible options for enqueueing jobs, including scheduling and transactional insertion.
This guide covers scheduling jobs for execution in the future and inserting jobs atomically with
other database changes.

## Schedule in Relative Time

You can schedule jobs to run after a specific delay using the `scheduled_in` parameter:

```python
from datetime import timedelta

# Using timedelta
await MyWorker.enqueue({"id": 1}, scheduled_in=timedelta(seconds=5))

# Or using seconds directly
await MyWorker.enqueue({"id": 1}, scheduled_in=5)
```

This is useful for tasks that need to happen after a short delay, such as sending a follow-up
email or retrying a failed operation.

## Schedule at a Specific Time

For tasks that need to run at a precise moment, you can schedule jobs at a *specific timestamp*
using the `scheduled_at` parameter:

```python
await MyWorker.enqueue({"id": 1}, scheduled_at=some_datetime)
```

This is particularly useful for time-sensitive operations like executing a maintenance task at
off-hours, or preparing monthly reports.

### Time Zone Considerations

Scheduling in Oban is *always* done in UTC. If you're working with timestamps in different time
zones, you should convert them to UTC before scheduling:

```python
from datetime import timezone

utc_time = local_datetime.astimezone(timezone.utc)

await MyWorker.enqueue({"id": 1}, scheduled_at=utc_time)
```

This ensures consistent behavior across different server locations and prevents daylight saving
time issues.

### How Scheduling Works

Behind the scenes, Oban stores your job in the database with the specified scheduled time. The job
remains in a `scheduled` state until that time arrives, at which point it becomes `available` for
execution by the appropriate worker.

Scheduled jobs don't consume worker resources until they're ready to execute, allowing you to
queue thousands of future jobs without impacting current performance.

## Transactional Insertion

Oban supports inserting jobs within a database transaction, ensuring that insertion is atomic with
other database changes. If the transaction rolls back, the job is never inserted.

This is useful when you need to guarantee consistency between your application data and background
jobs. For example, when creating a user record and enqueueing a welcome email together.

### With SQLAlchemy

Pass your `SQLAlchemy` session directly to `enqueue()`:

```python
async with session.begin():
    user = User(email="user@example.com", status="active")
    session.add(user)

    await WelcomeEmailWorker.enqueue({"email": user.email}, conn=session)
```

For multiple jobs, use `enqueue_many()`:

```python
async with session.begin():
    order = Order(user_id=user.id, total=100)
    session.add(order)

    await oban.enqueue_many(
        SendReceiptWorker.new({"user_id": user.id}),
        UpdateInventoryWorker.new({"user_id": user.id}),
        conn=session,
    )
```

### With Psycopg

You can also pass a `psycopg` connection directly:

```python
async with oban._connection() as conn:
    async with conn.transaction():
        await conn.execute("INSERT INTO users ...")
        await oban.enqueue(MyWorker.new({"user_id": user_id}), conn=conn)
```

### Supported Connection Types

The `conn` parameter accepts serveral connection types, all of which resolve to a `psycopg`
connection:

- SQLAlchemy `AsyncSession`
- SQLAlchemy `AsyncConnection`
- psycopg `AsyncConnection`
- Any object with an `execute()` method
