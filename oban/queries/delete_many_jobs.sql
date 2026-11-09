DELETE FROM
  oban_jobs
WHERE
  id = ANY(%(ids)s)
  AND state != 'executing'
RETURNING
  id,
  state,
  queue,
  worker,
  attempt,
  max_attempts,
  priority,
  args,
  meta,
  errors,
  tags,
  attempted_by,
  inserted_at,
  attempted_at,
  cancelled_at,
  completed_at,
  discarded_at,
  scheduled_at
