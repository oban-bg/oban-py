WITH locked_jobs AS (
  SELECT
    id, state
  FROM
    oban_jobs
  WHERE
    id = ANY(%(ids)s)
    AND state IN ('executing', 'available', 'scheduled', 'retryable', 'suspended')
  FOR UPDATE
),
updated_jobs AS (
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
    oj.id, locked_jobs.state AS original_state
)
SELECT
  id, original_state
FROM
  updated_jobs
