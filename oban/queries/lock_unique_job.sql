SELECT pg_try_advisory_xact_lock(hashtextextended(%(key)s, 0)) AS locked
