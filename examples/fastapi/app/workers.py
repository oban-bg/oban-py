import logging

from oban import worker

logger = logging.getLogger(__name__)


@worker(queue="mailers")
class SendEmailWorker:
    async def process(self, job):
        logger.info(f"Sending email: {job.args}")


@worker(queue="reports", max_attempts=3, tags=["report"])
class GenerateReportWorker:
    async def process(self, job):
        logger.info(f"Generating report: {job.args}")


@worker(queue="default", cron="* * * * *")
class HealthCheckWorker:
    async def process(self, job):
        logger.info("Running scheduled health check")
