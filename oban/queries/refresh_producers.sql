UPDATE oban_producers
SET updated_at = timezone('UTC', now())
WHERE uuid = ANY(%(uuids)s)
RETURNING uuid::text
