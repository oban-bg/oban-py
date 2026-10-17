# Testing

Since Oban orchestrates your application's background tasks, testing Oban is highly recommended.
Automated testing is essential for building reliable applications. This guide covers unit testing
workers, integration testing with queues, and managing test data.

```{tip}
Because enqueueing jobs, processing jobs, and asserting on test data are `async` actions, tests
must also be `async`.
```

## Test Configuration

For integration testing with queues, an `Oban` instance must be initialized (but not started), so
it is running in client-only mode. This prevents Oban from automatically processing jobs in the
background, while allowing you to enqueue job as needed.

Typically, your app will initialize Oban when it boots. If you're testing in isolation, without
the app running, then you'll want to start oban as a fixture:

```python
import pytest
from oban import Oban


@pytest.fixture
async def oban():
    pool = await Oban.create_pool()
    oban = Oban(pool=pool)

    async with oban:
        yield oban

    await pool.close()
```

## Testing Modes

Oban provides two testing modes controlled via the `oban.testing.mode()` context manager:

- **inline** — Jobs execute immediately within the calling process without touching the database.
  Simple and suitable for most tests.
- **manual** — Jobs are inserted into the database where they can be verified and executed when
  desired. More flexible but requires database interaction.

By default, jobs are inserted into the database in tests (manual mode). You can switch modes
within a test using the context manager:

```python
import oban.testing


async def test_with_inline_mode():
    with oban.testing.mode("inline"):
        # Job executes immediately without hitting the database
        result = await send_email.enqueue("user@example.com", "Hello")
        assert result.state == "completed"
```

## Unit Testing Workers

Worker modules are the primary "unit" of an Oban system. You should test worker logic locally,
in-process, without having Oban touch the database.

### Testing Process Methods

The `process_job()` helper simplifies unit testing by handling boilerplate like JSON
encoding/decoding and setting required job fields.

Let's test a worker that activates user accounts:

```python
from oban import worker
from oban.testing import process_job


@worker(queue="default")
class ActivationWorker:
    async def process(self, job):
        user = await User.activate(job.args["id"])

        return {"activated": user.email}


async def test_activating_a_new_user():
    user = await User.create(email="parker@example.com")

    job = ActivationWorker.new({"id": user.id})
    result = await process_job(job)

    assert result["activated"] == "parker@example.com"
```

The `process_job()` helper:

- Converts `args` to JSON and back to ensure valid types
- Sets required fields like `id`, `attempt`, `attempted_at`
- Executes the worker's `process()` method
- Returns the result for assertions

### Testing Function Workers

Function-based workers created with `@job` can be tested the same way:

```python
from oban import job
from oban.testing import process_job


@job(queue="default")
async def send_notification(user_id: int, message: str):
    await NotificationService.send(user_id, message)
    return {"sent": True}


async def test_send_notification():
    job = send_notification.new(123, "Hello World")
    result = await process_job(job)

    assert result["sent"] is True
```

## Integration Testing with Queues

For integration tests, you'll want to verify that jobs are enqueued correctly and can execute them
when needed. The `oban.testing` module provides helpers for these scenarios.

### Asserting Enqueued Jobs

Use `assert_enqueued()` to verify a job was enqueued:

```python
from oban.testing import assert_enqueued


async def test_signup_enqueues_activation(app):
    await app.post("/signup", json={"email": "foo@example.com"})

    await assert_enqueued(
        worker=ActivationWorker, args={"email": "parker@example.com"}, queue="default"
    )
```

You can assert on partial args without matching exact values:

```python
async def test_enqueued_args_have_email_key():
    await Account.notify_owners(account)

    # Match jobs that have an "email" key, regardless of value
    await assert_enqueued(queue="default", args={"email": "foo@example.com"})
```

Use `scheduled_in` to match jobs scheduled relative to now, within a second, rather than computing
an exact `scheduled_at`:

```python
async def test_reminder_is_scheduled():
    await Account.schedule_reminder(account)

    await assert_enqueued(worker=ReminderWorker, scheduled_in=timedelta(hours=1))
```

### Refuting Enqueued Jobs

Use `refute_enqueued()` to assert no matching job was enqueued:

```python
from oban.testing import refute_enqueued


async def test_invalid_signup_skips_activation(app):
    response = await app.post("/signup", json={"email": "invalid"})

    assert response.status_code == 400
    await refute_enqueued(worker=ActivationWorker)
```

### Asserting Multiple Jobs

For complex assertions on multiple jobs, use `all_enqueued()`:

```python
from oban.testing import all_enqueued


async def test_notifies_all_owners():
    await Account.notify_owners(account_with_3_owners)

    jobs = await all_enqueued(worker=NotificationWorker)
    assert len(jobs) == 3

    # Make complex assertions on each job's args
    for job in jobs:
        assert "email" in job.args
        assert "name" in job.args
```

### Waiting for Async Jobs

If your application enqueues jobs asynchronously, use the `timeout` parameter:

```python
async def test_async_job_enqueued():
    # Trigger async operation that enqueues a job
    await trigger_background_process()

    # Wait up to 0.2 seconds for the job to be enqueued
    await assert_enqueued(worker=ProcessWorker, timeout=0.2)
```

## Executing Jobs in Tests

Sometimes you need to actually execute jobs during integration tests. Use `drain_queue()` to
process all pending jobs in a queue:

```python
from oban.testing import drain_queue


async def test_email_delivery():
    await Business.schedule_meeting({"email": "monty@brewster.com"})

    result = await drain_queue(queue="mailers")
    assert result["completed"] == 1
    assert result["discarded"] == 0

    # Now assert that the email was sent
    assert len(sent_emails) == 1
```

The `drain_queue()` function supports several options:

```python
# Drain without scheduled jobs
await drain_queue(queue="default", with_scheduled=False)

# Drain without recursion (jobs that enqueue other jobs won't run)
await drain_queue(queue="default", with_recursion=False)

# Drain with safety (errors are recorded, not raised)
await drain_queue(queue="default", with_safety=True)
```

### Resetting Between Tests

Use `reset_oban()` to clean up job data between tests:

```python
import pytest
from oban.testing import reset_oban


@pytest.fixture(autouse=True)
async def _reset_oban():
    yield

    await reset_oban()
```

Choose the appropriate strategy based on what you're testing. Use unit tests for worker logic,
integration tests with assertions for enqueueing verification, and draining for end-to-end
scenarios.
