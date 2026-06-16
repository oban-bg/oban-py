WITH locked_jobs AS (
  SELECT
    priority, scheduled_at, id
  FROM
  oban_jobs
  WHERE
    state = 'available'
    AND queue = %(queue)s
  ORDER BY
    priority ASC, scheduled_at ASC, id ASC
  LIMIT
    %(demand)s
  FOR UPDATE SKIP LOCKED
)
UPDATE
  oban_jobs oj
SET
  attempt = oj.attempt + 1,
  attempted_at = timezone('UTC', now()),
  attempted_by = %(attempted_by)s,
  state = 'executing'
FROM
  locked_jobs
WHERE
  oj.id = locked_jobs.id
  AND oj.attempt < oj.max_attempts
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
