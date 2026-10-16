from contextlib import asynccontextmanager

from fastapi import FastAPI

from oban import Oban

from .workers import GenerateReportWorker, SendEmailWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await Oban.create_pool()

    Oban(pool=pool, name="oban")

    yield

    await pool.close()


app = FastAPI(title="Oban + FastAPI Demo", lifespan=lifespan)


@app.post("/send-email")
async def send_email(to: str, subject: str, body: str):
    job = await SendEmailWorker.enqueue({"to": to, "subject": subject, "body": body})

    return {"job_id": job.id}


@app.post("/generate-report")
async def generate_report(report_type: str, user_id: int):
    job = await GenerateReportWorker.enqueue(
        {"report_type": report_type, "user_id": user_id}
    )

    return {"job_id": job.id}
