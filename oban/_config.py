from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from psycopg_pool import AsyncConnectionPool

from oban import Oban


@dataclass
class Config:
    """Configuration for Oban instances.

    Can be used by both CLI and programmatic usage to create Oban instances
    with consistent configuration.
    """

    dsn: str | None = None
    queues: dict[str, int] = field(default_factory=dict)
    name: str | None = None
    node: str | None = None
    prefix: str | None = None
    leadership: bool | None = None

    # Core loop configurations
    lifeline: dict[str, Any] | None = None
    metrics: dict[str, Any] | bool | None = None
    pruner: dict[str, Any] | None = None
    refresher: dict[str, Any] | None = None
    scheduler: dict[str, Any] | None = None
    stager: dict[str, Any] | None = None

    pool_min_size: int | None = None
    pool_max_size: int | None = None
    pool_timeout: float | None = None
    pool_max_lifetime: float | None = None
    pool_max_idle: float | None = None
    pool_check: bool | None = None

    @staticmethod
    def _parse_queues(input: str) -> dict[str, int]:
        if not input:
            return {}

        return {
            name.strip(): int(limit.strip())
            for line in input.split(",")
            if line.strip() and ":" in line
            for name, limit in [line.split(":", 1)]
        }

    @classmethod
    def from_env(cls) -> Config:
        """Load configuration from environment variables.

        Only fields with a corresponding env var actually set are populated;
        unset fields stay at their default so `merge()` can defer to other
        sources (e.g. a TOML file).

        Supported environment variables:

        - OBAN_DSN: Database connection string (required)
        - OBAN_QUEUES: Comma-separated queue:limit pairs (e.g., "default:10,mailers:5")
        - OBAN_PREFIX: Schema prefix
        - OBAN_NODE: Node identifier
        - OBAN_POOL_MIN_SIZE: Minimum connection pool size
        - OBAN_POOL_MAX_SIZE: Maximum connection pool size
        - OBAN_POOL_TIMEOUT: Seconds to wait for a connection from the pool
        - OBAN_POOL_MAX_LIFETIME: Max seconds before recycling a connection
        - OBAN_POOL_MAX_IDLE: Max seconds a connection can sit idle
        - OBAN_POOL_CHECK: Validate connections before use
        """
        params: dict[str, Any] = {}

        if (value := os.getenv("OBAN_DSN")) is not None:
            params["dsn"] = value
        if (value := os.getenv("OBAN_QUEUES")) is not None:
            params["queues"] = cls._parse_queues(value)
        if (value := os.getenv("OBAN_NODE")) is not None:
            params["node"] = value
        if (value := os.getenv("OBAN_PREFIX")) is not None:
            params["prefix"] = value
        if (value := os.getenv("OBAN_POOL_MIN_SIZE")) is not None:
            params["pool_min_size"] = int(value)
        if (value := os.getenv("OBAN_POOL_MAX_SIZE")) is not None:
            params["pool_max_size"] = int(value)
        if (value := os.getenv("OBAN_POOL_TIMEOUT")) is not None:
            params["pool_timeout"] = float(value)
        if (value := os.getenv("OBAN_POOL_MAX_LIFETIME")) is not None:
            params["pool_max_lifetime"] = float(value)
        if (value := os.getenv("OBAN_POOL_MAX_IDLE")) is not None:
            params["pool_max_idle"] = float(value)
        if (value := os.getenv("OBAN_POOL_CHECK")) is not None:
            params["pool_check"] = value.lower() == "true"

        return cls(**params)

    @classmethod
    def from_cli(cls, params: dict[str, Any]) -> Config:
        if queues := params.pop("queues", None):
            params["queues"] = cls._parse_queues(queues)

        return cls(**params)

    @classmethod
    def from_toml(cls, path: str | None = None) -> Config:
        params = {}
        path_obj = Path(path or "oban.toml")

        if path_obj.exists():
            with open(path_obj, "rb") as file:
                params = tomllib.load(file)

        return cls(**params)

    @classmethod
    def load(cls, path: str | None = None, **overrides: Any) -> Config:
        tml_conf = cls.from_toml(path)
        env_conf = cls.from_env()
        cli_conf = cls(**overrides)

        return tml_conf.merge(env_conf).merge(cli_conf)

    def merge(self, other: Config) -> Config:
        def merge_dicts(this, that) -> dict | None:
            if that is None or this is None:
                return this

            merged = this.copy()
            merged.update(that)

            return merged

        merged = {}

        for field_ref in fields(self):
            name = field_ref.name
            this_val = getattr(self, name)
            that_val = getattr(other, name)

            if isinstance(that_val, dict):
                merged[name] = merge_dicts(this_val, that_val)
            elif that_val is not None:
                merged[name] = that_val
            else:
                merged[name] = this_val

        return Config(**merged)

    async def create_pool(self) -> AsyncConnectionPool:
        if not self.dsn:
            raise ValueError("dsn is required to create a connection pool")

        min_size = self.pool_min_size if self.pool_min_size is not None else 1
        max_size = self.pool_max_size if self.pool_max_size is not None else 10
        timeout = self.pool_timeout if self.pool_timeout is not None else 30.0
        max_lifetime = (
            self.pool_max_lifetime if self.pool_max_lifetime is not None else 3600.0
        )
        max_idle = self.pool_max_idle if self.pool_max_idle is not None else 600.0
        check = AsyncConnectionPool.check_connection if self.pool_check else None

        pool = AsyncConnectionPool(
            conninfo=self.dsn,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout,
            max_lifetime=max_lifetime,
            max_idle=max_idle,
            check=check,
            open=False,
        )

        await pool.open()
        await pool.wait()

        return pool

    async def create_oban(
        self, pool: AsyncConnectionPool | None = None, dispatcher: Any = None
    ) -> Oban:
        pool = pool or await self.create_pool()

        params: dict[str, Any] = {
            "pool": pool,
            "dispatcher": dispatcher,
            "name": self.name,
            "prefix": self.prefix,
            "queues": self.queues,
        }

        extras = {
            key: getattr(self, key)
            for key in [
                "leadership",
                "lifeline",
                "metrics",
                "node",
                "pruner",
                "refresher",
                "scheduler",
                "stager",
            ]
            if getattr(self, key) is not None
        }

        return Oban(**params, **extras)
