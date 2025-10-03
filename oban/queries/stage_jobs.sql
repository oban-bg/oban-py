WITH locked_jobs AS (
  SELECT
    id
  FROM
    oban_jobs
  WHERE
    state IN ('scheduled', 'retryable')
    AND scheduled_at <= timezone('UTC', now())
  LIMIT %(limit)s
  FOR UPDATE
)
UPDATE
  oban_jobs
SET
  state = 'available'::oban_job_state
FROM
  locked_jobs
WHERE
  oban_jobs.id = locked_jobs.id
