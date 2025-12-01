from __future__ import annotations

import re
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from functools import cache
from importlib.resources import files
from typing import Any

from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from ._executor import AckAction
from .job import Job, TIMESTAMP_FIELDS

ACKABLE_FIELDS = [
    "id",
    "state",
    "attempt_change",
    "schedule_in",
    "error",
    "meta",
]


INSERTABLE_FIELDS = [
    "args",
    "inserted_at",
    "max_attempts",
    "meta",
    "priority",
    "queue",
    "scheduled_at",
    "state",
    "tags",
    "worker",
]

# The `Job` class has errors, but we only insert a single `error` at one time.
JSON_FIELDS = ["args", "error", "errors", "meta", "tags"]


UPDATABLE_FIELDS = [
    "args",
    "max_attempts",
    "meta",
    "priority",
    "queue",
    "scheduled_at",
    "tags",
    "worker",
]


class Query:
    @staticmethod
    @cache
    def _load_file(
        path: str,
        prefix: str = "public",
        package: str = "oban.queries",
        apply_prefix: bool = True,
    ) -> str:
        sql = files(package).joinpath(path).read_text(encoding="utf-8")

        if apply_prefix:
            return re.sub(
                r"\b(oban_jobs|oban_leaders|oban_producers|oban_job_state|oban_state_to_bit)\b",
                rf"{prefix}.\1",
                sql,
            )
        else:
            return sql

    @staticmethod
    def _cast_type(field: str, value: Any) -> Any:
        if field in JSON_FIELDS and value is not None:
            return Jsonb(value)

        # Ensure timestamps are written as UTC rather than being implicitly cast to the current
        # timezone. The database uses `TIMESTAMP WITHOUT TIME ZONE` and the value is automatically
        # shifted when the zone is present.
        if field in TIMESTAMP_FIELDS and value is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)

        return value

    def __init__(self, pool: AsyncConnectionPool, prefix: str = "public") -> None:
        if not isinstance(pool, AsyncConnectionPool):
            raise TypeError(f"Expected AsyncConnectionPool, got {type(pool).__name__}")

        self._pool = pool
        self._prefix = prefix

    @property
    def dsn(self) -> str:
        return self._pool.conninfo

    @asynccontextmanager
    async def connection(self):
        async with self._pool.connection() as conn:
            yield conn

    # Jobs

    async def ack_jobs(self, acks: list[AckAction]) -> list[int]:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                stmt = self._load_file("ack_jobs.sql", self._prefix)
                args = {}

                for field in ACKABLE_FIELDS:
                    values = [
                        self._cast_type(field, getattr(ack, field)) for ack in acks
                    ]

                    args[field] = values

                result = await conn.execute(stmt, args)
                rows = await result.fetchall()

                return [row[0] for row in rows]

    async def all_jobs(self, states: list[str]) -> list[Job]:
        async with self._pool.connection() as conn:
            stmt = self._load_file("all_jobs.sql", self._prefix)

            async with conn.cursor(row_factory=class_row(Job)) as cur:
                await cur.execute(stmt, {"states": states})
                return await cur.fetchall()

    async def cancel_many_jobs(self, ids: list[int]) -> tuple[int, list[int]]:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                stmt = self._load_file("cancel_many_jobs.sql", self._prefix)
                args = {"ids": ids}

                result = await conn.execute(stmt, args)
                rows = await result.fetchall()

                executing_ids = [row[0] for row in rows if row[1] == "executing"]

                return len(rows), executing_ids

    async def delete_many_jobs(self, ids: list[int]) -> int:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                stmt = self._load_file("delete_many_jobs.sql", self._prefix)
                args = {"ids": ids}

                result = await conn.execute(stmt, args)

                return result.rowcount

    async def get_job(self, job_id: int) -> Job:
        async with self._pool.connection() as conn:
            stmt = self._load_file("get_job.sql", self._prefix)

            async with conn.cursor(row_factory=class_row(Job)) as cur:
                await cur.execute(stmt, (job_id,))

                return await cur.fetchone()

    async def fetch_jobs(
        self, demand: int, queue: str, node: str, uuid: str
    ) -> list[Job]:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                stmt = self._load_file("fetch_jobs.sql", self._prefix)
                args = {"queue": queue, "demand": demand, "attempted_by": [node, uuid]}

                async with conn.cursor(row_factory=class_row(Job)) as cur:
                    await cur.execute(stmt, args)

                    return await cur.fetchall()

    async def insert_jobs(self, jobs: list[Job]) -> list[Job]:
        async with self._pool.connection() as conn:
            stmt = self._load_file("insert_jobs.sql", self._prefix)
            args = defaultdict(list)
            seen = set([])
            dupes = []

            for job in jobs:
                uniq_key = job.meta.get("uniq_key", None)

                if uniq_key and uniq_key in seen:
                    dupes.append(replace(job, conflicted=True))

                    continue
                elif uniq_key:
                    seen.add(uniq_key)

                for key in INSERTABLE_FIELDS:
                    args[key].append(self._cast_type(key, getattr(job, key)))

            result = await conn.execute(stmt, args)
            rows = await result.fetchall()

            inserted = [
                replace(
                    job,
                    id=row[0],
                    inserted_at=row[1],
                    queue=row[2],
                    scheduled_at=row[3],
                    state=row[4],
                    conflicted=row[5],
                )
                for job, row in zip(jobs, rows)
            ]

            return inserted + dupes

    async def prune_jobs(self, max_age: int, limit: int) -> int:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                stmt = self._load_file("prune_jobs.sql", self._prefix)
                args = {"max_age": max_age, "limit": limit}

                result = await conn.execute(stmt, args)

                return result.rowcount

    async def rescue_jobs(self) -> int:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                stmt = self._load_file("rescue_jobs.sql", self._prefix)

                result = await conn.execute(stmt)

                return result.rowcount

    async def retry_many_jobs(self, ids: list[int]) -> int:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                stmt = self._load_file("retry_many_jobs.sql", self._prefix)
                args = {"ids": ids}

                result = await conn.execute(stmt, args)

                return result.rowcount

    async def stage_jobs(
        self, limit: int, queues: list[str], before: datetime | None = None
    ) -> tuple[int, list[str]]:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                stmt = self._load_file("stage_jobs.sql", self._prefix)
                args = {"limit": limit, "queues": queues, "before": before}

                result = await conn.execute(stmt, args)
                rows = await result.fetchall()
                queues = [queue for (queue,) in rows]

                return (len(rows), queues)

    async def update_many_jobs(self, jobs: list[Job]) -> list[Job]:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                stmt = self._load_file("update_job.sql", self._prefix)
                args = defaultdict(list)

                for job in jobs:
                    args["ids"].append(job.id)

                    for key in UPDATABLE_FIELDS:
                        args[key].append(self._cast_type(key, getattr(job, key)))

                result = await conn.execute(stmt, dict(args))
                rows = await result.fetchall()

                return [
                    replace(
                        job,
                        args=row[0],
                        max_attempts=row[1],
                        meta=row[2],
                        priority=row[3],
                        queue=row[4],
                        scheduled_at=row[5],
                        state=row[6],
                        tags=row[7],
                        worker=row[8],
                    )
                    for job, row in zip(jobs, rows)
                ]

    # Leadership

    async def attempt_leadership(
        self, name: str, node: str, ttl: int, is_leader: bool
    ) -> bool:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cleanup_stmt = self._load_file(
                    "cleanup_expired_leaders.sql", self._prefix
                )

                await conn.execute(cleanup_stmt)

                if is_leader:
                    elect_stmt = self._load_file("reelect_leader.sql", self._prefix)
                else:
                    elect_stmt = self._load_file("elect_leader.sql", self._prefix)

                args = {"name": name, "node": node, "ttl": ttl}
                result = await conn.execute(elect_stmt, args)
                rows = await result.fetchone()

                return rows is not None and rows[0] == node

    async def resign_leader(self, name: str, node: str) -> None:
        async with self._pool.connection() as conn:
            stmt = self._load_file("resign_leader.sql", self._prefix)
            args = {"name": name, "node": node}

            await conn.execute(stmt, args)

    # Schema

    async def install(self) -> None:
        async with self._pool.connection() as conn:
            stmt = self._load_file("install.sql", self._prefix)

            await conn.execute(stmt)

    async def reset(self) -> None:
        async with self._pool.connection() as conn:
            stmt = self._load_file("reset.sql", self._prefix)

            await conn.execute(stmt)

    async def uninstall(self) -> None:
        async with self._pool.connection() as conn:
            stmt = self._load_file("uninstall.sql", self._prefix)

            await conn.execute(stmt)

    async def verify_structure(self) -> list[str]:
        async with self._pool.connection() as conn:
            stmt = self._load_file("verify_structure.sql", apply_prefix=False)
            args = {"prefix": self._prefix}
            rows = await conn.execute(stmt, args)
            results = await rows.fetchall()

            return [table for (table,) in results]

    # Producer

    async def cleanup_expired_producers(self, max_age: float) -> int:
        async with self._pool.connection() as conn:
            stmt = self._load_file("cleanup_expired_producers.sql", self._prefix)
            args = {"max_age": max_age}

            result = await conn.execute(stmt, args)

            return result.rowcount

    async def delete_producer(self, uuid: str) -> None:
        async with self._pool.connection() as conn:
            stmt = self._load_file("delete_producer.sql", self._prefix)
            args = {"uuid": uuid}

            await conn.execute(stmt, args)

    async def insert_producer(
        self, uuid: str, name: str, node: str, queue: str, meta: dict[str, Any]
    ) -> None:
        async with self._pool.connection() as conn:
            stmt = self._load_file("insert_producer.sql", self._prefix)
            args = {
                "uuid": uuid,
                "name": name,
                "node": node,
                "queue": queue,
                "meta": Jsonb(meta),
            }

            await conn.execute(stmt, args)

    async def refresh_producers(self, uuids: list[str]) -> int:
        async with self._pool.connection() as conn:
            stmt = self._load_file("refresh_producers.sql", self._prefix)
            args = {"uuids": uuids}

            result = await conn.execute(stmt, args)

            return result.rowcount

    async def update_producer(self, uuid: str, meta: dict[str, Any]) -> None:
        async with self._pool.connection() as conn:
            stmt = self._load_file("update_producer.sql", self._prefix)
            args = {"uuid": uuid, "meta": Jsonb(meta)}

            await conn.execute(stmt, args)

    # Notifier

    async def notify(self, channel: str, payloads: list[str]) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "SELECT pg_notify(%s, payload) FROM json_array_elements_text(%s::json) AS payload",
                (channel, Jsonb(payloads)),
            )
