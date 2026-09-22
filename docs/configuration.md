# Configuration

Server environment values take precedence over session settings. Session
settings are convenient configuration, not confidential storage. Do not put API
keys in SQL text, role defaults, or `ALTER SYSTEM`.

| Provider | Select | Credential | Model |
| --- | --- | --- | --- |
| TypeSafe | default or `PROMPT_JEV_PROVIDER=typesafe` | `TYPESAFE_API_KEY` | `TYPESAFE_DEFAULT_MODEL`, default `jev-latest` |
| Vercel | `PROMPT_JEV_PROVIDER=vercel` | `AI_GATEWAY_API_KEY` | `VERCEL_JEV_MODEL`, default `typesafe-ai/jev` |
| Cloudflare | `PROMPT_JEV_PROVIDER=cloudflare` | `CLOUDFLARE_API_TOKEN` plus `CLOUDFLARE_ACCOUNT_ID` | `CLOUDFLARE_JEV_MODEL`, default `typesafe/jev` |

For backward compatibility, Vercel uses `TYPESAFE_DEFAULT_MODEL` when
`VERCEL_JEV_MODEL` is absent. Provider-specific values should be preferred.
The equivalent session model settings are `jev.typesafe_model`,
`jev.vercel_model`, and `jev.cloudflare_model`. `jev.api_key` is the session
credential fallback.

Administrators can restrict provider selection with
`PROMPT_JEV_ALLOWED_PROVIDERS`. Custom TypeSafe and Vercel base URLs are server
environment settings. See [SECURITY.md](../SECURITY.md) for endpoint policy and
[limits.md](limits.md) for resource ceilings.

## Retries

Scalar calls make one attempt by default. An administrator may set
`PROMPT_JEV_MAX_RETRIES=1`; a caller can then opt into one retry with
`jev.retries=1`. Only HTTP 429, 502, 503, 504, and 529 are retryable. Authentication,
invalid input, invalid output, TLS errors, cancellation, timeouts, and ambiguous
connection failures are not retried.

`Retry-After` is capped by `PROMPT_JEV_MAX_RETRY_AFTER_SECONDS`, default one
second, and by the remaining total time budget. Retrying a POST can duplicate
provider work or charges. The extension does not promise exactly-once execution
and never fails over to another provider.

## Connection lifetime

A validated direct HTTP/1.1 connection is reused only within the current
PostgreSQL backend and matching role, endpoint, credential identity, provider,
model, and TLS context. `PROMPT_JEV_MAX_CONNECTION_AGE_SECONDS` sets the
administrator ceiling, default 60 seconds; `jev.connection_age` may reduce it.
Proxy-routed calls use the standard URL transport without backend caching.
Malformed, incomplete, timed out, cancelled, redirected, or server-closed
responses discard the connection. Results and credentials are not cached.
