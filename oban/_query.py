from dataclasses import replace
from functools import lru_cache
from importlib.resources import files
from psycopg.rows import class_row

from .job import Job


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


def fetch_jobs(conn, demand: int, queue: str) -> list[Job]:
    stmt = _load_file("fetch_jobs.sql")
    atby = ["not-a-real-node", "not-a-real-uuid"]
    args = {"queue": queue, "demand": demand, "attempted_by": atby}

    with conn.cursor(row_factory=class_row(Job)) as cur:
        cur.execute(stmt, args)

        return cur.fetchall()


def insert_job(conn, job: Job) -> Job:
    stmt = _load_file("insert_job.sql")

    id, ins_at, sch_at = conn.execute(stmt, job.to_dict()).fetchone()

    return replace(job, id=id, inserted_at=ins_at, scheduled_at=sch_at)


def snooze_job(conn, job: Job, seconds: int) -> None:
    stmt = _load_file("snooze_job.sql")
    args = {"id": job.id, "seconds": seconds}

    conn.execute(stmt, args)


def stage_jobs(conn, limit: int) -> None:
    stmt = _load_file("stage_jobs.sql")
    args = {"limit": limit}

    conn.execute(stmt, args)
