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
    pruner: dict[str, Any] | None = None
    refresher: dict[str, Any] | None = None
    scheduler: dict[str, Any] | None = None
    stager: dict[str, Any] | None = None

    # Connection pool options
    pool_min_size: int = 1
    pool_max_size: int = 10
    pool_timeout: float = 30.0

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

        Supported environment variables:

        - OBAN_DSN: Database connection string (required)
        - OBAN_QUEUES: Comma-separated queue:limit pairs (e.g., "default:10,mailers:5")
        - OBAN_PREFIX: Schema prefix (default: "public")
        - OBAN_NODE: Node identifier (default: hostname)
        - OBAN_POOL_MIN_SIZE: Minimum connection pool size (default: 1)
        - OBAN_POOL_MAX_SIZE: Maximum connection pool size (default: 10)
        """
        return cls(
            dsn=os.getenv("OBAN_DSN"),
            queues=cls._parse_queues(os.getenv("OBAN_QUEUES", "")),
            node=os.getenv("OBAN_NODE"),
            prefix=os.getenv("OBAN_PREFIX", "public"),
            pool_min_size=int(os.getenv("OBAN_POOL_MIN_SIZE", "1")),
            pool_max_size=int(os.getenv("OBAN_POOL_MAX_SIZE", "10")),
        )

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

        pool = AsyncConnectionPool(
            conninfo=self.dsn,
            min_size=self.pool_min_size,
            max_size=self.pool_max_size,
            timeout=self.pool_timeout,
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
                "node",
                "pruner",
                "refresher",
                "scheduler",
                "stager",
            ]
            if getattr(self, key) is not None
        }

        return Oban(**params, **extras)
