from dataclasses import replace
from functools import lru_cache
from importlib.resources import files
from psycopg.rows import class_row

from .job import Job


@lru_cache(maxsize=None)
def _load_file(path: str) -> str:
    return files("oban.queries").joinpath(path).read_text(encoding="utf-8")


def insert_job(conn, job: Job) -> Job:
    stmt = _load_file("insert_job.sql")
    
    id, ins_at, sch_at = conn.execute(stmt, job.to_dict()).fetchone()

    return replace(job, id=id, inserted_at=ins_at, scheduled_at=sch_at)


def fetch_jobs(conn, demand: int, queue: str) -> list[Job]:
    stmt = _load_file("fetch_jobs.sql")
    atby = ["not-a-real-node", "not-a-real-uuid"]
    args = {"queue": queue, "demand": demand, "attempted_by": atby}

    with conn.cursor(row_factory=class_row(Job)) as cur:
        cur.execute(stmt, args)

        return cur.fetchall()
