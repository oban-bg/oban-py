from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace
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
            r"\b(oban_jobs|oban_leaders|oban_job_state)\b", rf"{prefix}.\1", sql
        )
    else:
        return sql


class Query:
    def __init__(self, conn: Any, prefix: str = "public") -> None:
        self._driver = wrap_conn(conn)
        self._prefix = prefix

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

    async def cancel_job(self, job: Job, reason: str) -> None:
        async with self._driver.connection() as conn:
            stmt = load_file("cancel_job.sql", self._prefix)
            args = {"attempt": job.attempt, "id": job.id, "reason": reason}

            await conn.execute(stmt, args)

    async def check_available_queues(self) -> list[str]:
        async with self._driver.connection() as conn:
            stmt = load_file("check_available_queues.sql", self._prefix)
            rows = await conn.execute(stmt, {})
            results = await rows.fetchall()

            return [queue for (queue,) in results]

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
            stmt = load_file("insert_many.sql", self._prefix)
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
                    scheduled_at=row[2],
                    state=row[3],
                )
                for job, row in zip(jobs, rows)
            ]

    async def install(self) -> None:
        async with self._driver.connection() as conn:
            stmt = load_file("install.sql", self._prefix)

            await conn.execute(stmt)

    async def resign_leader(self, name: str, node: str) -> None:
        async with self._driver.connection() as conn:
            stmt = load_file("resign_leader.sql", self._prefix)
            args = {"name": name, "node": node}

            await conn.execute(stmt, args)

    async def snooze_job(self, job: Job, seconds: int) -> None:
        async with self._driver.connection() as conn:
            stmt = load_file("snooze_job.sql", self._prefix)
            args = {"id": job.id, "seconds": seconds}

            await conn.execute(stmt, args)

    async def stage_jobs(self, limit: int) -> None:
        async with self._driver.connection() as conn:
            async with conn.transaction():
                stmt = load_file("stage_jobs.sql", self._prefix)
                args = {"limit": limit}

                await conn.execute(stmt, args)

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
