from collections import defaultdict
from dataclasses import replace
from functools import lru_cache
from importlib.resources import files
from psycopg.rows import class_row

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


@lru_cache(maxsize=None)
def _load_file(path: str) -> str:
    return files("oban.queries").joinpath(path).read_text(encoding="utf-8")


def cancel_job(conn, job: Job, reason: str) -> None:
    stmt = _load_file("cancel_job.sql")
    args = {"attempt": job.attempt, "id": job.id, "reason": reason}

    conn.execute(stmt, args)


def check_available_queues(conn) -> list[str]:
    stmt = _load_file("check_available_queues.sql")
    rows = conn.execute(stmt, {}).fetchall()

    return [queue for (queue,) in rows]


def complete_job(conn, job: Job) -> None:
    stmt = _load_file("complete_job.sql")

    conn.execute(stmt, {"id": job.id})


def error_job(conn, job: Job, error: Exception, seconds: int) -> None:
    stmt = _load_file("error_job.sql")
    args = {
        "attempt": job.attempt,
        "id": job.id,
        "error": repr(error),
        "seconds": seconds,
    }

    conn.execute(stmt, args)


def get_job(conn, job_id: int) -> Job:
    with conn.cursor(row_factory=class_row(Job)) as cur:
        cur.execute("SELECT * FROM oban_jobs WHERE id = %s", (job_id,))

        return cur.fetchone()


def fetch_jobs(conn, demand: int, queue: str, node: str, uuid: str) -> list[Job]:
    stmt = _load_file("fetch_jobs.sql")
    args = {"queue": queue, "demand": demand, "attempted_by": [node, uuid]}

    with conn.cursor(row_factory=class_row(Job)) as cur:
        cur.execute(stmt, args)

        return cur.fetchall()


def insert_jobs(conn, jobs: list[Job]) -> list[Job]:
    stmt = _load_file("insert_many.sql")
    args = defaultdict(list)

    for job in jobs:
        data = job.to_dict()

        for key in INSERTABLE_FIELDS:
            args[key].append(data[key])

    rows = conn.execute(stmt, dict(args)).fetchall()

    return [
        replace(job, id=row[0], inserted_at=row[1], scheduled_at=row[2], state=row[3])
        for job, row in zip(jobs, rows)
    ]


def install(conn) -> None:
    stmt = _load_file("install.sql")

    conn.execute(stmt)


def snooze_job(conn, job: Job, seconds: int) -> None:
    stmt = _load_file("snooze_job.sql")
    args = {"id": job.id, "seconds": seconds}

    conn.execute(stmt, args)


def stage_jobs(conn, limit: int) -> None:
    stmt = _load_file("stage_jobs.sql")
    args = {"limit": limit}

    conn.execute(stmt, args)
