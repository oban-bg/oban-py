# Oban

Oban is a robust job orchestration framework for Python, backed by PostgreSQL.

Oban provides a scalable, reliable, and easy-to-use system for background job processing. It leverages PostgreSQL for storage, ensuring durability and transactional integrity for your jobs.

## Features

- **Reliable Job Processing**: PostgreSQL-backed job queue with ACID guarantees
- **Worker Decorators**: Simple `@job` and `@worker` decorators for defining job handlers
- **Flexible Scheduling**: Schedule jobs for immediate or future execution
- **Built-in Retries**: Automatic retry with configurable backoff strategies
- **Queue Management**: Multiple queues with configurable concurrency limits
- **Testing Support**: Comprehensive testing utilities for different modes
- **CLI Tools**: Command-line interface for running workers and managing schema
- **Telemetry**: Built-in event system for monitoring and observability
- **Cron Scheduling**: Periodic job execution with cron-like syntax

## Quick Links

```{toctree}
:maxdepth: 2
:caption: Contents:

installation
quickstart
api
cli
```

## Project Information

- **License**: Apache 2.0
- **Python Version**: 3.12+
- **Source Code**: [GitHub](https://github.com/sorentwo/oban)

## Indices and Tables

- {ref}`genindex`
- {ref}`modindex`
- {ref}`search`
