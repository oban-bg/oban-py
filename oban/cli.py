from __future__ import annotations

import asyncio
import importlib
import logging
import os
import signal
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import click
import orjson
from psycopg.types.json import set_json_dumps, set_json_loads
from psycopg_pool import AsyncConnectionPool

from oban import __version__
from oban._config import Config
from oban.schema import (
    install as install_schema,
    uninstall as uninstall_schema,
)
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


def _file_to_module(file_path: str) -> str | None:
    root = Path(os.getcwd())
    path = Path(file_path)

    try:
        rel_path = path.relative_to(root)
    except ValueError:
        return None

    parts = list(rel_path.parts)

    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]

    if parts[-1] == "__init__":
        parts.pop()

    return ".".join(parts)


def _import_cron_modules(module_paths: list[str]) -> int:
    def safe_import(path: str) -> bool:
        try:
            importlib.import_module(path)
            return True
        except Exception as error:
            logger.error(f"Failed to import cron module at {path}: {error}")

            return False

    return sum([safe_import(path) for path in module_paths])


def _import_cron_paths(paths: list[str]) -> list[str]:
    root = Path(os.getcwd())
    grep = ["grep", "-rl", "--include=*.py", r"@worker\|@job"]

    found = []
    for pattern in [str(root / path) for path in paths]:
        result = subprocess.run(
            [*grep, pattern],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            continue

        candidates = [
            line.strip() for line in result.stdout.strip().split("\n") if line.strip()
        ]

        if not candidates:
            continue

        result = subprocess.run(
            ["grep", "-l", r"cron=", *candidates],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode == 0:
            files = [
                line.strip()
                for line in result.stdout.strip().split("\n")
                if line.strip()
            ]
            found.extend(files)

    files = set(str(Path(file).resolve()) for file in found)

    return [mod for file in files if (mod := _file_to_module(file))]


def _split_csv(value: str | None) -> list[str] | None:
    return [item.strip() for item in value.split(",")] if value else None


def _find_and_load_cron_modules(
    cron_modules: list[str] | None = None, cron_paths: list[str] | None = None
) -> None:
    if cron_modules:
        logger.info(f"Importing {len(cron_modules)} cron modules...")

    elif cron_paths:
        logger.info(f"Discovering cron workers in {', '.join(cron_paths)}...")

        cron_modules = _import_cron_paths(cron_paths)
    else:
        logger.info("Auto-discovering cron workers in current directory...")

        cron_modules = _import_cron_paths([os.getcwd()])

    import_count = _import_cron_modules(cron_modules)

    logger.info(
        f"Successfully imported {import_count}/{len(cron_modules)} cron modules"
    )


def print_banner(version: str) -> None:
    banner = f"""

  [38;2;153;183;183m ██████╗  ██████╗    ████╗   ███╗   ██╗
  [38;2;143;175;175m██╔═══██╗ ██╔══██╗  ██╔═██╗  ████╗  ██║
  [38;2;133;167;167m██║   ██║ ██████╔╝ ████████╗ ██╔██╗ ██║
  [38;2;123;159;159m██║   ██║ ██╔══██╗ ██╔═══██║ ██║╚██╗██║
  [38;2;113;151;151m ██████╔╝ ██████╔╝ ██║   ██║ ██║ ╚████║
  [38;2;103;143;143m ╚═════╝  ╚═════╝  ╚═╝   ╚═╝ ╚═╝  ╚═══╝
  [0m
  Job orchestration framework for Python, backed by PostgreSQL

  v{version} | [38;2;100;149;237mhttps://oban.pro[0m
"""
    print(banner)


@asynccontextmanager
async def schema_pool(dsn: str) -> AsyncIterator[AsyncConnectionPool]:
    if not dsn:
        raise click.UsageError("--dsn is required (or set OBAN_DSN)")

    conf = Config(dsn=dsn, pool_min_size=1, pool_max_size=1)
    pool = await conf.create_pool()

    try:
        yield pool
    finally:
        await pool.close()


async def _start_pool(conf: Config) -> AsyncConnectionPool:
    try:
        pool = await conf.create_pool()
        logger.info(
            f"Connected to database (pool: {conf.pool_min_size}-{conf.pool_max_size})"
        )
        return pool
    except Exception as error:
        logger.error(f"Failed to connect to database: {error!r}")
        sys.exit(1)


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
def version() -> None:
    click.echo(f"oban {__version__}")


@main.command()
@click.option(
    "--config",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help="Path to TOML configuration file (default: searches for oban.toml)",
)
@click.option(
    "--dsn",
    envvar="OBAN_DSN",
    help="PostgreSQL connection string",
)
@click.option(
    "--prefix",
    envvar="OBAN_PREFIX",
    help="PostgreSQL schema name (default: public)",
)
def install(config: str | None, dsn: str | None, prefix: str | None) -> None:
    """Install the Oban database schema."""

    async def run() -> None:
        conf = _load_conf(config, {"dsn": dsn, "prefix": prefix})
        schema_prefix = conf.prefix or "public"

        logger.info(f"Installing Oban schema in '{schema_prefix}'...")

        try:
            async with schema_pool(conf.dsn) as pool:
                await install_schema(pool, prefix=schema_prefix)
            logger.info("Schema installed successfully")
        except Exception as error:
            logger.error(f"Failed to install schema: {error!r}", exc_info=True)
            sys.exit(1)

    asyncio_run(run())


@main.command()
@click.option(
    "--config",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help="Path to TOML configuration file (default: searches for oban.toml)",
)
@click.option(
    "--dsn",
    envvar="OBAN_DSN",
    help="PostgreSQL connection string",
)
@click.option(
    "--prefix",
    envvar="OBAN_PREFIX",
    help="PostgreSQL schema name (default: public)",
)
def uninstall(config: str | None, dsn: str | None, prefix: str | None) -> None:
    """Uninstall the Oban database schema."""

    async def run() -> None:
        conf = _load_conf(config, {"dsn": dsn, "prefix": prefix})
        schema_prefix = conf.prefix or "public"

        logger.info(f"Uninstalling Oban schema from '{schema_prefix}' schema...")

        try:
            async with schema_pool(conf.dsn) as pool:
                await uninstall_schema(pool, prefix=schema_prefix)
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
    "--dsn",
    envvar="OBAN_DSN",
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
@click.option(
    "--cron-modules",
    envvar="OBAN_CRON_MODULES",
    help="Comma-separated list of module paths with cron workers (e.g., 'myapp.workers,myapp.jobs')",
)
@click.option(
    "--cron-paths",
    envvar="OBAN_CRON_PATHS",
    help="Comma-separated list of directories to search for cron workers (e.g., 'myapp/workers')",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Validate configuration and load cron modules without starting Oban",
)
def start(
    log_level: str,
    config: str | None,
    cron_modules: str | None,
    cron_paths: str | None,
    dry_run: bool,
    **params: Any,
) -> None:
    """Start the Oban worker process.

    This command starts an Oban instance that processes jobs from the configured queues.
    The process will run until terminated by a signal.

    Signal handling:
    - SIGTERM: Graceful shutdown (finish running jobs, then exit)
    - SIGINT (Ctrl+C): Graceful shutdown on first signal, force exit on second

    Examples:

        # Start with queues
        oban start --dsn postgresql://localhost/mydb --queues default:10,mailers:5

        # Use environment variables
        export OBAN_DSN=postgresql://localhost/mydb
        export OBAN_QUEUES=default:10,mailers:5
        oban start
    """
    logging.getLogger().setLevel(getattr(logging, log_level.upper()))

    set_json_dumps(orjson.dumps)
    set_json_loads(orjson.loads)

    conf = _load_conf(config, params)
    node = conf.node or socket.gethostname()

    async def run() -> None:
        print_banner(__version__)

        logger.info(f"Starting Oban v{__version__} on node {node}...")

        _find_and_load_cron_modules(
            cron_modules=_split_csv(cron_modules),
            cron_paths=_split_csv(cron_paths),
        )

        if dry_run:
            logger.info("Dry run complete-configuration is valid!")
            sys.exit(0)

        pool = await _start_pool(conf)
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


def _load_conf(conf_path: str | None, params: Any) -> Config:
    if conf_path and not Path(conf_path).exists():
        raise click.UsageError(f"--config file '{conf_path}' doesn't exist")

    if queues := params.pop("queues", None):
        params["queues"] = Config._parse_queues(queues)

    conf = Config.load(conf_path, **params)

    if not conf.dsn:
        raise click.UsageError("--dsn, OBAN_DSN, or dsn in oban.toml required")

    return conf


if __name__ == "__main__":
    main()
