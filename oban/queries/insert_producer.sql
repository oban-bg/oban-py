INSERT INTO oban_producers (uuid, name, node, queue, meta)
VALUES (%(uuid)s, %(name)s, %(node)s, %(queue)s, %(meta)s)
ON CONFLICT (uuid) DO NOTHING
