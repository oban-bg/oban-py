from __future__ import annotations

import asyncio
import logging
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import click
from psycopg_pool import AsyncConnectionPool

from oban.config import Config
from oban.schema import install as install_schema, uninstall as uninstall_schema
from oban.telemetry import logger as telemetry_logger

try:
    from uvloop import run as asyncio_run
except ImportError:
    from asyncio import run as asyncio_run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("oban.cli")


@asynccontextmanager
async def schema_pool(database_url: str) -> AsyncIterator[AsyncConnectionPool]:
    if not database_url:
        raise click.UsageError("--database-url is required (or set OBAN_DATABASE_URL)")

    conf = Config(database_url=database_url, pool_min_size=1, pool_max_size=1)
    pool = await conf.create_pool()

    try:
        yield pool
    finally:
        await pool.close()


def handle_signals() -> asyncio.Event:
    shutdown_event = asyncio.Event()
    sigint_count = 0

    def signal_handler(signum: int) -> None:
        nonlocal sigint_count

        shutdown_event.set()

        if signum == signal.SIGTERM:
            logger.info("Received SIGTERM, initiating graceful shutdown...")
        elif signum == signal.SIGINT:
            sigint_count += 1

            if sigint_count == 1:
                logger.info("Received SIGINT, initiating graceful shutdown...")
                logger.info("Send another SIGINT to force exit")
            else:
                logger.warning("Forcing exit...")
                sys.exit(1)

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, lambda: signal_handler(signal.SIGTERM))
    loop.add_signal_handler(signal.SIGINT, lambda: signal_handler(signal.SIGINT))

    return shutdown_event


@click.group(
    context_settings={
        "help_option_names": ["-h", "--help"],
    }
)
@click.version_option(package_name="oban")
def main() -> None:
    """Oban - Job orchestration framework for Python, backed by PostgreSQL."""
    pass


@main.command()
@click.option(
    "--database-url",
    envvar="OBAN_DATABASE_URL",
    help="PostgreSQL connection string",
)
@click.option(
    "--prefix",
    envvar="OBAN_PREFIX",
    default="public",
    help="PostgreSQL schema name (default: public)",
)
def install(database_url: str | None, prefix: str) -> None:
    """Install the Oban database schema."""

    async def run() -> None:
        logger.info(f"Installing Oban schema in '{prefix}' schema...")

        try:
            async with schema_pool(database_url) as pool:
                await install_schema(pool, prefix=prefix)
            logger.info("Schema installed successfully")
        except Exception as error:
            logger.error(f"Failed to install schema: {error!r}", exc_info=True)
            sys.exit(1)

    asyncio_run(run())


@main.command()
@click.option(
    "--database-url",
    envvar="OBAN_DATABASE_URL",
    help="PostgreSQL connection string",
)
@click.option(
    "--prefix",
    envvar="OBAN_PREFIX",
    default="public",
    help="PostgreSQL schema name (default: public)",
)
def uninstall(database_url: str | None, prefix: str) -> None:
    """Uninstall the Oban database schema."""

    async def run() -> None:
        logger.info(f"Uninstalling Oban schema from '{prefix}' schema...")

        try:
            async with schema_pool(database_url) as pool:
                await uninstall_schema(pool, prefix=prefix)
            logger.info("Schema uninstalled successfully")
        except Exception as e:
            logger.error(f"Failed to uninstall schema: {e}", exc_info=True)
            sys.exit(1)

    asyncio_run(run())


@main.command()
@click.option(
    "--config",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help="Path to TOML configuration file (default: searches for oban.toml)",
)
@click.option(
    "--database-url",
    envvar="OBAN_DATABASE_URL",
    help="PostgreSQL connection string",
)
@click.option(
    "--queues",
    envvar="OBAN_QUEUES",
    help="Comma-separated queue:limit pairs (e.g., 'default:10,mailers:5')",
)
@click.option(
    "--prefix",
    envvar="OBAN_PREFIX",
    help="PostgreSQL schema name (default: public)",
)
@click.option(
    "--node",
    envvar="OBAN_NODE",
    help="Node identifier (default: hostname)",
)
@click.option(
    "--pool-min-size",
    envvar="OBAN_POOL_MIN_SIZE",
    type=int,
    help="Minimum connection pool size (default: 1)",
)
@click.option(
    "--pool-max-size",
    envvar="OBAN_POOL_MAX_SIZE",
    type=int,
    help="Maximum connection pool size (default: 10)",
)
@click.option(
    "--log-level",
    type=click.Choice(
        ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False
    ),
    default="INFO",
    help="Logging level (default: INFO)",
)
def start(log_level: str, config: str | None, **params: Any) -> None:
    """Start the Oban worker process.

    This command starts an Oban instance that processes jobs from the configured queues.
    The process will run until terminated by a signal.

    Signal handling:
    - SIGTERM: Graceful shutdown (finish running jobs, then exit)
    - SIGINT (Ctrl+C): Graceful shutdown on first signal, force exit on second

    Examples:

        # Start with queues
        oban start --database-url postgresql://localhost/mydb --queues default:10,mailers:5

        # Use environment variables
        export OBAN_DATABASE_URL=postgresql://localhost/mydb
        export OBAN_QUEUES=default:10,mailers:5
        oban start
    """
    logging.getLogger().setLevel(getattr(logging, log_level.upper()))

    if config and not Path(config).exists():
        raise click.UsageError(f"--config file '{config}' doesn't exist")

    tml_conf = Config.from_toml(config)
    env_conf = Config.from_env()
    cli_conf = Config.from_cli(params)

    conf = tml_conf.merge(env_conf).merge(cli_conf)

    if not conf.database_url:
        raise click.UsageError(
            "--database-url, OBAN_DATABASE_URL, or database_url in oban.toml required"
        )

    async def run() -> None:
        logger.info("Starting Oban...")

        try:
            pool = await conf.create_pool()
            logger.info("Connected to database")
        except Exception as error:
            logger.error(f"Failed to connect to database: {error!r}")
            sys.exit(1)

        oban = await conf.create_oban(pool)

        telemetry_logger.attach()
        shutdown_event = handle_signals()

        try:
            async with oban:
                logger.info("Oban started, press Ctrl+C to stop")

                await shutdown_event.wait()

                logger.info("Shutting down gracefully...")
        except Exception as error:
            logger.error(f"Error during operation: {error!r}", exc_info=True)
            sys.exit(1)
        finally:
            telemetry_logger.detach()
            await pool.close()
            logger.info("Shutdown complete")

    asyncio_run(run())


if __name__ == "__main__":
    main()
