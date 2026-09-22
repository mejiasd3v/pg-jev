# Operations

## Transaction and cost behavior

Every non-NULL call performs synchronous external work inside the current
statement and transaction. A rollback cannot retract data already sent or refund
provider work. If one row fails in a multi-row statement, PostgreSQL rolls back
the database changes from the statement, but earlier HTTP requests remain
observable by the provider. `EXPLAIN ANALYZE` executes the query and can make
billable calls.

Prefer one multi-question request when several decisions evaluate the same text.
The question map is one provider request. It is not a batch of independent input
rows.

For bounded interactive work, select candidates before calling the function:

```sql
WITH candidates AS MATERIALIZED (
  SELECT id, message
  FROM support_messages
  WHERE evaluation IS NULL
  ORDER BY id
  LIMIT 20
)
SELECT id, prompt_jev(
  message,
  questions => '{
    "refund": {"type": "noul", "instructions": "Does this request a refund?"},
    "team": {
      "type": "choice",
      "instructions": "Which team owns this?",
      "criteria": ["billing", "technical", "sales"]
    }
  }'::jsonb
)
FROM candidates;
```

This remains synchronous. Do not use it for an unbounded backfill or inside a
user-critical transaction that holds locks. Store validated results in an
application table when they will be reused. Use an external worker for backfills,
shared rate limits, strict latency targets, or durable retries.

## Deployment controls

- Install a PostgreSQL build with `plpython3u` and Python's standard HTTPS stack.
- Restrict outbound network access to approved provider or gateway origins.
- Grant the exact six-argument function signature only to roles allowed to
  export supplied text and consume the server credential.
- Set provider allowlists, credentials, endpoint policy, and resource ceilings
  in the PostgreSQL server environment.
- Treat model aliases as mutable. For reproducibility, store the normalized
  question specification, configured model, result, and evaluation timestamp in
  application-owned storage.

A server key shared by several roles does not impose per-role spend quotas.
Backend-local state does not impose cluster-wide concurrency limits. Enforce
shared quotas and concurrency at an approved gateway or worker.

## Observability

Use low-cardinality fields: provider, outcome category, HTTP status, elapsed
milliseconds, attempts, request bytes, response bytes, and the validated request
ID. Never log raw input, credentials, complete request or response bodies,
configured URLs containing credentials, or sensitive question labels. Gateway
metrics and external logs are not transactional database state.

See [configuration.md](configuration.md), [errors.md](errors.md), and
[limits.md](limits.md). The cancellation timings in `limits.md` are one measured
PostgreSQL 18.6 environment, not a platform guarantee.
