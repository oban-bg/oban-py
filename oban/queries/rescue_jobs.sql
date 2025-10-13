UPDATE
  oban_jobs
SET
  state = CASE
    WHEN attempt >= max_attempts THEN 'discarded'::oban_job_state
    ELSE 'available'::oban_job_state
  END,
  discarded_at = CASE
    WHEN attempt >= max_attempts THEN timezone('UTC', now())
    ELSE discarded_at
  END,
  meta = CASE
    WHEN attempt >= max_attempts THEN meta
    ELSE meta || jsonb_build_object('rescued', coalesce((meta->>'rescued')::int, 0) + 1)
  END
WHERE
  state = 'executing'
  AND attempted_by[2] NOT IN (SELECT uuid::text FROM oban_producers)
