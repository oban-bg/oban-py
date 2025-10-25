UPDATE
  oban_jobs
SET
  state = 'cancelled'::oban_job_state,
  cancelled_at = timezone('UTC', now())
WHERE
  id = ANY(%(ids)s)
  AND state IN ('executing', 'available', 'scheduled', 'retryable')
