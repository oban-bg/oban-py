SELECT id
FROM oban_jobs
WHERE state = ANY(%(states)s::oban_job_state[]){conditions}
LIMIT 1
