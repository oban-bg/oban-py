INSERT INTO oban_leaders (name, node, elected_at, expires_at)
VALUES (
  %(name)s,
  %(node)s,
  timezone('UTC', now()),
  timezone('UTC', now()) + interval '%(ttl)s seconds'
)
ON CONFLICT (name) DO UPDATE SET
  expires_at = EXCLUDED.expires_at
WHERE
  oban_leaders.node = EXCLUDED.node
RETURNING node
