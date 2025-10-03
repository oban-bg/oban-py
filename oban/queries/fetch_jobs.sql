WITH locked_jobs AS (
  SELECT *
  FROM oban_jobs
  WHERE state = 'available' AND queue = %(queue)s
  ORDER BY priority ASC, scheduled_at ASC, id ASC
  LIMIT %(demand)s
  FOR UPDATE SKIP LOCKED
)
UPDATE
  oban_jobs
SET
  attempt = oban_jobs.attempt + 1,
  attempted_at = timezone('UTC', now()),
  attempted_by = %(attempted_by)s,
  state = 'executing'
FROM
  locked_jobs
WHERE
  oban_jobs.id = locked_jobs.id
RETURNING
  oban_jobs.*
