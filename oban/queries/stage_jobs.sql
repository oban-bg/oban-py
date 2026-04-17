WITH locked_jobs AS (
  SELECT
    id
  FROM
    oban_jobs
  WHERE
    state = ANY('{scheduled,retryable}')
    AND queue = ANY(%(queues)s::text[])
    AND scheduled_at <= coalesce(%(before)s, timezone('UTC', now()))
  ORDER BY
    scheduled_at ASC, id ASC
  LIMIT
    %(limit)s
  FOR UPDATE SKIP LOCKED
),
updated_jobs AS (
  UPDATE
    oban_jobs
  SET
    state = 'available'::oban_job_state
  FROM
    locked_jobs
  WHERE
    oban_jobs.id = locked_jobs.id
)
SELECT DISTINCT
  q.queue
FROM
  unnest(%(queues)s::text[]) AS q(queue)
WHERE
  EXISTS (
    SELECT 1
    FROM oban_jobs
    WHERE state = 'available' AND queue = q.queue
  )
