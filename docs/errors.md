# Errors

Input validation uses PostgreSQL SQLSTATE `22023`. Extension errors use:

| SQLSTATE | Category |
| --- | --- |
| `JV001` | Invalid or disallowed configuration |
| `JV002` | Transport time budget exhausted |
| `JV003` | Provider authentication rejected |
| `JV004` | Provider rate limit |
| `JV005` | Provider or transport unavailable |
| `JV006` | Invalid, incomplete, or oversized provider response |
| `JV007` | Endpoint, redirect, or TLS policy failure |

PostgreSQL query cancellation remains SQLSTATE `57014`. Cancellation is not
retried or converted into an extension error after the blocking transport phase
returns control to PostgreSQL.

Errors contain bounded fields such as provider, category, HTTP status, and a
validated request ID. They do not include credentials, prompts, response bodies,
or complete configured URLs. An HTTP request that fails after its POST was sent
may already have consumed provider work even though the SQL call failed.
