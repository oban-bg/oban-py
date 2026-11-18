SELECT pg_notify(%(channel)s, payload)
FROM json_array_elements_text(%(payloads)s::json) AS payload
