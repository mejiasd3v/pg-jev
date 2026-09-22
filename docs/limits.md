# Resource limits and timeouts

The defaults below are extension policy, not provider limits.

| Resource | Default administrator ceiling | Caller reduction |
| --- | ---: | --- |
| Input UTF-8 bytes | 256 KiB | `jev.input_bytes` |
| Serialized request bytes | 1 MiB | `jev.request_bytes` |
| Response bytes read | 1 MiB | `jev.response_bytes` |
| Questions per call | 64 | `jev.question_limit` |
| JSON nesting | 64 levels | `jev.json_depth` |
| Connection phase | 2 seconds | `jev.connect_timeout` |
| Read idle | 3 seconds | `jev.read_timeout` |
| Intended total call time | 10 seconds | `jev.total_timeout` |

Set an administrator ceiling in the PostgreSQL server environment with the
corresponding `PROMPT_JEV_MAX_*` variable, such as
`PROMPT_JEV_MAX_RESPONSE_BYTES`. A session setting may reduce a ceiling but
cannot raise it. Values must be finite and positive.

Responses are read incrementally. The extension rejects an oversized declared
length, keeps counting when no length is declared, rejects content encodings,
and discards incomplete or invalid responses. Requests and responses over the
JSON nesting limit are rejected.

## Timeout boundary

The total timer uses a monotonic clock and is checked before connection work and
between incremental response reads. The smaller connection, read, or remaining
time budget is passed to the standard library transport. This stops slow headers,
idle reads, and slow response chunks in the covered tests.

This is an intended elapsed-time policy, not a proven hard deadline. Python's
standard URL transport and operating-system DNS, TCP, TLS, and signal behavior
can block between checks. Deployments with a strict latency or cancellation SLO
should use an external worker or a separately evaluated PostgreSQL-aware
transport.

A local PostgreSQL 18.6 Docker experiment stalled response headers while the
connection-phase budget was 2 seconds. A 200 ms `statement_timeout` surfaced as
SQLSTATE `57014` after 2.060 seconds. `pg_cancel_backend` also surfaced as
`57014` after 2.000 seconds. The same backend completed a subsequent query in
both cases. These measurements show that cancellation is preserved, but not
promptly observed during that blocking phase. They are test observations, not a
cross-platform cancellation guarantee.
