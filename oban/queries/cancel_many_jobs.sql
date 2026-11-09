WITH locked_jobs AS (
  SELECT
    id
  FROM
    oban_jobs
  WHERE
    id = ANY(%(ids)s)
    AND state IN ('executing', 'available', 'scheduled', 'retryable', 'suspended')
  FOR UPDATE
)
UPDATE
  oban_jobs oj
SET
  state = CASE
    WHEN oj.state = 'executing' THEN oj.state
    ELSE 'cancelled'::oban_job_state
  END,
  cancelled_at = CASE
    WHEN oj.state = 'executing' THEN oj.cancelled_at
    ELSE timezone('UTC', now())
  END,
  meta = CASE
    WHEN oj.state = 'executing' THEN oj.meta
    ELSE oj.meta || jsonb_build_object('cancel_attempted_at', timezone('UTC', now()))
  END
FROM
  locked_jobs
WHERE
  oj.id = locked_jobs.id
RETURNING
  oj.id,
  oj.state,
  oj.queue,
  oj.worker,
  oj.attempt,
  oj.max_attempts,
  oj.priority,
  oj.args,
  oj.meta,
  oj.errors,
  oj.tags,
  oj.attempted_by,
  oj.inserted_at,
  oj.attempted_at,
  oj.cancelled_at,
  oj.completed_at,
  oj.discarded_at,
  oj.scheduled_at
