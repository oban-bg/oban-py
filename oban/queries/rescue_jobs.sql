UPDATE
  oban_jobs
SET
  state = 'available'::oban_job_state,
  meta = meta || jsonb_build_object('rescued', coalesce((meta->>'rescued')::int, 0) + 1)
WHERE
  state = 'executing'
  AND attempted_by[2] NOT IN (SELECT uuid::text FROM oban_producers)
