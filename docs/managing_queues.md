# Managing Queues

Once queues are [defined and running](defining_queues.md), you're able to inspect and control them
at runtime. This is essential for dynamic processing, handling traffic bursts, and troubleshooting
unexpected conditions.

## Pausing and Resuming Queues

Pause a queue to stop it from starting new jobs.

```python
# Pause across all nodes
await oban.pause_queue("media")
```

By default, that queue is paused across all running instances (thanks to pubsub notifications).
It's also possible to pause on a specific node:

```python
# Pause only on a specific node
await oban.pause_queue("media", node="worker.1")
```

Jobs that are already running will continue until they complete. Resume a paused queue to allow it
to process jobs again:

```python
# Resume across all nodes
await oban.resume_queue("media")

# Resume only on a specific node
await oban.resume_queue("media", node="worker.1")
```

Some use cases for pausing queues:

- **Maintenance windows**: Pause queues before deploying or running migrations
- **Incident response**: Stop processing a problematic queue while investigating
- **Resource management**: Pause low-priority queues during high-load periods

## Scaling Queues

A queue's concurrency limit may be changed across all nodes at runtime without restarting:

```python
# Scale up to handle increased load
await oban.scale_queue(queue="default", limit=50)

# Scale down to reduce resource usage
await oban.scale_queue(queue="default", limit=5)
```

Like pausing and resuming, you can target a single node for scaling:

```python
# Scale only on a specific node
await oban.scale_queue(queue="default", limit=20, node="worker.1")
```

Scaling up triggers immediate job fetching, so queued jobs will start executing right away.
Scaling down doesn't interrupt running jobs—it only affects how many new jobs can start.

Some use cases for scaling:

- **Traffic bursts**: Scale up queues during peak hours, scale down during quiet periods
- **Gradual rollouts**: Start with low concurrency to validate changes, then scale up
- **Resource balancing**: Shift capacity between queues based on current demand

## Starting and Stopping Queues

Queues can be started and stopped dynamically across all nodes at runtime:

```python
# Start a queue across all nodes
await oban.start_queue(queue="priority", limit=10)

# Start a queue in a paused state
await oban.start_queue(queue="batch", limit=20, paused=True)
```

Naturally, you can scope starting and stopping to a single node as well:

```python
# Start only on a specific node
await oban.start_queue(queue="local_only", limit=5, node="worker.1")
```

To stop a running queue:

```python
# Stop across all nodes
await oban.stop_queue("priority")

# Stop only on a specific node
await oban.stop_queue("priority", node="worker.1")
```

Stopping a queue allows running jobs to complete gracefully—it doesn't terminate them immediately.

Some use cases for dynamic queues:

- **Feature flags**: Start queues only when certain features are enabled
- **Tenant isolation**: Create dedicated queues for specific customers
- **Batch processing**: Start temporary queues for large batches, stop when done

## Node-Specific Operations

All queue management methods accept an optional `node` parameter to target specific nodes in a
cluster. This is useful when:

- Different nodes have different resource capacities
- You want to drain a node before maintenance
- Testing changes on a single node before rolling out

```python
# Pause only on the node being maintained
await oban.pause_queue("default", node="worker.3")

# Scale up a specific high-capacity node
await oban.scale_queue(queue="media", limit=100, node="gpu-worker.1")
```

If no `node` is specified, the operation applies to all nodes running that queue.

```{tip}
The term **node** comes from Oban's Elixir heritage, where it refers to a running instance of the
virtual machine. In Python, a node is simply a single running instance of your application.

When you run `oban start` or start Oban embedded in your app, that process becomes a node with its
own unique identifier. In a typical deployment, you might have multiple nodes (containers,
servers, or processes) all running Oban and processing jobs _from the same database_.

The default node name combines the hostname and process id, such as `web-1.4021`, so several
processes on one host stay distinct. It can be overridden with the `node` option or the
`OBAN_NODE` environment variable, but must be unique.
```

## Checking Queue State

When running Oban in embedded mode, you can use `check_queue` and `check_all_queues` to inspect
the state of queues on the _current instance_. These methods only have visibility into queues
running in the same process—they cannot see queues on other nodes.

```python
# Check a specific queue
info = oban.check_queue("default")
if info:
    print(f"Queue: {info.queue}")
    print(f"Limit: {info.limit}")
    print(f"Running: {len(info.running)} jobs")
    print(f"Paused: {info.paused}")

# Check all queues on this node
for info in oban.check_all_queues():
    print(f"{info.queue}: {len(info.running)}/{info.limit} running, paused={info.paused}")
```

The returned `QueueInfo` includes:

| Field        | Description                                |
| ------------ | ------------------------------------------ |
| `queue`      | The queue name                             |
| `limit`      | Current concurrency limit                  |
| `running`    | List of currently executing job IDs        |
| `paused`     | Whether the queue is paused                |
| `node`       | The node where this queue is running       |
| `started_at` | When the queue was started                 |
| `meta`       | Queue metadata including extension options |

Note that `check_queue` returns `None` if the queue isn't running on the current node, while
`check_all_queues` returns an empty list if no queues are running locally.
