from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from functools import cache
from importlib.resources import files
from typing import Any

from psycopg.rows import class_row

from ._driver import wrap_conn
from .job import Job


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


@cache
def load_file(path: str, prefix: str = "public", apply_prefix: bool = True) -> str:
    sql = files("oban.queries").joinpath(path).read_text(encoding="utf-8")

    if apply_prefix:
        return re.sub(
            r"\b(oban_jobs|oban_leaders|oban_producers|oban_job_state)\b",
            rf"{prefix}.\1",
            sql,
        )
    else:
        return sql


class Query:
    def __init__(self, conn: Any, prefix: str = "public") -> None:
        self._driver = wrap_conn(conn)
        self._prefix = prefix

    # Jobs

    async def all_jobs(self, states: list[str]) -> list[Job]:
        async with self._driver.connection() as conn:
            stmt = load_file("all_jobs.sql", self._prefix)

            async with conn.cursor(row_factory=class_row(Job)) as cur:
                await cur.execute(stmt, {"states": states})
                return await cur.fetchall()

    async def cancel_job(self, job: Job, reason: str) -> None:
        async with self._driver.connection() as conn:
            stmt = load_file("cancel_job.sql", self._prefix)
            args = {"attempt": job.attempt, "id": job.id, "reason": reason}

            await conn.execute(stmt, args)

    async def complete_job(self, job: Job) -> None:
        async with self._driver.connection() as conn:
            stmt = load_file("complete_job.sql", self._prefix)

            await conn.execute(stmt, {"id": job.id})

    async def error_job(self, job: Job, error: Exception, seconds: int) -> None:
        async with self._driver.connection() as conn:
            stmt = load_file("error_job.sql", self._prefix)
            args = {
                "attempt": job.attempt,
                "id": job.id,
                "error": repr(error),
                "seconds": seconds,
            }

            await conn.execute(stmt, args)

    async def get_job(self, job_id: int) -> Job:
        async with self._driver.connection() as conn:
            stmt = load_file("get_job.sql", self._prefix)

            async with conn.cursor(row_factory=class_row(Job)) as cur:
                await cur.execute(stmt, (job_id,))

                return await cur.fetchone()

    async def fetch_jobs(
        self, demand: int, queue: str, node: str, uuid: str
    ) -> list[Job]:
        async with self._driver.connection() as conn:
            async with conn.transaction():
                stmt = load_file("fetch_jobs.sql", self._prefix)
                args = {"queue": queue, "demand": demand, "attempted_by": [node, uuid]}

                async with conn.cursor(row_factory=class_row(Job)) as cur:
                    await cur.execute(stmt, args)

                    return await cur.fetchall()

    async def insert_jobs(self, jobs: list[Job]) -> list[Job]:
        async with self._driver.connection() as conn:
            stmt = load_file("insert_jobs.sql", self._prefix)
            args = defaultdict(list)

            for job in jobs:
                data = job.to_dict()
                for key in INSERTABLE_FIELDS:
                    args[key].append(data[key])

            result = await conn.execute(stmt, dict(args))
            rows = await result.fetchall()

            return [
                replace(
                    job,
                    id=row[0],
                    inserted_at=row[1],
                    queue=row[2],
                    scheduled_at=row[3],
                    state=row[4],
                )
                for job, row in zip(jobs, rows)
            ]

    async def prune_jobs(self, max_age: int, limit: int) -> int:
        async with self._driver.connection() as conn:
            async with conn.transaction():
                stmt = load_file("prune_jobs.sql", self._prefix)
                args = {"max_age": max_age, "limit": limit}

                result = await conn.execute(stmt, args)

                return result.rowcount

    async def rescue_jobs(self) -> int:
        async with self._driver.connection() as conn:
            async with conn.transaction():
                stmt = load_file("rescue_jobs.sql", self._prefix)

                result = await conn.execute(stmt)

                return result.rowcount

    async def retry_all_jobs(self, ids: list[int]) -> int:
        async with self._driver.connection() as conn:
            async with conn.transaction():
                stmt = load_file("retry_all_jobs.sql", self._prefix)
                args = {"ids": ids}

                result = await conn.execute(stmt, args)

                return result.rowcount

    async def delete_all_jobs(self, ids: list[int]) -> int:
        async with self._driver.connection() as conn:
            async with conn.transaction():
                stmt = load_file("delete_all_jobs.sql", self._prefix)
                args = {"ids": ids}

                result = await conn.execute(stmt, args)

                return result.rowcount

    async def cancel_all_jobs(self, ids: list[int]) -> int:
        async with self._driver.connection() as conn:
            async with conn.transaction():
                stmt = load_file("cancel_all_jobs.sql", self._prefix)
                args = {"ids": ids}

                result = await conn.execute(stmt, args)

                return result.rowcount

    async def snooze_job(self, job: Job, seconds: int) -> None:
        async with self._driver.connection() as conn:
            stmt = load_file("snooze_job.sql", self._prefix)
            args = {"id": job.id, "seconds": seconds}

            await conn.execute(stmt, args)

    async def stage_jobs(
        self, limit: int, queues: list[str], before: datetime | None = None
    ) -> list[str]:
        async with self._driver.connection() as conn:
            async with conn.transaction():
                stmt = load_file("stage_jobs.sql", self._prefix)
                args = {"limit": limit, "queues": queues, "before": before}

                result = await conn.execute(stmt, args)
                rows = await result.fetchall()

                return [queue for (queue,) in rows]

    # Leadership

    async def attempt_leadership(
        self, name: str, node: str, ttl: int, is_leader: bool
    ) -> bool:
        async with self._driver.connection() as conn:
            async with conn.transaction():
                cleanup_stmt = load_file("cleanup_expired_leaders.sql", self._prefix)

                await conn.execute(cleanup_stmt)

                if is_leader:
                    elect_stmt = load_file("reelect_leader.sql", self._prefix)
                else:
                    elect_stmt = load_file("elect_leader.sql", self._prefix)

                args = {"name": name, "node": node, "ttl": ttl}
                rows = await conn.execute(elect_stmt, args)
                result = await rows.fetchone()

                return result is not None and result[0] == node

    async def resign_leader(self, name: str, node: str) -> None:
        async with self._driver.connection() as conn:
            stmt = load_file("resign_leader.sql", self._prefix)
            args = {"name": name, "node": node}

            await conn.execute(stmt, args)

    # Schema

    async def install(self) -> None:
        async with self._driver.connection() as conn:
            stmt = load_file("install.sql", self._prefix)

            await conn.execute(stmt)

    async def reset(self) -> None:
        async with self._driver.connection() as conn:
            stmt = load_file("reset.sql", self._prefix)

            await conn.execute(stmt)

    async def uninstall(self) -> None:
        async with self._driver.connection() as conn:
            stmt = load_file("uninstall.sql", self._prefix)

            await conn.execute(stmt)

    async def verify_structure(self) -> list[str]:
        async with self._driver.connection() as conn:
            stmt = load_file("verify_structure.sql", apply_prefix=False)
            args = {"prefix": self._prefix}
            rows = await conn.execute(stmt, args)
            results = await rows.fetchall()

            return [table for (table,) in results]

    # Producer

    async def cleanup_expired_producers(self, max_age: float) -> int:
        async with self._driver.connection() as conn:
            stmt = load_file("cleanup_expired_producers.sql", self._prefix)
            args = {"max_age": max_age}

            result = await conn.execute(stmt, args)

            return result.rowcount

    async def delete_producer(self, uuid: str) -> None:
        async with self._driver.connection() as conn:
            stmt = load_file("delete_producer.sql", self._prefix)
            args = {"uuid": uuid}

            await conn.execute(stmt, args)

    async def insert_producer(
        self, uuid: str, name: str, node: str, queue: str, meta: dict[str, Any]
    ) -> None:
        async with self._driver.connection() as conn:
            stmt = load_file("insert_producer.sql", self._prefix)
            args = {
                "uuid": uuid,
                "name": name,
                "node": node,
                "queue": queue,
                "meta": json.dumps(meta),
            }

            await conn.execute(stmt, args)

    async def refresh_producers(self, uuids: list[str]) -> int:
        async with self._driver.connection() as conn:
            stmt = load_file("refresh_producers.sql", self._prefix)
            args = {"uuids": uuids}

            result = await conn.execute(stmt, args)

            return result.rowcount

    async def update_producer(self, uuid: str, meta: dict[str, Any]) -> None:
        async with self._driver.connection() as conn:
            stmt = load_file("update_producer.sql", self._prefix)
            args = {"uuid": uuid, "meta": json.dumps(meta)}

            await conn.execute(stmt, args)

    # Notifier

    async def notify(self, channel: str, payloads: list[str]) -> None:
        async with self._driver.connection() as conn:
            await conn.execute(
                "SELECT pg_notify(%s, payload) FROM json_array_elements_text(%s::json) AS payload",
                (channel, json.dumps(payloads)),
            )
