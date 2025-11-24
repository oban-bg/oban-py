# Defining Queues

Queues are the foundation of how Oban organizes and processes jobs. They allow you to:

- Separate different types of work (e.g., emails, report generation, media processing)
- Control the concurrency of job execution
- Manage resource consumption across your application

Each queue operates independently with its own throughput and concurrency limits.

## Basic Queue Configuration

Queues are defined with a name the maximum number of concurrent jobs. The following configuration
would start four queues with concurrency ranging from 5 to 50:

```toml
# oban.toml

[queues]
default = 10
mailers = 20
media = 5
```

In this example:

- The `default` queue can process up to 10 jobs simultaneously
- The `mailers` queue can process up to 20 jobs simultaneously
- The `media` queue can process up to 5 jobs simultaneously

Once configured, start Oban to begin processing jobs:

```bash
oban start
```

The CLI will automatically load your queue configuration from `oban.toml` and start processing
jobs from all configured queues.

## Programmatic Configuration

If you're running Oban embedded in your application rather than using the CLI, you can configure
queues programmatically:

```python
oban = Oban(pool=pool, queues={"default": 10, "mailers": 20 })
```

## Advanced Queue Options

For more control over queue behavior, you can configure queues with additional options using
TOML's nested table syntax:

```toml
[queues.mailers]
limit = 20
debounce_interval = 0.05

[queues.media]
limit = 5
paused = true
```

This expanded configuration demonstrates several advanced options:

* The `mailers`  queue has a dispatch cooldown of 50ms between job fetching
* The `media` queue starts in a paused state, which means it won't process anything until
  it is resumed

### Paused Queues

When a queue is configured with `paused: True`, it won't process any jobs until explicitly
resumed. This is useful for maintenance periods, controlling 84 when resource-intensive jobs can
run, or temporarily disabling certain types of jobs.

You can resume a paused queue programmatically:

```python
await oban.resume_queue("media")
```

And pause an active queue:

```python
await oban.pause_queue("media")
```

## Queue Planning Guidelines

There isn't a limit to the number of queues or how many jobs may execute concurrently in each
queue. However, consider these important guidelines:

### Resource Considerations

* Each queue will run as many jobs as possible concurrently, up to the configured limit. Make sure
  your system has enough resources (such as *database connections*) to handle the concurrent load.

* Consider the total concurrency across all queues. For example, if you have 4 queues with limits
  of 10, 20, 30, and 40, your system needs to handle up to 100 concurrent jobs, each potentially
  requiring database connections and other resources.

### Concurrency and Distribution

* Queue limits are **local** (per-node), not global (per-cluster). For example, running a queue
  with a local limit of `2` on three separate nodes is effectively a global limit of *six
  concurrent jobs*.

### Queue Planning

* Only jobs in the configured queues will execute. Jobs in any other queue will stay in the
  database untouched. Be sure to configure all queues you intend to use.

* Organize queues by workload characteristics. For example:

  - CPU-intensive jobs might benefit from a dedicated low-concurrency queue
  - I/O-bound jobs (like sending emails) can often use higher concurrency
  - Priority work could have dedicated queues with higher concurrency

### External Process Considerations

* Pay attention to the number of concurrent jobs making expensive system calls (such as calls to
  resource-intensive tools like [FFmpeg][ffmpeg] or [ImageMagick][imagemagick]). Python's asyncio
  ensures your application stays responsive under load, but those guarantees don't apply when
  using subprocess calls or external processes.

* Consider creating dedicated queues with lower concurrency for jobs that interact with external
  processes or services that have their own concurrency limitations.

[ffmpeg]: https://www.ffmpeg.org
[imagemagick]: https://imagemagick.org/index.php
