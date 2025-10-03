SELECT
  DISTINCT queue
FROM
  oban_jobs
WHERE
  state = 'available'
