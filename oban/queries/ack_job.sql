UPDATE oban_jobs
SET state = %(state)s::oban_job_state,
    cancelled_at = CASE WHEN %(state)s::oban_job_state = 'cancelled' THEN timezone('UTC', now()) ELSE cancelled_at END,
    completed_at = CASE WHEN %(state)s::oban_job_state = 'completed' THEN timezone('UTC', now()) ELSE completed_at END,
    discarded_at = CASE WHEN %(state)s::oban_job_state = 'discarded' THEN timezone('UTC', now()) ELSE discarded_at END,
    scheduled_at = CASE WHEN %(schedule_in)s::int IS NULL THEN scheduled_at ELSE timezone('UTC', now()) + make_interval(secs => %(schedule_in)s::int) END,
    attempt = COALESCE(%(attempt_change)s::int + attempt, attempt),
    errors = CASE WHEN %(error)s::jsonb IS NULL THEN errors ELSE errors || %(error)s::jsonb END,
    meta = CASE WHEN %(meta)s::jsonb IS NULL THEN meta ELSE meta || %(meta)s::jsonb END
WHERE id = %(id)s
  AND state = 'executing'
RETURNING id;
